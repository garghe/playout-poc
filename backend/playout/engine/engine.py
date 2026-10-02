"""Automation engine: schedules playlist events onto the output, handles
breaks/SCTE, live inputs, fallback, alarms, countdowns and the as-run log.

All GStreamer and engine state changes happen on one GLib main-loop thread.
API calls are marshalled onto it with `call()`.
"""
from __future__ import annotations

import concurrent.futures
import threading
import time
import uuid
from typing import Any, Callable

from .. import media, schedule
from ..asrun import AsRunLog
from ..config import CONFIG
from ..eventlog import EventLog
from ..playlist import Event, Playlist, fmt_tc
from .gst import GLib, check_elements
from .output import Output
from .sources import FileSource, LiveSource, SlateSource, Source

TICK_MS = 20
DEFAULT_LIVE_NAME = "LIVE-1"


class Engine:
    def __init__(self, log: EventLog, asrun: AsRunLog) -> None:
        self.log = log
        self.asrun = asrun
        self.loop = GLib.MainLoop()
        self.thread = threading.Thread(target=self.loop.run, name="glib", daemon=True)

        self.playlist: Playlist | None = None
        self.events: list[Event] = []
        self.mode = "stopped"            # stopped | auto | hold | live
        self.cur: int | None = None      # index of on-air event in self.events
        self.cur_ev: Event | None = None
        self.cur_start = 0.0
        self.cur_src: Source | None = None
        self.cur_note = ""
        self.pending: int | None = None  # waiting (on slate) for this hard-start event
        self.cursor = 0                  # where Start begins when stopped
        self.status: dict[str, str] = {}  # uid -> done | error | skipped | truncated
        self.prerolled: dict[int, FileSource] = {}
        self.fallback: str | None = None
        self.resume_idx: int | None = None
        self.live_override: LiveSource | None = None
        self.manual_cds: list[dict] = []
        self.alarms: dict[str, dict] = {}
        self.checks: list[dict] = []
        self.scte_id = int(time.time()) % 100000
        self._break_scte: int | None = None
        self._in_break: str | None = None
        self._in_break_title = ""
        self._black_since: float | None = None
        self._silence_since: float | None = None
        self._last_meters: dict = {}
        self._rate = {"t": time.time(), "bytes": 0, "frames": 0, "kbps": 0.0, "fps": 0.0}
        self.snapshot: dict = {}
        self.on_state: Callable[[dict], None] | None = None
        self.on_meters: Callable[[dict], None] | None = None

        check_elements()
        self.output = Output(self._notify)
        self.slate = SlateSource(self._notify)
        self.lives: dict[str, LiveSource] = {}

    # ------------------------------------------------------------------ infra
    def _notify(self, cat: str, msg: str, level: str = "info") -> None:
        if level == "debug":
            level = "info"
        self.log.add(cat, msg, level)

    def start(self) -> None:
        self.thread.start()
        self.call(self._boot).result(10)

    def _boot(self) -> None:
        self.output.start()
        self.slate.start()
        self._slate_on("Off air - automation stopped")
        self._ensure_live(CONFIG.default_live, DEFAULT_LIVE_NAME)
        GLib.timeout_add(TICK_MS, self._tick)
        GLib.timeout_add(100, self._meters_tick)
        GLib.timeout_add(250, self._state_tick)
        GLib.timeout_add(1000, self._stats_tick)
        self.log.add("ENGINE", "engine started")

    def call(self, fn: Callable[..., Any], *args) -> concurrent.futures.Future:
        """Run fn on the GLib thread; returns a Future with its result."""
        fut: concurrent.futures.Future = concurrent.futures.Future()

        def run():
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(fn(*args))
                except Exception as e:  # noqa: BLE001
                    fut.set_exception(e)
            return False

        GLib.idle_add(run)
        return fut

    def shutdown(self) -> None:
        def stop():
            for s in [*self.prerolled.values(), *self.lives.values(), self.slate]:
                s.destroy()
            if isinstance(self.cur_src, FileSource):
                self.cur_src.destroy()
            self.output.stop()
            self.loop.quit()
        try:
            self.call(stop).result(5)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ live inputs
    def _ensure_live(self, uri: str, name: str | None = None) -> LiveSource:
        if uri not in self.lives:
            name = name or f"LIVE-{len(self.lives) + 1}"
            src = LiveSource(name, uri, CONFIG.udp_base + 1 + len(self.lives), self._notify)
            self.lives[uri] = src
            src.start()
            self.log.add("SRT-IN", f"{name}: listening on {uri}")
        return self.lives[uri]

    def live_by_name(self, name: str | None) -> LiveSource:
        if not name:
            return self.lives[CONFIG.default_live]
        for s in self.lives.values():
            if s.name == name or s.uri == name:
                return s
        raise ValueError(f"unknown live input {name}")

    # -------------------------------------------------------------- playlist
    def set_playlist(self, pl: Playlist) -> dict:
        for s in self.prerolled.values():
            s.destroy()
        self.prerolled.clear()
        self.playlist = pl
        self.events = pl.events
        self.status = {}
        self.cursor = 0
        self.pending = None
        self.resume_idx = None
        self.cur = None  # on-air item (if any) keeps playing as an orphan until it ends
        for e in self.events:
            if e.kind == "live":
                self._ensure_live(e.source)
        self.checks = schedule.preair_checks(self.events, time.time())
        errs = sum(1 for c in self.checks if c["level"] == "error")
        warns = sum(1 for c in self.checks if c["level"] == "warning")
        self.log.add("PLAYLIST", f"loaded '{pl.channel}' {pl.date}: {len(pl.items)} items / {len(self.events)} "
                                 f"events, checks: {errs} errors, {warns} warnings",
                     "warn" if errs else "info")
        if self.mode == "auto" and self.cur_ev is None:
            self._advance_to(0)
        return {"events": len(self.events), "errors": errs, "warnings": warns}

    # ------------------------------------------------------------- switching
    def _slate_on(self, caption: str) -> None:
        self.slate.set_caption(caption)
        self.slate.set_on_air(True)

    def _slate_off(self) -> None:
        # the router has a single active source: taking another source implicitly releases the slate
        self.slate.set_on_air(False)

    def _release(self, src: Source | None) -> None:
        if src is None:
            return
        if isinstance(src, FileSource):
            src.destroy()
        elif isinstance(src, LiveSource):
            src.set_on_air(False)

    def _preroll(self, idx: int) -> None:
        if idx in self.prerolled or idx >= len(self.events):
            return
        e = self.events[idx]
        if not e.is_file or self.status.get(e.uid) == "skipped":
            return
        try:
            fs = FileSource(e.uid, media.to_uri(e.source), e.in_point, self._notify)
        except Exception as ex:  # noqa: BLE001
            self.log.add("PREROLL", f"[{e.uid}] cannot create player: {ex}", "error")
            return
        fs.on_error = self._on_file_error
        fs.on_eos = self._on_file_eos
        fs.preroll()
        self.prerolled[idx] = fs
        self.log.add("PREROLL", f"[{e.uid}] {e.title} cueing ({e.source})")

    def _take(self, idx: int, note: str = "") -> None:
        """Put event idx on air now."""
        e = self.events[idx]
        now = time.time()
        prev_src = self.cur_src
        self.pending = None
        self.fallback = None
        self._clear_alarm("fallback")

        if e.is_file:
            src = self.prerolled.pop(idx, None)
            cued = src is not None and src.prerolled
            if src is None:
                try:
                    src = FileSource(e.uid, media.to_uri(e.source), e.in_point, self._notify)
                except Exception as ex:  # noqa: BLE001
                    self._fail(idx, f"cannot open media: {ex}")
                    return
                src.on_error = self._on_file_error
                src.on_eos = self._on_file_eos
            self._release(prev_src)
            self._slate_off()
            src.play()
            how = "cued" if cued else "NOT cued (late load)"
        else:
            src = self._ensure_live(e.source)
            self._release(prev_src)
            if src.has_signal:
                self._slate_off()
                src.set_on_air(True)
            else:
                src.set_on_air(True)
                self._enter_fallback(f"no signal on {src.name}")
            how = f"input {src.name}"

        self._break_transition(idx)
        self.cur, self.cur_ev, self.cur_src = idx, e, src
        self.cur_start = now
        self.cur_note = note
        self.cursor = idx + 1
        dur = schedule.planned_duration(e)
        self.log.add("SOURCE", f"ON AIR [{e.uid}] {e.title} | {e.kind}{'/' + e.category if e.category else ''} | "
                               f"{how} | dur {fmt_tc(dur) if dur else 'open'}",
                     data={"uid": e.uid, "kind": e.kind})

    def _break_transition(self, idx: int | None) -> None:
        """Track entering/leaving an ad break by what actually goes on air
        (robust to skipped spots, takes and live overrides)."""
        e = self.events[idx] if idx is not None else None
        new = e.break_id if e is not None and e.kind == "spot" else None
        if self._in_break and new != self._in_break:
            self.log.add("BREAK", f"break '{self._in_break_title}' ended")
            if self._break_scte is not None:
                self.output.splice_in(self._break_scte, 0.0)
                self.log.add("SCTE35", f"queued IN #{self._break_scte} (end of break {self._in_break})")
                self._break_scte = None
            self._in_break = None
        if new and new != self._in_break:
            remaining = [x for x in self.events[idx:] if x.break_id == new
                         and self.status.get(x.uid) != "skipped"]
            durs = [schedule.planned_duration(x) for x in remaining]
            total = sum(durs) if all(d is not None for d in durs) else None
            self._in_break, self._in_break_title = new, e.break_title
            self.log.add("BREAK", f"break '{e.break_title}' started: {len(remaining)} spots, "
                                  f"{fmt_tc(total) if total else 'unknown duration'}")
            if e.scte and total:
                self._break_scte = self._scte_out(total, 0.0, f"break {new}")

    def _finish_current(self, status: str) -> None:
        e = self.cur_ev
        if e is None:
            return
        end = time.time()
        if self.status.get(e.uid) != "error":
            self.status[e.uid] = "done" if status == "complete" else status
        note = self.cur_note + (f"; fallback: {self.fallback}" if self.fallback else "")
        self.asrun.write(start=self.cur_start, end=end, uid=e.uid, title=e.title, kind=e.kind,
                         category=e.category, source=e.source, break_title=e.break_title,
                         status=status, note=note.strip("; "))
        self.log.add("ASRUN", f"[{e.uid}] {e.title} {status} ({fmt_tc(end - self.cur_start)})")
        self.cur_ev = None

    def _next_index(self, after: int | None) -> int:
        i = self.cursor if after is None else after + 1
        while i < len(self.events) and self.status.get(self.events[i].uid) == "skipped":
            i += 1
        return i

    def _advance(self, status: str, force: bool = False, note: str = "") -> None:
        nxt = self._next_index(self.cur)
        self._finish_current(status)
        self._advance_to(nxt, force, note)

    def _advance_to(self, nxt: int, force: bool = False, note: str = "") -> None:
        nxt = self._next_index(nxt - 1)   # never put a skipped item on air
        if nxt >= len(self.events):
            self._release(self.cur_src)
            self.cur_src, self.cur = None, None
            self._break_transition(None)
            self._slate_on("End of playlist")
            self.mode = "stopped"
            self.cursor = len(self.events)
            self.log.add("ENGINE", "end of playlist reached - slate on air", "warn")
            return
        e = self.events[nxt]
        if not force and e.hard_start is not None and e.hard_start > time.time() + 0.04:
            self._release(self.cur_src)
            self.cur_src, self.cur = None, None
            self.pending = nxt
            self._break_transition(None)
            self._slate_on("Programmes will resume shortly")
            self.log.add("SOURCE", f"FILLER slate until hard start of [{e.uid}] {e.title} at "
                                   f"{self._clock(e.hard_start)}")
            self._preroll(nxt)
            return
        self._take(nxt, note)

    def _fail(self, idx: int, reason: str) -> None:
        e = self.events[idx]
        self.status[e.uid] = "error"
        self.log.add("ERROR", f"[{e.uid}] {e.title}: {reason} - skipping", "error")
        self._raise_alarm(f"item:{e.uid}", "error", f"{e.title}: {reason}")
        self.cur = idx
        self.cursor = idx + 1
        self._advance_to(idx + 1, note="after failed item")

    def _enter_fallback(self, reason: str) -> None:
        if self.fallback == reason:
            return
        self.fallback = reason
        self._slate_on("We'll be right back")
        self.log.add("FALLBACK", f"slate engaged: {reason}", "error")
        self._raise_alarm("fallback", "error", f"Fallback slate on air: {reason}")

    def _exit_fallback(self, why: str) -> None:
        if self.cur_src is not None:
            self.cur_src.set_on_air(True)
        else:
            self._slate_off()
        self.log.add("FALLBACK", f"slate released: {why}")
        self.fallback = None
        self._clear_alarm("fallback")

    # --------------------------------------------------------------- bus cbs
    def _on_file_eos(self, src: FileSource) -> None:
        if src is not self.cur_src:
            return
        e = self.cur_ev
        planned = schedule.planned_duration(e) if e else None
        elapsed = time.time() - self.cur_start
        if self.mode == "auto":
            short = planned is not None and elapsed < planned - 0.5
            self._advance("complete" if not short else "short", note="ended early" if short else "")
        elif self.mode == "hold":
            self.log.add("ENGINE", f"[{e.uid}] ended during HOLD - slate on air", "warn")
            self._enter_fallback("item ended while on hold")

    def _on_file_error(self, src: FileSource, message: str) -> None:
        idx = next((i for i, s in self.prerolled.items() if s is src), None)
        if idx is not None:
            self.prerolled.pop(idx).destroy()
            self.status[self.events[idx].uid] = "error"
            self.log.add("PREROLL", f"[{self.events[idx].uid}] failed to cue: {message}", "error")
            self._raise_alarm(f"item:{self.events[idx].uid}", "error", f"{self.events[idx].title}: {message}")
            return
        if src is self.cur_src and self.cur is not None:
            self.cur_src = None
            src.destroy()
            self._finish_current("error")
            self._fail(self.cur, message)

    # ------------------------------------------------------------------ tick
    def _cur_end(self) -> float | None:
        if self.cur_ev is None:
            return None
        d = schedule.planned_duration(self.cur_ev)
        return self.cur_start + d if d is not None else None

    def _tick(self) -> bool:
        try:
            self._tick_inner()
        except Exception as ex:  # noqa: BLE001 - never let the scheduler die
            self.log.add("ENGINE", f"tick error: {ex!r}", "error")
        return True

    def _tick_inner(self) -> None:
        now = time.time()
        self._countdown_actions(now)

        # live signal supervision for whatever live source is on air
        if isinstance(self.cur_src, LiveSource):
            src = self.cur_src
            if not src.has_signal and self.fallback is None:
                self._enter_fallback(f"signal lost on {src.name}")
            elif src.has_signal and self.fallback and self.fallback.startswith(("signal lost", "no signal")):
                self._exit_fallback(f"signal back on {src.name}")

        if self.mode != "auto":
            return
        if self.pending is not None:
            e = self.events[self.pending]
            if e.hard_start is not None and e.hard_start - now <= CONFIG.preroll_s:
                self._preroll(self.pending)
            if e.hard_start is None or now >= e.hard_start:
                self._take(self.pending)
            return
        if self.cur_ev is None:
            return
        nxt = self._next_index(self.cur)
        end = self._cur_end()
        if nxt < len(self.events):
            ne = self.events[nxt]
            hard = ne.hard_start
            switch_at = min(x for x in (end, hard) if x is not None) if (end or hard) else None
            if switch_at is not None and switch_at - now <= CONFIG.preroll_s:
                self._preroll(nxt)
            if hard is not None and now >= hard:
                truncated = end is not None and end - now > 0.04
                self._advance("truncated" if truncated else "complete", force=True,
                              note=f"hard start of [{ne.uid}]" if truncated else "")
                return
        if end is not None and now >= end:
            self._advance("complete")

    def _countdown_actions(self, now: float) -> None:
        for cd in self.manual_cds:
            if not cd["fired"] and now >= cd["at"]:
                cd["fired"] = True
                self.log.add("COUNTDOWN", f"'{cd['label']}' reached zero (action: {cd['action']})")
                try:
                    if cd["action"] == "go_live":
                        self.go_live(cd.get("live_input"), note=f"countdown '{cd['label']}'")
                    elif cd["action"] == "take_next":
                        self.take_next()
                    elif cd["action"] == "scte":
                        self.scte_out(cd.get("scte_duration") or 60, 0)
                except Exception as ex:  # noqa: BLE001
                    self.log.add("COUNTDOWN", f"action failed: {ex}", "error")
        self.manual_cds = [c for c in self.manual_cds if not (c["fired"] and now - c["at"] > 15)]

    # ----------------------------------------------------- meters & alarms
    def _meters_tick(self) -> bool:
        m = self.output.take_meters()
        self._last_meters = m
        now = time.time()
        # black detection on a 64x36 luma thumbnail: dark AND flat (video black is Y=16)
        if self.output.luma < 24 and self.output.luma_std < 4:
            self._black_since = self._black_since or now
            if now - self._black_since >= CONFIG.black_alarm_s:
                self._raise_alarm("black", "warning", f"Black on output for {now - self._black_since:.0f}s")
        else:
            self._black_since = None
            self._clear_alarm("black")
        # silence detection
        if max(m["peak"]) < CONFIG.silence_dbfs:
            self._silence_since = self._silence_since or now
            if now - self._silence_since >= CONFIG.silence_alarm_s:
                self._raise_alarm("silence", "warning", f"Silence on output for {now - self._silence_since:.0f}s")
        else:
            self._silence_since = None
            self._clear_alarm("silence")
        # loudness (short term)
        s = m.get("lufs_s")
        if s is not None and s > CONFIG.loudness_max_lufs:
            self._raise_alarm("loudness", "warning", f"Loud: short-term {s} LUFS > {CONFIG.loudness_max_lufs}")
        elif s is None or s < CONFIG.loudness_max_lufs - 1:
            self._clear_alarm("loudness")
        if self.on_meters:
            self.on_meters(m)
        return True

    def _raise_alarm(self, key: str, level: str, msg: str) -> None:
        a = self.alarms.get(key)
        if a is None:
            self.alarms[key] = {"key": key, "level": level, "msg": msg, "since": time.time()}
            self.log.add("ALARM", f"RAISED {msg}", "error" if level == "error" else "warn")
        else:
            a["msg"] = msg

    def _clear_alarm(self, key: str) -> None:
        a = self.alarms.pop(key, None)
        if a:
            self.log.add("ALARM", f"cleared: {a['msg']}")

    def clear_alarms(self) -> None:
        for k in [k for k in self.alarms if k.startswith("item:")]:
            self._clear_alarm(k)

    def _stats_tick(self) -> bool:
        now = time.time()
        r = self._rate
        dt = max(0.001, now - r["t"])
        r["kbps"] = (self.output.bytes_out - r["bytes"]) * 8 / 1000 / dt
        r["fps"] = (self.output.frames - r["frames"]) / dt
        r.update(t=now, bytes=self.output.bytes_out, frames=self.output.frames)
        for src in self.lives.values():
            prev = getattr(src, "_prev_bytes", 0)
            src.kbps = (src.bytes_in - prev) * 8 / 1000 / dt
            src._prev_bytes = src.bytes_in
            was = getattr(src, "_was_signal", None)
            if was is not None and was != src.has_signal:
                self.log.add("SRT-IN", f"{src.name}: signal {'UP' if src.has_signal else 'LOST'}",
                             "info" if src.has_signal else "warn")
            src._was_signal = src.has_signal
        return True

    # ---------------------------------------------------------------- state
    def _clock(self, ts: float | None) -> str:
        if ts is None:
            return "--:--:--"
        from datetime import datetime
        return datetime.fromtimestamp(ts, CONFIG.tz).strftime("%H:%M:%S")

    def _projection(self, now: float) -> list:
        if not self.events:
            return []
        if self.cur is not None and self.cur_ev is self.events[self.cur]:
            return schedule.project(self.events, self.cur, self.cur_start, now)
        if self.pending is not None:
            e = self.events[self.pending]
            return schedule.project(self.events, self.pending, max(e.hard_start or now, now), now)
        start_from = self.resume_idx if (self.mode == "live" and self.resume_idx is not None) else self.cursor
        start_from = self._next_index(start_from - 1)
        if start_from >= len(self.events):
            return [None] * len(self.events)
        t0 = (self._cur_end() or now) if self.cur_ev is not None else now
        return schedule.project(self.events, start_from, max(t0, now), now)

    def _state_tick(self) -> bool:
        try:
            self.snapshot = self._build_state()
            if self.on_state:
                self.on_state(self.snapshot)
        except Exception as ex:  # noqa: BLE001
            self.log.add("ENGINE", f"state error: {ex!r}", "error")
        return True

    def _build_state(self) -> dict:
        now = time.time()
        slots = self._projection(now)
        if self.pending is not None:
            nxt = self.pending
        elif self.cur is not None and self.cur_ev is not None:
            nxt = self._next_index(self.cur)
        elif self.mode == "live" and self.resume_idx is not None:
            nxt = self._next_index(self.resume_idx - 1)
        else:
            nxt = self._next_index(self.cursor - 1)
        evs = []
        for i, e in enumerate(self.events):
            s = slots[i] if i < len(slots) else None
            st = self.status.get(e.uid, "")
            if i == self.cur and self.cur_ev is e:
                st = "on_air"
            elif i == nxt and st in ("", None):
                st = "next"
            d = e.to_dict()
            d.update(index=i, status=st, planned=schedule.planned_duration(e),
                     cued=i in self.prerolled and self.prerolled[i].prerolled,
                     p_start=s.start if s else None, p_end=s.end if s else None,
                     gap_before=s.gap_before if s else 0, truncated_by=s.truncated_by if s else 0)
            evs.append(d)

        on_air = None
        if self.cur_ev is not None:
            e, end = self.cur_ev, self._cur_end()
            on_air = {"uid": e.uid, "title": e.title, "kind": e.kind, "category": e.category,
                      "break_title": e.break_title, "start": self.cur_start, "end": end,
                      "elapsed": now - self.cur_start, "remaining": (end - now) if end else None,
                      "source": getattr(self.cur_src, "name", ""), "orphan": self.cur is None}
        elif self.live_override is not None:
            on_air = {"uid": "LIVE", "title": f"Live override: {self.live_override.name}", "kind": "live",
                      "category": "live", "start": self.cur_start, "end": None,
                      "elapsed": now - self.cur_start, "remaining": None, "source": self.live_override.name}
        nxt_info = None
        if 0 <= nxt < len(self.events):
            ne, ns = self.events[nxt], slots[nxt] if nxt < len(slots) else None
            nxt_info = {"uid": ne.uid, "title": ne.title, "kind": ne.kind, "start": ns.start if ns else None,
                        "in": (ns.start - now) if ns and ns.start else None}

        cds = schedule.countdowns(self.events, [s if i >= (self.cur or 0) else None for i, s in enumerate(slots)],
                                  now) if slots else []
        for c in self.manual_cds:
            cds.append({"key": c["id"], "label": c["label"], "category": c["category"], "at": c["at"],
                        "source": "manual", "action": c["action"], "fired": c["fired"]})
        for c in cds:
            c["remaining"] = c["at"] - now

        stats = {
            "output": {**self.output.stats(), "kbps": round(self._rate["kbps"]), "fps": round(self._rate["fps"], 1),
                       "uri": CONFIG.output_srt, "scte_pid": CONFIG.scte_pid},
            "live_inputs": [{**s.stats(), "name": s.name, "on_air": s.on_air,
                             "kbps": round(getattr(s, "kbps", 0))} for s in self.lives.values()],
            "engine": {"prerolled": [self.events[i].uid for i in self.prerolled if i < len(self.events)],
                       "slate_on_air": self.slate.on_air, "glib_tick_ms": TICK_MS},
        }
        return {
            "now": now, "mode": self.mode, "channel": self.playlist.channel if self.playlist else None,
            "date": self.playlist.date if self.playlist else None,
            "tz": str(CONFIG.tz), "on_air": on_air, "next": nxt_info, "fallback": self.fallback,
            "pending_hard_start": self.events[self.pending].hard_start if self.pending is not None else None,
            "events": evs, "items": self.playlist.items if self.playlist else [],
            "countdowns": sorted(cds, key=lambda c: c["at"]),
            "alarms": list(self.alarms.values()), "checks": self.checks, "stats": stats,
            "meters": self._last_meters, "live_inputs": [s.name for s in self.lives.values()],
        }

    # ------------------------------------------------------------- commands
    # (always called on the GLib thread via call())
    def start_auto(self, index: int | None = None) -> None:
        if not self.events:
            raise ValueError("no playlist loaded")
        if self.mode in ("auto", "hold") and index is None:
            self.mode = "auto"
            self.log.add("CONTROL", "automation resumed")
            return
        self._end_live_override()
        self.mode = "auto"
        self.log.add("CONTROL", f"automation START from {'item ' + str(index + 1) if index is not None else 'cursor'}")
        if self.cur_ev is not None:
            self._finish_current("interrupted")
        self._advance_to(self.cursor if index is None else index, force=True)

    def stop_auto(self) -> None:
        if self.cur_ev is not None:
            self._finish_current("stopped")
        self._end_live_override()
        self._release(self.cur_src)
        self.cur_src, self.cur, self.pending = None, None, None
        self.mode = "stopped"
        self._break_transition(None)
        self._slate_on("Off air - automation stopped")
        self.log.add("CONTROL", "automation STOP - slate on air")

    def hold(self, on: bool) -> None:
        if self.mode not in ("auto", "hold"):
            raise ValueError("automation is not running")
        self.mode = "hold" if on else "auto"
        self.log.add("CONTROL", "HOLD on - no auto advance" if on else "HOLD off - automation resumed")
        if not on and self.fallback == "item ended while on hold":
            self.fallback = None
            self._clear_alarm("fallback")
            self._advance("complete")

    def take_next(self) -> None:
        if self.mode == "live":
            self.return_from_live()
            return
        if not self.events:
            raise ValueError("no playlist loaded")
        if self.mode == "stopped":
            self.mode = "auto"
        if self.pending is not None:
            self.log.add("CONTROL", "TAKE NEXT (ignoring hard start)")
            self._take(self.pending, "taken early")
            return
        self.log.add("CONTROL", "TAKE NEXT")
        if self.cur_ev is not None:
            self._advance("truncated", force=True, note="operator take")
        else:
            self._advance_to(self.cursor, force=True)

    def skip(self, uid: str) -> None:
        idx = next((i for i, e in enumerate(self.events) if e.uid == uid), None)
        if idx is None:
            raise ValueError(f"unknown item {uid}")
        if idx == self.cur and self.cur_ev is not None:
            raise ValueError("cannot skip the on-air item (use Take Next)")
        e = self.events[idx]
        if self.status.get(uid) == "skipped":
            del self.status[uid]
            self.log.add("CONTROL", f"[{uid}] {e.title} un-skipped")
            return
        self.status[uid] = "skipped"
        if idx in self.prerolled:
            self.prerolled.pop(idx).destroy()
        if self.pending == idx:
            self.pending = None
            self._advance_to(self._next_index(idx - 1))
        self.log.add("CONTROL", f"[{uid}] {e.title} SKIPPED")

    def set_cursor(self, uid: str) -> None:
        idx = next((i for i, e in enumerate(self.events) if e.uid == uid), None)
        if idx is None:
            raise ValueError(f"unknown item {uid}")
        if self.mode == "stopped":
            self.cursor = idx
        self.log.add("CONTROL", f"cursor set to [{uid}]")

    def go_live(self, name: str | None = None, note: str = "operator") -> None:
        src = self.live_by_name(name)
        if self.cur_ev is not None:
            self.resume_idx = self._next_index(self.cur)
            self._finish_current("interrupted")
        elif self.pending is not None:
            self.resume_idx = self.pending
        elif self.resume_idx is None:
            self.resume_idx = self.cursor
        self._release(self.cur_src)
        self._break_transition(None)
        self.cur, self.pending = None, None
        self.mode = "live"
        self.live_override = src
        self.cur_src = src
        self.cur_start = time.time()
        self.fallback = None
        if src.has_signal:
            self._slate_off()
        src.set_on_air(True)
        if not src.has_signal:
            self._enter_fallback(f"no signal on {src.name}")
        self.log.add("SOURCE", f"ON AIR LIVE OVERRIDE {src.name} ({src.uri}) - {note}", "warn")

    def _end_live_override(self) -> None:
        src = self.live_override
        if src is None:
            return
        self.asrun.write(start=self.cur_start, end=time.time(), uid="LIVE", title=f"Live override {src.name}",
                         kind="live", category="live", source=src.uri, status="complete",
                         note=f"fallback: {self.fallback}" if self.fallback else "")
        src.set_on_air(False)
        self.live_override = None
        if self.cur_src is src:
            self.cur_src = None
        if self.fallback:
            self.fallback = None
            self._clear_alarm("fallback")

    def return_from_live(self) -> None:
        if self.mode != "live":
            raise ValueError("not in live override")
        self._end_live_override()
        self.mode = "auto"
        idx = self.resume_idx if self.resume_idx is not None else self.cursor
        self.resume_idx = None
        self.log.add("CONTROL", "RETURN to playlist")
        self._advance_to(idx, force=True, note="return from live")

    def scte_out(self, duration: float, preroll: float = 0.0) -> int:
        return self._scte_out(duration, preroll, "manual")

    def _scte_out(self, duration: float, preroll: float, why: str) -> int:
        self.scte_id += 1
        self.output.splice_out(self.scte_id, duration, preroll)
        self.log.add("SCTE35", f"queued OUT #{self.scte_id} ({why}) duration {duration:.1f}s preroll {preroll:.1f}s",
                     data={"event_id": self.scte_id})
        return self.scte_id

    def scte_in(self, event_id: int | None = None, preroll: float = 0.0) -> int:
        eid = event_id or self.scte_id
        self.output.splice_in(eid, preroll)
        self.log.add("SCTE35", f"queued IN #{eid} (manual) preroll {preroll:.1f}s")
        return eid

    def add_countdown(self, label: str, category: str, at: float, action: str = "none",
                      live_input: str | None = None, scte_duration: float | None = None) -> dict:
        if action not in ("none", "go_live", "take_next", "scte"):
            raise ValueError("action must be none, go_live, take_next or scte")
        cd = {"id": uuid.uuid4().hex[:8], "label": label, "category": category, "at": at, "action": action,
              "live_input": live_input, "scte_duration": scte_duration, "fired": False}
        self.manual_cds.append(cd)
        self.log.add("COUNTDOWN", f"manual countdown '{label}' -> {self._clock(at)} (action: {action})")
        return cd

    def remove_countdown(self, cd_id: str) -> None:
        self.manual_cds = [c for c in self.manual_cds if c["id"] != cd_id]
        self.log.add("COUNTDOWN", f"countdown {cd_id} removed")

    def rerun_checks(self) -> list[dict]:
        schedule.probe_events(self.events)
        self.checks = schedule.preair_checks(self.events, time.time())
        return self.checks
