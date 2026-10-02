#!/usr/bin/env python3
"""Print SCTE-35 splice commands found in an MPEG-TS file or a live SRT stream.

  scripts/scte_sniff.py recording.ts
  ffmpeg -i "srt://127.0.0.1:9000?mode=caller" -map 0 -c copy -f mpegts - | scripts/scte_sniff.py -
"""
import sys

PID = int(sys.argv[2]) if len(sys.argv) > 2 else 500


def pts(b: bytes) -> float:
    v = ((b[0] & 1) << 32) | (b[1] << 24) | (b[2] << 16) | (b[3] << 8) | b[4]
    return v / 90000


def parse(sec: bytes) -> str | None:
    cmd = sec[13]
    if cmd == 0:
        return None                      # splice_null heartbeat
    if cmd != 5:
        return f"splice command 0x{cmd:02x}"
    p = sec[14:]
    event_id = int.from_bytes(p[0:4], "big")
    if p[4] & 0x80:
        return f"splice_insert CANCEL event={event_id}"
    flags = p[5]
    out = bool(flags & 0x80)
    immediate = bool(flags & 0x10)
    dur_flag = bool(flags & 0x20)
    i = 6
    when = "immediate"
    if not immediate and flags & 0x40:   # program splice
        if p[i] & 0x80:
            when = f"pts={pts(p[i:i + 5]):.3f}"
            i += 5
        else:
            i += 1
    txt = f"splice_insert {'OUT' if out else 'IN '} event={event_id} {when}"
    if dur_flag:
        auto = bool(p[i] & 0x80)
        txt += f" duration={pts(p[i:i + 5]):.1f}s auto_return={int(auto)}"
    return txt


def main() -> None:
    f = sys.stdin.buffer if sys.argv[1] == "-" else open(sys.argv[1], "rb")
    nulls = 0
    while True:
        pk = f.read(188)
        if len(pk) < 188:
            break
        if pk[0] != 0x47:
            continue
        pid = ((pk[1] & 0x1F) << 8) | pk[2]
        if pid != PID or not pk[1] & 0x40:
            continue
        payload = pk[4:]
        if pk[3] & 0x20:                 # adaptation field present
            payload = pk[5 + pk[4]:]
        sec = payload[1 + payload[0]:]
        if sec[0] != 0xFC:
            continue
        r = parse(sec)
        if r is None:
            nulls += 1
        else:
            print(r, flush=True)
    print(f"({nulls} splice_null heartbeats)")


if __name__ == "__main__":
    main()
