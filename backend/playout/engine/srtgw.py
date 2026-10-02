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
    def __init__(self, name: str, args: list[str], notify: Callable[[str, str, str], None],
                 on_event: Callable[[dict], None] | None = None) -> None:
        self.name = name
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
        cat = "SRT-OUT" if self.args[0] == "out" else "SRT-IN"
        while not self._stop:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "playout.gateway", *self.args], cwd=BACKEND,
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
