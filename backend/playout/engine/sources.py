"""Playout sources. Each source is its own GStreamer pipeline that decodes and
normalises to the house format (1080p25 I420 / 48 kHz stereo F32) and hands
frames to the ROUTER, which only forwards the on-air source to the output.

* FileSource  - one per playlist file; prerolled (PAUSED) ahead of time, PLAYING = on air.
* LiveSource  - persistent SRT input; always decoding (so signal can be monitored).
* SlateSource - persistent fallback/filler (bars + caption + clock).
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np

from ..config import CONFIG
from .gst import GLib, Gst
from .router import ROUTER
from .srtgw import Gateway

VIDEO_CAPS = (f"video/x-raw,format=I420,width={CONFIG.width},height={CONFIG.height},"
              f"framerate={CONFIG.fps}/1,pixel-aspect-ratio=1/1,interlace-mode=progressive")
AUDIO_CAPS = "audio/x-raw,format=F32LE,rate=48000,channels=2,layout=interleaved"

Notify = Callable[[str, str, str], None]   # (cat, msg, level)


def _chain(factories: list[tuple[str, dict]]) -> tuple[Gst.Bin, Gst.Element]:
    """Build a bin of linked elements (ghost sink pad) ending in an appsink.

    Built element by element: gst_parse is not safe to call from several
    streaming threads at once (pad-added fires on decoder threads).
    """
    b = Gst.Bin()
    els = []
    for fac, props in factories:
        e = Gst.ElementFactory.make(fac)
        for k, v in props.items():
            e.set_property(k, v)
        b.add(e)
        if els:
            els[-1].link(e)
        els.append(e)
    b.add_pad(Gst.GhostPad.new("sink", els[0].get_static_pad("sink")))
    return b, els[-1]


def _appsink(caps: str, sync: bool) -> tuple[str, dict]:
    return ("appsink", {"caps": Gst.Caps.from_string(caps), "sync": sync, "emit-signals": True,
                        "max-buffers": 3, "drop": not sync, "enable-last-sample": False})


def video_chain(sync: bool) -> tuple[Gst.Bin, Gst.Element]:
    return _chain([
        ("queue", {"max-size-time": 500_000_000}), ("deinterlace", {}), ("videoconvert", {}),
        ("videoscale", {"add-borders": True}), ("videorate", {}),
        ("capsfilter", {"caps": Gst.Caps.from_string(VIDEO_CAPS)}), _appsink(VIDEO_CAPS, sync),
    ])


def audio_chain(sync: bool) -> tuple[Gst.Bin, Gst.Element]:
    return _chain([
        ("queue", {"max-size-time": 500_000_000}), ("audioconvert", {}), ("audioresample", {}),
        ("capsfilter", {"caps": Gst.Caps.from_string(AUDIO_CAPS)}), _appsink(AUDIO_CAPS, sync),
    ])


class Source:
    kind = "source"

    def __init__(self, name: str, notify: Notify) -> None:
        self.name = name
        self.notify = notify
        self.pipeline: Gst.Pipeline | None = None
        self.on_eos: Callable[["Source"], None] | None = None
        self.on_error: Callable[["Source", str], None] | None = None
        self._bus_id = None
        self.last_video = 0.0

    # ---- router plumbing
    @property
    def on_air(self) -> bool:
        return ROUTER.active is self

    def set_on_air(self, on: bool) -> None:
        if on:
            ROUTER.set_active(self)
        else:
            ROUTER.release(self)

    def _attach_sink(self, sink: Gst.Element, video: bool) -> None:
        sink.connect("new-sample", self._on_video if video else self._on_audio)

    def _on_video(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is not None:
            self.last_video = time.time()
            ROUTER.video_in(self, sample.get_buffer())
        return Gst.FlowReturn.OK

    def _on_audio(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is not None and ROUTER.active is self:
            buf = sample.get_buffer()
            ok, m = buf.map(Gst.MapFlags.READ)
            if ok:
                a = np.frombuffer(m.data, dtype=np.float32).reshape(-1, 2).copy()
                buf.unmap(m)
                ROUTER.audio_in(self, a)
        return Gst.FlowReturn.OK

    def _add_chain(self, media: str, sync: bool) -> Gst.Bin | None:
        if media.startswith("video/"):
            chain, sink = video_chain(sync)
            self._attach_sink(sink, True)
        elif media.startswith("audio/"):
            chain, sink = audio_chain(sync)
            self._attach_sink(sink, False)
        else:
            return None
        self.pipeline.add(chain)
        chain.sync_state_with_parent()
        return chain

    # ---- bus
    def _watch_bus(self) -> None:
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        self._bus_id = bus.connect("message", self._on_message)

    def _on_message(self, _bus, msg: Gst.Message) -> None:
        if self.pipeline is None:
            return
        t = msg.type
        if t == Gst.MessageType.EOS:
            self.handle_eos()
        elif t == Gst.MessageType.ERROR:
            err, _dbg = msg.parse_error()
            self.handle_error(err.message)
        elif t == Gst.MessageType.WARNING:
            w, _ = msg.parse_warning()
            self.notify("PIPELINE", f"{self.name}: warning: {w.message}", "warn")
        elif t == Gst.MessageType.ASYNC_DONE:
            self.handle_async_done()

    def handle_eos(self) -> None:
        if self.on_eos:
            self.on_eos(self)

    def handle_error(self, message: str) -> None:
        if self.on_error:
            self.on_error(self, message)

    def handle_async_done(self) -> None:
        pass

    def _teardown(self) -> None:
        if self.pipeline is not None:
            bus = self.pipeline.get_bus()
            if self._bus_id:
                bus.disconnect(self._bus_id)
                self._bus_id = None
            bus.remove_signal_watch()
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None

    def destroy(self) -> None:
        ROUTER.release(self)
        self._teardown()


class FileSource(Source):
    kind = "file"

    def __init__(self, name: str, uri: str, in_point: float, notify: Notify) -> None:
        super().__init__(name, notify)
        self.uri = uri
        self.in_point = in_point
        self.prerolled = False
        self._seeked = in_point <= 0
        p = Gst.Pipeline.new(f"file-{name}")
        dec = Gst.ElementFactory.make("uridecodebin", "dec")
        dec.set_property("uri", uri)
        dec.connect("pad-added", self._on_pad)
        p.add(dec)
        self.pipeline = p
        self._watch_bus()

    def _on_pad(self, _dec, pad: Gst.Pad) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        chain = self._add_chain(caps.get_structure(0).get_name(), sync=True)
        if chain is not None:
            pad.link(chain.get_static_pad("sink"))

    def preroll(self) -> None:
        self.pipeline.set_state(Gst.State.PAUSED)

    def handle_async_done(self) -> None:
        if not self._seeked:
            self._seeked = True
            self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                                      int(self.in_point * Gst.SECOND))
            return
        if not self.prerolled:
            self.prerolled = True
            self.notify("PREROLL", f"[{self.name}] cued", "debug")

    def play(self) -> None:
        self.set_on_air(True)
        self.pipeline.set_state(Gst.State.PLAYING)

    def position(self) -> float | None:
        ok, pos = self.pipeline.query_position(Gst.Format.TIME)
        return pos / Gst.SECOND if ok else None


class LiveSource(Source):
    """A live SRT input. libsrt runs in a gateway subprocess (SRT listener ->
    localhost UDP); this pipeline decodes the UDP MPEG-TS. A new caller
    connection rebuilds the decoder so codec/timestamp changes are clean."""
    kind = "live"

    def __init__(self, name: str, uri: str, udp_port: int, notify: Notify) -> None:
        super().__init__(name, notify)
        self.uri = uri
        self.udp_port = udp_port
        self.bytes_in = 0
        self.kbps = 0.0
        self.gateway = Gateway(name, ["in", uri, str(udp_port)], notify, self._on_gateway_event)
        self._build()

    def _build(self) -> None:
        p = Gst.Pipeline.new(f"live-{self.name}")
        dec = Gst.ElementFactory.make("uridecodebin", "dec")
        dec.set_property("uri", f"udp://127.0.0.1:{self.udp_port}")
        dec.connect("pad-added", self._on_pad)
        dec.connect("source-setup", self._on_source)
        p.add(dec)
        self.pipeline = p
        self._watch_bus()

    def _on_gateway_event(self, ev: dict) -> None:
        if ev.get("event") == "caller" and ev.get("action") == "connected":
            GLib.idle_add(self._restart)

    def _on_source(self, _dec, src: Gst.Element) -> None:
        if src.find_property("buffer-size") is not None:
            src.set_property("buffer-size", 8 * 1024 * 1024)
        pad = src.get_static_pad("src")
        if pad:
            pad.add_probe(Gst.PadProbeType.BUFFER, self._count_bytes)

    def _count_bytes(self, _pad, info):
        buf = info.get_buffer()
        if buf:
            self.bytes_in += buf.get_size()
        return Gst.PadProbeReturn.OK

    def _on_pad(self, _dec, pad: Gst.Pad) -> None:
        media = (pad.get_current_caps() or pad.query_caps(None)).get_structure(0).get_name()
        chain = self._add_chain(media, sync=False)
        if chain is not None:
            pad.link(chain.get_static_pad("sink"))
            self.notify("SRT-IN", f"{self.name}: {media.split('/')[0]} stream decoded", "info")

    @property
    def has_signal(self) -> bool:
        return time.time() - self.last_video < CONFIG.live_loss_s

    def start(self) -> None:
        self.gateway.start()
        self.pipeline.set_state(Gst.State.PLAYING)

    def handle_eos(self) -> None:
        self.notify("SRT-IN", f"{self.name}: decoder EOS, restarting", "warn")
        GLib.timeout_add(500, self._restart)

    def handle_error(self, message: str) -> None:
        self.notify("SRT-IN", f"{self.name}: decoder error: {message}; restarting", "warn")
        GLib.timeout_add(500, self._restart)

    def _restart(self) -> bool:
        # router selection is kept: if this input is on air it stays selected
        self._teardown()
        self._build()
        self.pipeline.set_state(Gst.State.PLAYING)
        return False

    def destroy(self) -> None:
        self.gateway.stop()
        super().destroy()

    def stats(self) -> dict:
        return {"uri": self.uri, "signal": self.has_signal, "bytes_in": self.bytes_in,
                "callers": self.gateway.callers, "gateway_restarts": self.gateway.restarts,
                **self.gateway.summary()}


class SlateSource(Source):
    kind = "slate"

    def __init__(self, notify: Notify) -> None:
        super().__init__("SLATE", notify)
        desc = (
            "videotestsrc is-live=true pattern=smpte100 ! "
            f"video/x-raw,width={CONFIG.width},height={CONFIG.height},framerate={CONFIG.fps}/1 ! "
            "textoverlay name=txt text=\"We'll be right back\" font-desc=\"Sans Bold 48\" "
            "valignment=center halignment=center shaded-background=true ! "
            "clockoverlay time-format=\"%H:%M:%S\" font-desc=\"Sans 28\" valignment=bottom halignment=right ! "
            f"videoconvert ! {VIDEO_CAPS} ! appsink name=v sync=false emit-signals=true max-buffers=2 drop=true "
            f"audiotestsrc is-live=true wave=silence samplesperbuffer=960 ! audioconvert ! {AUDIO_CAPS} ! "
            "appsink name=a sync=false emit-signals=true max-buffers=4 drop=true"
        )
        self.pipeline = Gst.parse_launch(desc)
        self._attach_sink(self.pipeline.get_by_name("v"), True)
        self._attach_sink(self.pipeline.get_by_name("a"), False)
        self._txt = self.pipeline.get_by_name("txt")
        self._watch_bus()

    def set_caption(self, text: str) -> None:
        self._txt.set_property("text", text)

    def start(self) -> None:
        self.pipeline.set_state(Gst.State.PLAYING)
