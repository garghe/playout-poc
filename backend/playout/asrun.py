"""As-run log: what actually went to air, one CSV file per day."""
from __future__ import annotations

import csv
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .playlist import fmt_tc

FIELDS = ["date", "on_air", "off_air", "duration", "uid", "title", "kind", "category", "source",
          "break", "status", "note"]


class AsRunLog:
    def __init__(self, directory: Path, tz: ZoneInfo) -> None:
        self.dir = directory
        self.tz = tz
        self._lock = threading.Lock()
        self.dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, ts: float | None = None) -> Path:
        d = datetime.fromtimestamp(ts or datetime.now().timestamp(), self.tz).date().isoformat()
        return self.dir / f"asrun-{d}.csv"

    def _fmt(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.tz).strftime("%H:%M:%S.%f")[:-3]

    def write(self, *, start: float, end: float, uid: str, title: str, kind: str, category: str = "",
              source: str = "", break_title: str | None = None, status: str = "complete", note: str = "") -> dict:
        row = {
            "date": datetime.fromtimestamp(start, self.tz).date().isoformat(),
            "on_air": self._fmt(start), "off_air": self._fmt(end), "duration": fmt_tc(end - start),
            "uid": uid, "title": title, "kind": kind, "category": category, "source": source,
            "break": break_title or "", "status": status, "note": note,
        }
        path = self.path_for(start)
        with self._lock:
            new = not path.exists()
            with path.open("a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
        return row

    def read(self, ts: float | None = None) -> list[dict]:
        path = self.path_for(ts)
        if not path.exists():
            return []
        with path.open() as f:
            return list(csv.DictReader(f))
