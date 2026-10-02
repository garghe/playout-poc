"""HLS packager for FAST channels, run as a separate process by the engine.

Reads the programme MPEG-TS (same stream as the SRT output, SCTE-35 included)
from localhost UDP and writes a live HLS rendition:

  master.m3u8  ->  stream.m3u8  ->  seg_<n>.ts

* Segments are cut on keyframes (the encoder makes one every second), at the
  target duration, and always at an SCTE-35 splice point, so ads can be
  stitched on a clean segment boundary.
* SCTE-35 splice_insert OUT/IN become playlist cues in both common styles
  used by SSAI services (e.g. AWS MediaTailor):
    #EXT-X-CUE-OUT:DURATION=30 / #EXT-X-CUE-OUT-CONT:ElapsedTime=..,Duration=.. / #EXT-X-CUE-IN
    #EXT-X-DATERANGE ... SCTE35-OUT=0x... / SCTE35-IN=0x...
* Every segment carries #EXT-X-PROGRAM-DATE-TIME.

  python -m playout.hls <udp_port> <out_dir> [segment_s] [window]

Prints one JSON object per line: {"event": "started"|"segment"|"cue"|"error", ...}
"""
from __future__ import annotations

import json
import math
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PTS_HZ = 90000
WRAP = 1 << 33


def emit(**kw) -> None:
    print(json.dumps(kw), flush=True)


def _pts(b: bytes) -> int:
    return ((b[0] >> 1) & 0x07) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_splice(sec: bytes) -> dict | None:
    """Decode a splice_insert from an SCTE-35 section (table 0xFC). Returns None for others."""
    if len(sec) < 15 or sec[0] != 0xFC:
        return None
    pts_adj = ((sec[4] & 1) << 32) | int.from_bytes(sec[5:9], "big")
    if sec[13] != 0x05:                     # only splice_insert (0x00 = null heartbeat)
        return None
    p = sec[14:]
    ev = {"id": int.from_bytes(p[0:4], "big")}
    if p[4] & 0x80:
        return None                          # cancel
    flags = p[5]
    ev["out"] = bool(flags & 0x80)
    i = 6
    ev["pts"] = None
    if flags & 0x40 and not flags & 0x10:    # program splice, not immediate
        if p[i] & 0x80:
            ev["pts"] = (((p[i] & 1) << 32) | int.from_bytes(p[i + 1:i + 5], "big")) + pts_adj
            i += 5
        else:
            i += 1
    ev["duration"] = None
    if flags & 0x20:
        ev["duration"] = ((((p[i] & 1) << 32) | int.from_bytes(p[i + 1:i + 5], "big"))) / PTS_HZ
    return ev


