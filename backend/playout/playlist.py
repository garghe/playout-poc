"""Playlist model + JSON/CSV import.

A playlist is a list of items. Items are either:
  * programme - a file played from `source`
  * live      - an SRT feed (source = srt://...)
  * break     - a container of `spots` (each spot is a file)

For playout the playlist is flattened into a list of Events (breaks expand
into their spots), which is what the engine schedules.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time as _time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

FPS = 25
ITEM_TYPES = {"programme", "live", "break"}
START_MODES = {"follow", "hard"}


class PlaylistError(ValueError):
    pass


def parse_tc(value: Any) -> float | None:
    """Parse a duration/time: seconds (number), HH:MM:SS, HH:MM:SS.mmm or HH:MM:SS:FF."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})(?:([.:])(\d+))?", s)
    if not m:
        raise PlaylistError(f"bad timecode '{s}' (use HH:MM:SS, HH:MM:SS:FF or seconds)")
    h, mi, se, sep, frac = m.groups()
    secs = int(h) * 3600 + int(mi) * 60 + int(se)
    if sep == ":":
        secs += int(frac) / FPS
    elif sep == ".":
        secs += float("0." + frac)
    return float(secs)


def fmt_tc(secs: float | None) -> str:
    if secs is None:
        return "--:--:--:--"
    secs = max(0.0, secs)
    frames = int(round(secs * FPS))
    f = frames % FPS
    s = frames // FPS
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


@dataclass
class Event:
    """One playable thing on the timeline (a programme, a live feed or a spot)."""
    uid: str
    id: str
    title: str
    kind: str                       # programme | live | spot
    source: str
    category: str = ""
    start_mode: str = "follow"
    hard_start: float | None = None  # unix timestamp
    duration: float | None = None    # planned seconds (None = open ended live)
    in_point: float = 0.0
    break_id: str | None = None
    break_title: str | None = None
    break_first: bool = False
    break_last: bool = False
    break_duration: float | None = None
    scte: bool = False
    # filled by media checks
    media_duration: float | None = None
    media_info: str = ""

    @property
    def is_file(self) -> bool:
        return self.kind in ("programme", "spot")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Playlist:
    channel: str = "Channel 1"
    date: str | None = None
    items: list[dict] = field(default_factory=list)   # normalised original structure
    events: list[Event] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"channel": self.channel, "date": self.date, "items": self.items,
                "events": [e.to_dict() for e in self.events]}


def _hard_start_ts(start: Any, day: date, tz: ZoneInfo) -> float:
    if isinstance(start, str) and start.strip().startswith("+"):
        # relative hard start ("+00:02:30" = 2m30s after the playlist is loaded) - handy for demos
        return _time.time() + (parse_tc(start.strip()[1:]) or 0)
    secs = parse_tc(start)
    if secs is None:
        raise PlaylistError("hard start item needs a 'start' time")
    midnight = datetime.combine(day, time(0), tzinfo=tz)
    return (midnight + timedelta(seconds=secs)).timestamp()


def _norm_item(raw: dict, idx: int) -> dict:
    if not isinstance(raw, dict):
        raise PlaylistError(f"item {idx}: must be an object")
    it = {k: v for k, v in raw.items() if v not in (None, "")}
    it.setdefault("id", f"{idx + 1:03d}")
    it["id"] = str(it["id"])
    it.setdefault("title", it["id"])
    t = str(it.get("type", "programme")).lower()
    if t not in ITEM_TYPES:
        raise PlaylistError(f"item {it['id']}: type must be one of {sorted(ITEM_TYPES)}")
    it["type"] = t
    sm = str(it.get("start_mode", "hard" if "start" in it else "follow")).lower()
    if sm not in START_MODES:
        raise PlaylistError(f"item {it['id']}: start_mode must be follow or hard")
    it["start_mode"] = sm
    if t == "break":
        spots = it.get("spots") or []
        if not spots:
            raise PlaylistError(f"break {it['id']}: has no spots")
        it["spots"] = [_norm_spot(s, it["id"], j) for j, s in enumerate(spots)]
        it["scte"] = _bool(it.get("scte", True))
    else:
        if not it.get("source"):
            raise PlaylistError(f"item {it['id']}: missing 'source'")
    return it


