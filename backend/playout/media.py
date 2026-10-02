"""Media lookup and probing (ffprobe)."""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from .config import CONFIG


def is_live_uri(source: str) -> bool:
    return source.startswith(("srt://", "udp://", "rtmp://", "rtsp://"))


def resolve(source: str) -> Path:
    """Map a playlist source to a file path (absolute, relative to cwd, or in the media dir)."""
    if source.startswith("file://"):
        return Path(source[7:])
    p = Path(source)
    if p.is_absolute() or p.exists():
        return p.resolve()
    return (CONFIG.media_dir / source).resolve()


def to_uri(source: str) -> str:
    return source if is_live_uri(source) else resolve(source).as_uri()


@lru_cache(maxsize=512)
def _probe(path: str, mtime: float) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, timeout=20)
    if out.returncode != 0:
        raise ValueError(out.stderr.strip() or "ffprobe failed")
    return json.loads(out.stdout)


def probe(source: str) -> dict:
    """Return {duration, video, audio, summary} or raise FileNotFoundError / ValueError."""
    path = resolve(source)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    info = _probe(str(path), path.stat().st_mtime)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(info.get("format", {}).get("duration") or 0) or None
    parts = []
    if v:
        parts.append(f"{v.get('codec_name')} {v.get('width')}x{v.get('height')} @{v.get('avg_frame_rate')}")
    if a:
        parts.append(f"{a.get('codec_name')} {a.get('channels')}ch {a.get('sample_rate')}Hz")
    return {"duration": dur, "video": v is not None, "audio": a is not None,
            "summary": ", ".join(parts) or "no streams"}