class Packager:
    def __init__(self, out_dir: Path, segment_s: float = 6.0, window: int = 10,
                 notify=emit, clock=time.time) -> None:
        self.dir = out_dir
        self.target = segment_s
        self.window = window
        self.notify = notify
        self.clock = clock
        self.dir.mkdir(parents=True, exist_ok=True)
        for f in self.dir.glob("*"):
            if f.suffix in (".ts", ".m3u8", ".tmp"):
                f.unlink()
        # stream structure
        self.pmt_pid: int | None = None
        self.video_pid: int | None = None
        self.scte_pid: int | None = None
        self.pat_pkt: bytes | None = None
        self.pmt_pkt: bytes | None = None
        # pts unwrap
        self._last_pts: int | None = None
        self._wraps = 0
        # segments
        self.seq = int(self.clock())              # monotonic across restarts
        self.segments: list[dict] = []
        self.cur: dict | None = None
        self._fh = None
        self._pdt_next: float | None = None
        # SCTE-35 cues
        self.pending: list[dict] = []            # cues waiting for their keyframe
        self.brk: dict | None = None             # open ad break
        self._scte_buf = b""
        self.cues = 0
        self._write_master_needed = True

    # ------------------------------------------------------------ TS input
    def feed(self, data: bytes) -> None:
        for i in range(0, len(data) - 187, 188):
            pk = data[i:i + 188]
            if pk[0] == 0x47:
                self._packet(pk)

    def _payload(self, pk: bytes) -> bytes:
        afc = (pk[3] >> 4) & 3
        if not afc & 1:
            return b""
        return pk[5 + pk[4]:] if afc & 2 else pk[4:]

    def _packet(self, pk: bytes) -> None:
        pid = ((pk[1] & 0x1F) << 8) | pk[2]
        pusi = bool(pk[1] & 0x40)
        if pid == 0 and pusi:
            self.pat_pkt = pk
            pl = self._payload(pk)
            sec = pl[1 + pl[0]:]
            if len(sec) >= 12:
                self.pmt_pid = ((sec[10] & 0x1F) << 8) | sec[11]
        elif pid == self.pmt_pid and pusi:
            self.pmt_pkt = pk
            self._parse_pmt(self._payload(pk))
        elif pid == self.scte_pid:
            self._scte(pk, pusi)
        elif pid == self.video_pid and pusi:
            pts, key = self._pes_info(self._payload(pk))
            if pts is not None and key:
                self._keyframe(self._unwrap(pts))
        if self._fh is not None:
            self._fh.write(pk)
            self.cur["bytes"] += 188

    def _parse_pmt(self, pl: bytes) -> None:
        sec = pl[1 + pl[0]:]
        if len(sec) < 12 or sec[0] != 0x02:
            return
        end = 3 + (((sec[1] & 0x0F) << 8) | sec[2]) - 4
        i = 12 + (((sec[10] & 0x0F) << 8) | sec[11])
        while i + 5 <= min(end, len(sec)):
            st, epid = sec[i], ((sec[i + 1] & 0x1F) << 8) | sec[i + 2]
            if st in (0x1B, 0x24) and self.video_pid is None:
                self.video_pid = epid
            elif st == 0x86:
                self.scte_pid = epid
            i += 5 + (((sec[i + 3] & 0x0F) << 8) | sec[i + 4])

    @staticmethod
    def _pes_info(pl: bytes) -> tuple[int | None, bool]:
        if len(pl) < 14 or pl[0:3] != b"\x00\x00\x01":
            return None, False
        pts = _pts(pl[9:14]) if pl[7] & 0x80 else None
        es = pl[9 + pl[8]:]
        key = False
        j = es.find(b"\x00\x00\x01")
        while j != -1 and j + 3 < len(es):
            nal = es[j + 3] & 0x1F
            if nal in (5, 7):                     # IDR or SPS (sent before each IDR)
                key = True
                break
            j = es.find(b"\x00\x00\x01", j + 3)
        return pts, key

    def _unwrap(self, pts: int) -> int:
        if self._last_pts is not None and pts < self._last_pts - WRAP // 2:
            self._wraps += 1
        self._last_pts = pts
        return pts + self._wraps * WRAP

    def _scte(self, pk: bytes, pusi: bool) -> None:
        pl = self._payload(pk)
        if pusi:
            self._scte_buf = pl[1 + pl[0]:]
        else:
            self._scte_buf += pl
        sec = self._scte_buf
        if len(sec) < 3:
            return
        length = 3 + (((sec[1] & 0x0F) << 8) | sec[2])
        if len(sec) < length:
            return
        sec, self._scte_buf = sec[:length], b""
        ev = parse_splice(sec)
        if ev is None:
            return
        ev["hex"] = "0x" + sec.hex().upper()
        if ev["pts"] is not None:
            base = self._last_pts if self._last_pts is not None else ev["pts"]
            ev["pts"] = ev["pts"] + self._wraps * WRAP
            if ev["pts"] < base - WRAP // 2:
                ev["pts"] += WRAP
        else:
            ev["pts"] = self._last_pts or 0
        self.pending.append(ev)
        self.pending.sort(key=lambda e: e["pts"])

    # ------------------------------------------------------------ segmentation
    def _keyframe(self, pts: int) -> None:
        if self.pat_pkt is None or self.pmt_pkt is None:
            return
        due = [c for c in self.pending if c["pts"] <= pts + PTS_HZ // 50]   # 20 ms tolerance
        if self.cur is None:
            self._open(pts, due)
            return
        dur = (pts - self.cur["pts"]) / PTS_HZ
        if due and dur > 0.2:
            self._close(pts)
            self._open(pts, due)
        elif dur >= self.target - 0.02:
            self._close(pts)
            self._open(pts, [])

    def _open(self, pts: int, cues: list[dict]) -> None:
        for c in cues:
            self.pending.remove(c)
        now = self.clock()
        if self._pdt_next is None or abs(self._pdt_next - now) > 2.0:
            self._pdt_next = now                  # (re)anchor program date time to the wall clock
        self.seq += 1
        name = f"seg_{self.seq}.ts"
        self._fh = open(self.dir / name, "wb")
        self._fh.write(self.pat_pkt + self.pmt_pkt)   # every segment starts decodable
        self.cur = {"seq": self.seq, "file": name, "pts": pts, "pdt": self._pdt_next,
                    "bytes": 376, "tags": [], "cues": cues}

    def _close(self, end_pts: int) -> None:
        seg = self.cur
        self._fh.close()
        self._fh = None
        seg["duration"] = (end_pts - seg["pts"]) / PTS_HZ
        self._pdt_next = seg["pdt"] + seg["duration"]
        self._apply_cues(seg)
        self.segments.append(seg)
        self.cur = None
        self.notify(event="segment", seq=seg["seq"], duration=round(seg["duration"], 3),
                    bytes=seg["bytes"], in_break=self.brk is not None)
        self._write_playlists()
        self._prune()

    def _apply_cues(self, seg: dict) -> None:
        """Turn SCTE-35 events (and the open break) into playlist tags for this segment."""
        tags = seg["tags"]
        for c in seg["cues"]:
            if c["out"]:
                if self.brk is not None:          # a new break replaces an open one
                    self._end_break(seg, tags, None)
                dur = c["duration"] or 0
                self.brk = {"id": c["id"], "duration": dur, "start": seg["pdt"], "elapsed": 0.0}
                tags.append(f'#EXT-X-DATERANGE:ID="splice-{c["id"]}",START-DATE="{_iso(seg["pdt"])}",'
                            f'PLANNED-DURATION={dur:.3f},SCTE35-OUT={c["hex"]}')
                tags.append(f"#EXT-X-CUE-OUT:DURATION={dur:.3f}")
                self.cues += 1
                self.notify(event="cue", type="out", id=c["id"], duration=dur, seq=seg["seq"])
            elif self.brk is not None:
                self._end_break(seg, tags, c)
        if self.brk is not None and not any(t.startswith("#EXT-X-CUE-OUT:") for t in tags):
            elapsed = seg["pdt"] - self.brk["start"]
            if self.brk["duration"] and elapsed >= self.brk["duration"] - 0.05:
                self._end_break(seg, tags, None)          # no IN received: auto-return
            else:
                tags.append(f'#EXT-X-CUE-OUT-CONT:ElapsedTime={elapsed:.3f},Duration={self.brk["duration"]:.3f}')

    def _end_break(self, seg: dict, tags: list[str], cue: dict | None) -> None:
        b, self.brk = self.brk, None
        actual = seg["pdt"] - b["start"]
        extra = f",SCTE35-IN={cue['hex']}" if cue else ""
        tags.append(f'#EXT-X-DATERANGE:ID="splice-{b["id"]}",START-DATE="{_iso(b["start"])}",'
                    f'END-DATE="{_iso(seg["pdt"])}",DURATION={actual:.3f}{extra}')
        tags.append("#EXT-X-CUE-IN")
        self.cues += 1
        self.notify(event="cue", type="in", id=b["id"], seq=seg["seq"], actual=round(actual, 3),
                    auto=cue is None)

    # ------------------------------------------------------------ playlists
    def _atomic(self, name: str, text: str) -> None:
        tmp = self.dir / (name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, self.dir / name)

    def _write_playlists(self) -> None:
        live = self.segments[-self.window:]
        if self._write_master_needed:
            kbps = sum(s["bytes"] for s in live) * 8 / max(0.001, sum(s["duration"] for s in live)) / 1000
            bw = int(max(kbps, 1000) * 1000 * 1.2)
            self._atomic("master.m3u8", "\n".join([
                "#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS",
                f'#EXT-X-STREAM-INF:BANDWIDTH={bw},AVERAGE-BANDWIDTH={int(bw / 1.2)},'
                'CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1920x1080,FRAME-RATE=25.000',
                "stream.m3u8", ""]))
            self._write_master_needed = len(self.segments) < 3   # refine bandwidth from the first segments
        target = max(math.ceil(max(s["duration"] for s in live)), math.ceil(self.target))
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}",
                 f"#EXT-X-MEDIA-SEQUENCE:{live[0]['seq']}"]
        for s in live:
            lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{_iso(s['pdt'])}")
            lines.extend(s["tags"])
            lines.append(f"#EXTINF:{s['duration']:.3f},")
            lines.append(s["file"])
        self._atomic("stream.m3u8", "\n".join(lines) + "\n")

    def _prune(self) -> None:
        keep = self.window + 6                     # players may still be fetching recent ones
        for s in self.segments[:-keep]:
            try:
                (self.dir / s["file"]).unlink()
            except FileNotFoundError:
                pass
        self.segments = self.segments[-keep:]

    def stats(self) -> dict:
        last = self.segments[-1] if self.segments else None
        return {"segments": len(self.segments), "last_seq": last["seq"] if last else None,
                "last_duration": round(last["duration"], 3) if last else None,
                "in_break": self.brk is not None, "cues": self.cues}


def main(argv: list[str]) -> int:
    port, out = int(argv[1]), Path(argv[2])
    seg = float(argv[3]) if len(argv) > 3 else 6.0
    window = int(argv[4]) if len(argv) > 4 else 10
    pk = Packager(out, seg, window)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    s.bind(("127.0.0.1", port))
    emit(event="started", port=port, dir=str(out), segment_s=seg, window=window)
    while True:
        data = s.recv(65536)
        try:
            pk.feed(data)
        except Exception as e:  # noqa: BLE001 - never die on one bad datagram
            emit(event="error", msg=repr(e))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