def _norm_spot(raw: dict, break_id: str, j: int) -> dict:
    sp = {k: v for k, v in raw.items() if v not in (None, "")}
    sp.setdefault("id", f"{break_id}-{j + 1}")
    sp["id"] = str(sp["id"])
    sp.setdefault("title", sp["id"])
    if not sp.get("source"):
        raise PlaylistError(f"spot {sp['id']}: missing 'source'")
    return sp


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def build(channel: str, day: str | None, items: list[dict], tz: ZoneInfo) -> Playlist:
    if not items:
        raise PlaylistError("playlist has no items")
    the_day = date.fromisoformat(day) if day else datetime.now(tz).date()
    norm = [_norm_item(r, i) for i, r in enumerate(items)]
    seen: set[str] = set()
    events: list[Event] = []
    for it in norm:
        if it["id"] in seen:
            raise PlaylistError(f"duplicate id {it['id']}")
        seen.add(it["id"])
        hard = _hard_start_ts(it.get("start"), the_day, tz) if it["start_mode"] == "hard" else None
        if it["type"] == "break":
            spots = it["spots"]
            durs = [parse_tc(s.get("duration")) for s in spots]
            total = sum(d for d in durs if d) if all(durs) else None
            for j, sp in enumerate(spots):
                events.append(Event(
                    uid=f"{it['id']}/{sp['id']}", id=sp["id"], title=sp["title"], kind="spot",
                    source=str(sp["source"]), category=str(sp.get("category", "advert")),
                    start_mode=it["start_mode"] if j == 0 else "follow",
                    hard_start=hard if j == 0 else None,
                    duration=durs[j], in_point=parse_tc(sp.get("in")) or 0.0,
                    break_id=it["id"], break_title=it["title"],
                    break_first=j == 0, break_last=j == len(spots) - 1,
                    break_duration=total, scte=it["scte"]))
        else:
            dur = parse_tc(it.get("duration"))
            events.append(Event(
                uid=it["id"], id=it["id"], title=str(it["title"]), kind=it["type"],
                source=str(it["source"]), category=str(it.get("category", "")).lower(),
                start_mode=it["start_mode"], hard_start=hard, duration=dur,
                in_point=parse_tc(it.get("in")) or 0.0))
    return Playlist(channel=channel, date=the_day.isoformat(), items=norm, events=events)


def from_json(text: str, tz: ZoneInfo) -> Playlist:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise PlaylistError(f"invalid JSON: {e}") from e
    if isinstance(data, list):
        data = {"items": data}
    return build(data.get("channel", "Channel 1"), data.get("date"), data.get("items", []), tz)


CSV_COLUMNS = ["id", "title", "type", "category", "source", "start_mode", "start", "duration", "in", "scte"]


def from_csv(text: str, tz: ZoneInfo, channel: str = "Channel 1") -> Playlist:
    """CSV: one row per item. Rows with type=spot belong to the preceding break row."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or "source" not in [f.strip().lower() for f in reader.fieldnames]:
        raise PlaylistError(f"CSV needs a header row with at least: {', '.join(CSV_COLUMNS)}")
    items: list[dict] = []
    for n, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not any(row.values()) or row.get("id", "").startswith("#"):
            continue
        if row.get("type", "").lower() == "spot":
            if not items or items[-1].get("type") != "break":
                raise PlaylistError(f"CSV line {n}: spot must follow a break row")
            items[-1].setdefault("spots", []).append(row)
        else:
            items.append(row)
    return build(channel, None, items, tz)


def load(text: str, filename: str, tz: ZoneInfo) -> Playlist:
    if filename.lower().endswith(".csv"):
        return from_csv(text, tz)
    return from_json(text, tz)
