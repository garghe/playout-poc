"""Supervisor for SRT gateway subprocesses (see playout/gateway.py)."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import ctypes
import signal

_libc = ctypes.CDLL(None, use_errno=True)
PR_SET_PDEATHSIG = 1


def _die_with_parent() -> None:
    # Linux: the gateway gets SIGTERM if the engine process dies (even on kill -9)
    _libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)

BACKEND = Path(__file__).resolve().parents[2]


class Gateway:
    module = "playout.gateway"

    def __init__(self, name: str, args: list[str], notify: Callable[[str, str, str], None],
                 on_event: Callable[[dict], None] | None = None, cat: str | None = None) -> None:
        self.name = name
        self.cat = cat or ("SRT-OUT" if args[0] == "out" else "SRT-IN")
        self.args = args
        self.notify = notify
        self.on_event = on_event
        self.stats: dict = {}
        self.callers = 0
        self.restarts = 0
        self._fails = 0
        self._proc: subprocess.Popen | None = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name=f"gw-{name}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(2)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _run(self) -> None:
        cat = self.cat
        while not self._stop:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", self.module, *self.args], cwd=BACKEND,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, preexec_fn=_die_with_parent)
            started = time.time()
            for line in self._proc.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = ev.get("event")
                if kind == "stats":
                    self.stats = ev.get("stats", {})
                elif kind == "caller":
                    self.callers = max(0, self.callers + (1 if ev["action"] == "connected" else -1))
                    self.notify(cat, f"{self.name}: caller {ev['action']} {ev.get('addr', '')}".rstrip()
                                + (f" (callers: {self.callers})" if cat == "SRT-OUT" else ""), "info")
                elif kind == "error" and self._fails:
                    pass   # already reported this failure streak
                elif kind in ("error", "warning"):
                    self.notify(cat, f"{self.name}: {ev.get('msg')}", "warn")
                if self.on_event:
                    self.on_event(ev)
            code = self._proc.wait()
            self.callers = 0
            self.stats = {}
            if self._stop:
                break
            self.restarts += 1
            quick = time.time() - started < 3
            self._fails = self._fails + 1 if quick else 0
            if code not in (0, -15) and self._fails <= 1:
                self.notify(cat, f"{self.name}: gateway exited ({code}), restarting"
                            + (" - is the port already in use?" if quick else ""), "warn")
            time.sleep(min(5.0, 0.5 * (2 ** self._fails)))

    def summary(self) -> dict:
        s = dict(self.stats)
        callers = s.pop("callers", None)
        if callers:   # listener mode: report the first caller's numbers
            s.update(callers[0])
        return s


class HlsPackagerProc(Gateway):
    """Supervises the HLS packager process (playout/hls.py) and keeps its stats."""
    module = "playout.hls"

    def __init__(self, notify: Callable[[str, str, str], None]) -> None:
        from ..config import CONFIG
        self.dir = CONFIG.data_dir / "hls"
        super().__init__("hls", [str(CONFIG.udp_base + 100), str(self.dir), str(CONFIG.hls_segment_s),
                                 str(CONFIG.hls_window)], notify, self._event, cat="HLS")
        self.hls: dict = {"segments": 0, "last_seq": None, "last_duration": None, "in_break": False,
                          "cues": 0, "last_segment_at": None}

    def _event(self, ev: dict) -> None:
        kind = ev.get("event")
        if kind == "started":
            self.notify("HLS", f"packager started: {ev.get('segment_s')}s segments, window {ev.get('window')}, "
                               f"/hls/master.m3u8", "info")
        elif kind == "segment":
            first = self.hls["segments"] == 0
            self.hls.update(segments=self.hls["segments"] + 1, last_seq=ev["seq"],
                            last_duration=ev["duration"], in_break=ev["in_break"], last_segment_at=time.time())
            if first:
                self.notify("HLS", f"first segment written (seq {ev['seq']}, {ev['duration']:.2f}s): stream is live",
                            "info")
        elif kind == "cue":
            self.hls["cues"] += 1
            if ev["type"] == "out":
                self.notify("HLS", f"#EXT-X-CUE-OUT duration {ev['duration']:.1f}s (splice {ev['id']}) "
                                   f"at segment {ev['seq']}", "info")
            else:
                how = "auto-return (duration elapsed)" if ev.get("auto") else "splice IN"
                self.notify("HLS", f"#EXT-X-CUE-IN after {ev['actual']:.1f}s ({how}, splice {ev['id']}) "
                                   f"at segment {ev['seq']}", "info")
        elif kind == "error":
            self.notify("HLS", f"packager error: {ev.get('msg')}", "warn")

    def summary(self) -> dict:
        h = dict(self.hls)
        at = h.pop("last_segment_at")
        h["age"] = round(time.time() - at, 1) if at else None
        h["restarts"] = self.restarts
        return h
