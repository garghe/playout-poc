"""Runtime settings, all overridable with environment variables."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    media_dir: Path = Path(os.environ.get("PLAYOUT_MEDIA_DIR", ROOT / "media"))
    data_dir: Path = Path(os.environ.get("PLAYOUT_DATA_DIR", ROOT / "data"))
    ui_dir: Path = Path(os.environ.get("PLAYOUT_UI_DIR", ROOT / "ui" / "dist"))
    http_port: int = int(os.environ.get("PLAYOUT_HTTP_PORT", "8080"))
    # Programme output: SRT listener, so VLC/ffplay connect as callers.
    output_srt: str = os.environ.get("PLAYOUT_OUTPUT_SRT", "srt://:9000?mode=listener")
    # Always-available live input for manual "Go Live" (breaking news etc.)
    default_live: str = os.environ.get("PLAYOUT_DEFAULT_LIVE", "srt://:9001?mode=listener")
    # More live inputs created at startup (comma separated), so they show signal status before
    # any playlist is loaded. LIVE-2 (:9002) is the sample playlist's live item, LIVE-3 (:9003) the BBB loop.
    extra_live: tuple[str, ...] = tuple(u.strip() for u in os.environ.get(
        "PLAYOUT_EXTRA_LIVE", "srt://:9002?mode=listener,srt://:9003?mode=listener").split(",") if u.strip())
    # internal localhost UDP ports between the engine and the SRT gateway processes
    udp_base: int = int(os.environ.get("PLAYOUT_UDP_BASE", "19000"))
    video_bitrate_kbps: int = int(os.environ.get("PLAYOUT_VIDEO_KBPS", "6000"))
    x264_preset: str = os.environ.get("PLAYOUT_X264_PRESET", "superfast")
    scte_pid: int = int(os.environ.get("PLAYOUT_SCTE_PID", "500"))
    tz: ZoneInfo = ZoneInfo(os.environ.get("PLAYOUT_TZ", "Europe/London"))
    preroll_s: float = float(os.environ.get("PLAYOUT_PREROLL_S", "3"))
    live_loss_s: float = float(os.environ.get("PLAYOUT_LIVE_LOSS_S", "1.5"))
    black_alarm_s: float = float(os.environ.get("PLAYOUT_BLACK_ALARM_S", "5"))
    silence_alarm_s: float = float(os.environ.get("PLAYOUT_SILENCE_ALARM_S", "5"))
    silence_dbfs: float = float(os.environ.get("PLAYOUT_SILENCE_DBFS", "-60"))
    loudness_max_lufs: float = float(os.environ.get("PLAYOUT_LOUDNESS_MAX", "-20"))
    width: int = 1920
    height: int = 1080
    fps: int = 25


CONFIG = Config()

# Make process-local time (e.g. the slate clocks drawn by GStreamer) match PLAYOUT_TZ,
# even in a container that runs in UTC.
os.environ["TZ"] = str(CONFIG.tz)
time.tzset()
