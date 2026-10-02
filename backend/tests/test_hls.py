import shutil
import subprocess
from pathlib import Path

import pytest

from playout.hls import PTS_HZ, Packager, parse_splice

# Real sections captured from the programme output during a 30 s break.
OUT_HEX = "FC302500000000000000FFF01405000138537FEFF21398E7C57E002932E00000000000004D21E681"
IN_HEX = "FC302000000000000000FFF00F05000138537F4FF213C22002000000000000B1507391"


def test_parse_splice_out_and_in():
    out = parse_splice(bytes.fromhex(OUT_HEX))
    assert out["out"] and out["id"] == 79955 and out["duration"] == pytest.approx(30.0)
    assert out["pts"] is not None
    inn = parse_splice(bytes.fromhex(IN_HEX))
    assert not inn["out"] and inn["id"] == 79955 and inn["duration"] is None
    assert inn["pts"] > out["pts"]


@pytest.fixture(scope="module")
def ts(tmp_path_factory) -> bytes:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("ts") / "src.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "26", "-c:v", "libx264", "-g", "25",
                    "-keyint_min", "25", "-sc_threshold", "0", "-c:a", "aac", "-f", "mpegts", str(out)], check=True)
    return out.read_bytes()


def run(ts: bytes, tmp: Path, cues=()) -> tuple[Packager, list[dict]]:
    events: list[dict] = []
    holder: dict = {}
    clock = lambda: 1_790_000_000.0 + ((holder["p"]._last_pts or 0) / PTS_HZ if "p" in holder else 0)  # noqa: E731
    p = Packager(tmp, 6.0, 10, notify=lambda **k: events.append(k), clock=clock)
    holder["p"] = p
    p.pending.extend(cues)
    for i in range(0, len(ts), 1316):
        p.feed(ts[i:i + 1316])
    return p, events


def test_segments_are_exact_and_playlist_is_valid(ts, tmp_path):
    p, events = run(ts, tmp_path)
    durs = [e["duration"] for e in events if e["event"] == "segment"]
    assert durs[:3] == [6.0, 6.0, 6.0]
    text = (tmp_path / "stream.m3u8").read_text()
    assert "#EXT-X-TARGETDURATION:6" in text and text.count("#EXT-X-PROGRAM-DATE-TIME") == len(durs)
    assert "CODECS=" in (tmp_path / "master.m3u8").read_text()
    for name in [ln for ln in text.splitlines() if ln.endswith(".ts")]:
        assert (tmp_path / name).read_bytes()[0] == 0x47


def test_cue_out_in_on_segment_boundaries(ts, tmp_path):
    p0, _ = run(ts, tmp_path / "probe")
    start = p0.segments[0]["pts"]
    out = {"id": 7, "out": True, "pts": start + 8 * PTS_HZ, "duration": 10.0, "hex": "0x" + OUT_HEX}
    inn = {"id": 7, "out": False, "pts": start + 18 * PTS_HZ, "duration": None, "hex": "0x" + IN_HEX}
    p, events = run(ts, tmp_path / "cues", [out, inn])
    cues = [e for e in events if e["event"] == "cue"]
    assert [c["type"] for c in cues] == ["out", "in"]
    assert cues[1]["actual"] == pytest.approx(10.0) and not cues[1]["auto"]
    durs = [e["duration"] for e in events if e["event"] == "segment"]
    assert durs[:4] == [6.0, 2.0, 6.0, 4.0]      # cut at 8 s (cue-out) and at 18 s (cue-in)
    text = (tmp_path / "cues" / "stream.m3u8").read_text()
    for tag in ("#EXT-X-CUE-OUT:DURATION=10.000", "#EXT-X-CUE-OUT-CONT:ElapsedTime=6.000", "#EXT-X-CUE-IN",
                "SCTE35-OUT=0x", "SCTE35-IN=0x", "PLANNED-DURATION=10.000"):
        assert tag in text
