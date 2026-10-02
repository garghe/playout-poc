"""Timeline projection, countdowns and pre-air checks."""
from __future__ import annotations

from dataclasses import dataclass

from . import media
from .playlist import Event, fmt_tc


def planned_duration(e: Event) -> float | None:
    """How long the event will run: explicit duration, else media length minus in point."""
    if e.duration is not None:
        return e.duration
    if e.media_duration is not None:
        return max(0.0, e.media_duration - e.in_point)
    return None


@dataclass
class Slot:
    start: float | None
    end: float | None
    gap_before: float = 0.0      # filler needed before a hard start
    truncated_by: float = 0.0    # seconds cut off by the next hard start


def project(events: list[Event], first: int, first_start: float, now: float) -> list[Slot | None]:
    """Project start/end times from event `first` (which starts at `first_start`) onwards.

    Events before `first` get None. Hard starts win over follow-on timing:
    a late previous item is truncated, an early one leaves a gap (filled with slate).
    """
    slots: list[Slot | None] = [None] * len(events)
    t: float | None = first_start
    for i in range(first, len(events)):
        e = events[i]
        gap = 0.0
        if e.hard_start is not None and i != first:
            start = max(e.hard_start, now)
            if t is not None:
                if start > t:
                    gap = start - t
                elif start < t and slots[i - 1] is not None:
                    prev = slots[i - 1]
                    prev.truncated_by = t - start
                    prev.end = start
        else:
            start = t
        dur = planned_duration(e)
        end = start + dur if (start is not None and dur is not None) else None
        slots[i] = Slot(start=start, end=end, gap_before=gap)
        t = end
    return slots


def next_by(events: list[Event], slots: list[Slot | None], now: float, pred) -> tuple[Event, float] | None:
    for e, s in zip(events, slots):
        if s is not None and s.start is not None and s.start > now and pred(e):
            return e, s.start
    return None


def countdowns(events: list[Event], slots: list[Slot | None], now: float) -> list[dict]:
    """Automatic countdowns: next break plus next event of each category."""
    out = []
    nb = next_by(events, slots, now, lambda e: e.kind == "spot" and e.break_first)
    if nb:
        e, at = nb
        out.append({"key": "break", "label": f"Next break: {e.break_title}", "category": "break",
                    "at": at, "source": "playlist"})
    cats = []
    for e in events:
        if e.category and e.kind != "spot" and e.category not in cats:
            cats.append(e.category)
    for c in cats:
        r = next_by(events, slots, now, lambda e, c=c: e.category == c and e.kind != "spot")
        if r:
            e, at = r
            out.append({"key": f"cat:{c}", "label": f"Next {c}: {e.title}", "category": c,
                        "at": at, "source": "playlist"})
    if any(e.kind == "live" for e in events):
        r = next_by(events, slots, now, lambda e: e.kind == "live")
        if r:
            e, at = r
            out.append({"key": "live", "label": f"Next live: {e.title}", "category": "live",
                        "at": at, "source": "playlist"})
    return out


def probe_events(events: list[Event]) -> None:
    for e in events:
        if not e.is_file:
            continue
        try:
            info = media.probe(e.source)
            e.media_duration = info["duration"]
            e.media_info = info["summary"]
        except FileNotFoundError:
            e.media_duration = None
            e.media_info = "MISSING"
        except Exception as ex:  # noqa: BLE001 - surface any probe problem as a check
            e.media_duration = None
            e.media_info = f"UNREADABLE: {ex}"


def preair_checks(events: list[Event], now: float) -> list[dict]:
    """Validate the playlist before (and during) air. Returns [{level, uid, message}]."""
    issues: list[dict] = []

    def add(level: str, e: Event | None, msg: str) -> None:
        issues.append({"level": level, "uid": e.uid if e else None, "title": e.title if e else "", "message": msg})

    for e in events:
        if e.is_file:
            if e.media_info == "MISSING":
                add("error", e, f"media file not found: {e.source}")
                continue
            if e.media_info.startswith("UNREADABLE"):
                add("error", e, e.media_info)
                continue
            if "x" not in e.media_info:
                add("error", e, "no video stream")
            if "Hz" not in e.media_info:
                add("warning", e, "no audio stream (silence will be played)")
            if e.media_duration is not None:
                avail = e.media_duration - e.in_point
                if e.duration is not None and e.duration > avail + 0.04:
                    add("warning", e, f"media is shorter than planned ({fmt_tc(avail)} < {fmt_tc(e.duration)}); "
                                      "will end early")
        else:
            if not media.is_live_uri(e.source):
                add("error", e, f"live source must be a srt:// (or udp/rtmp/rtsp) URI, got '{e.source}'")
            if e.duration is None:
                add("info", e, "open-ended live: runs until the operator takes the next item")
        if e.hard_start is not None and e.hard_start < now:
            add("info", e, "hard start time is in the past")

    if events:
        slots = project(events, 0, now, now)
        for e, s in zip(events, slots):
            if s is None:
                continue
            if s.gap_before > 1:
                add("warning", e, f"gap of {fmt_tc(s.gap_before)} before hard start (slate will fill)")
            if s.truncated_by > 0.04:
                add("warning", e, f"will be cut short by {fmt_tc(s.truncated_by)} (next hard start)")
    return issues
