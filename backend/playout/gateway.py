"""SRT gateway, run as a separate process by the engine.

Keeping libsrt out of the main process isolates it: a lock-up or crash in the
SRT stack cannot take playout down, and the engine simply restarts the gateway.

  python -m playout.gateway out <udp_port> <srt_uri>   # localhost UDP (MPEG-TS) -> SRT
  python -m playout.gateway in  <srt_uri> <udp_port>   # SRT -> localhost UDP (MPEG-TS)

Prints one JSON object per line on stdout: {"event": "caller"|"stats"|"error"|"eos", ...}
"""
from __future__ import annotations

import json
import signal
import sys

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

STATS_KEYS = ("rtt-ms", "bandwidth-mbps", "send-rate-mbps", "receive-rate-mbps", "packets-sent",
              "packets-sent-lost", "packets-retransmitted", "packets-received", "packets-received-lost",
              "packets-received-dropped", "bytes-sent", "bytes-received")


def emit(**kw) -> None:
    print(json.dumps(kw), flush=True)


def _num(v):
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def stats_of(s: Gst.Structure | None) -> dict:
    """Flatten srt stats (srtsink in listener mode nests per-caller structures)."""
    if s is None:
        return {}
    out = {k: _num(s.get_value(k)) for k in STATS_KEYS if s.has_field(k)}
    if s.has_field("callers"):
        callers = s.get_value("callers")
        try:
            items = list(callers)
        except TypeError:
            items = []
        out["callers"] = [{k: _num(c.get_value(k)) for k in STATS_KEYS if c.has_field(k)} for c in items]
    return {k: v for k, v in out.items() if v is not None}


def main(argv: list[str]) -> int:
    Gst.init(None)
    mode = argv[1]
    if mode == "out":
        port, uri = int(argv[2]), argv[3]
        desc = (f"udpsrc port={port} address=127.0.0.1 buffer-size=8388608 "
                "caps=\"video/mpegts,systemstream=(boolean)true,packetsize=(int)188\" ! "
                f"queue max-size-time=500000000 ! srtsink name=s uri=\"{uri}\" wait-for-connection=false "
                "latency=200 sync=false")
    elif mode == "in":
        uri, port = argv[2], int(argv[3])
        desc = (f"srtsrc name=s uri=\"{uri}\" latency=200 ! "
                f"udpsink host=127.0.0.1 port={port} sync=false buffer-size=8388608")
    else:
        print(__doc__, file=sys.stderr)
        return 2

    pipeline = Gst.parse_launch(desc)
    srt = pipeline.get_by_name("s")
    loop = GLib.MainLoop()
    rc = {"code": 0}

    def on_caller(_el, _unused, addr, action):
        where = ""
        try:
            where = f"{addr.get_address().to_string()}:{addr.get_port()}"
        except Exception:  # noqa: BLE001
            pass
        emit(event="caller", action=action, addr=where)

    for sig, action in (("caller-added", "connected"), ("caller-removed", "disconnected")):
        try:
            srt.connect(sig, on_caller, action)
        except TypeError:
            pass

    def on_msg(_bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, _ = msg.parse_error()
            emit(event="error", msg=err.message)
            rc["code"] = 1
            loop.quit()
        elif msg.type == Gst.MessageType.EOS:
            emit(event="eos")
            loop.quit()
        elif msg.type == Gst.MessageType.WARNING:
            w, _ = msg.parse_warning()
            emit(event="warning", msg=w.message)
            if mode == "in" and "SRT socket" in w.message:
                # caller went away: exit so the engine restarts a clean listener
                loop.quit()

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_msg)

    def tick():
        emit(event="stats", stats=stats_of(srt.get_property("stats")))
        return True

    GLib.timeout_add(1000, tick)
    for s in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, s, lambda *a: (loop.quit(), False)[1])
    pipeline.set_state(Gst.State.PLAYING)
    emit(event="started", mode=mode, desc=desc)
    loop.run()
    pipeline.set_state(Gst.State.NULL)
    return rc["code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
