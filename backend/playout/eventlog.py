"""In-memory "stats for nerds" event log, fanned out to UI clients."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

log = logging.getLogger("playout")


class EventLog:
    def __init__(self, size: int = 500) -> None:
        self._buf: deque[dict] = deque(maxlen=size)
        self._lock = threading.Lock()
        self._seq = 0
        self.listeners: list[Callable[[dict], None]] = []

    def add(self, cat: str, msg: str, level: str = "info", **data) -> dict:
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "ts": time.time(), "cat": cat, "level": level, "msg": msg}
            if data:
                ev["data"] = data
            self._buf.append(ev)
        getattr(log, "warning" if level == "warn" else level if level in ("info", "error") else "info")(
            "[%s] %s", cat, msg)
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception:  # noqa: BLE001 - a broken listener must not break playout
                log.exception("log listener failed")
        return ev

    def recent(self, n: int = 200) -> list[dict]:
        with self._lock:
            return list(self._buf)[-n:]
