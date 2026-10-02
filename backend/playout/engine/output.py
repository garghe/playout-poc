"""Programme output: reads the "program" inter channel, encodes H.264/AAC into
MPEG-TS (with an SCTE-35 PID) and serves it as an SRT listener. Also produces
the browser preview (JPEG), audio meters, loudness and black detection.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable

import numpy as np

from ..config import CONFIG
from .gst import GLib, Gst, GstMpegts, GstVideo
from .loudness import LoudnessMeter
from .router import ROUTER
from .srtgw import Gateway, HlsPackagerProc
from .sources import AUDIO_CAPS, VIDEO_CAPS, Notify


def _db(x: float) -> float:
    return 20 * math.log10(x) if x > 1e-9 else -120.0


class Output:
    def __init__(self, notify: Notify) -> None:
        self.notify = notify
        c = CONFIG
        desc = (
            f"appsrc name=vsrc is-live=true format=time do-timestamp=false block=false max-bytes=40000000 "
            f"caps=\"{VIDEO_CAPS}\" ! "
            "queue max-size-buffers=3 ! tee name=vt "
            f"vt. ! queue max-size-time=1000000000 ! x264enc name=venc tune=zerolatency speed-preset={c.x264_preset} "
            # fixed 1 s GOP (no scene-cut keyframes) so HLS segments are exactly N seconds;
            # extra keyframes are forced only at SCTE-35 splice points
            f"bitrate={c.video_bitrate_kbps} key-int-max={c.fps} option-string=\"keyint-min={c.fps}:scenecut=0\" ! "
            "video/x-h264,profile=high ! "
            "h264parse config-interval=-1 ! queue ! mux. "
            "vt. ! queue leaky=downstream max-size-buffers=1 ! videorate drop-only=true ! "
            "video/x-raw,framerate=10/1 ! videoscale ! video/x-raw,width=640,height=360 ! "
            "jpegenc quality=70 ! appsink name=preview emit-signals=true sync=false max-buffers=1 drop=true "
            "vt. ! queue leaky=downstream max-size-buffers=1 ! videorate drop-only=true ! "
            "video/x-raw,framerate=5/1 ! videoscale ! video/x-raw,width=64,height=36 ! "
            "appsink name=probe emit-signals=true sync=false max-buffers=1 drop=true "
            f"appsrc name=asrc is-live=true format=time do-timestamp=false block=false "
            f"caps=\"{AUDIO_CAPS}\" ! queue ! tee name=at "
            "at. ! queue max-size-time=1000000000 ! audioconvert ! avenc_aac bitrate=192000 ! aacparse ! queue ! mux. "
            "at. ! queue leaky=downstream max-size-buffers=50 ! "
            "appsink name=audio emit-signals=true sync=false max-buffers=50 drop=true "
            f"mpegtsmux name=mux alignment=7 scte-35-pid={c.scte_pid} scte-35-null-interval=900000 ! "
            "tee name=tst "
            # -> SRT gateway process
            f"tst. ! queue ! udpsink name=out host=127.0.0.1 port={c.udp_base} sync=false buffer-size=8388608 "
            # -> HLS packager process
            f"tst. ! queue ! udpsink host=127.0.0.1 port={c.udp_base + 100} sync=false buffer-size=8388608"
        )
        self.pipeline: Gst.Pipeline = Gst.parse_launch(desc)
        self.mux = self.pipeline.get_by_name("mux")
        ROUTER.attach(self.pipeline, self.pipeline.get_by_name("vsrc"), self.pipeline.get_by_name("asrc"))
        self.srt = self.pipeline.get_by_name("out")
        # SRT itself lives in a separate gateway process (localhost UDP -> SRT listener)
        self.gateway = Gateway("output", ["out", str(c.udp_base), c.output_srt], notify)
        self.hls = HlsPackagerProc(notify)

        self.preview_jpeg: bytes | None = None
        self.preview_seq = 0
        self.luma = 0.0
        self.luma_std = 0.0
        self.loud = LoudnessMeter()
        self._lock = threading.Lock()
        self._peak = [0.0, 0.0]
        self._sumsq = [0.0, 0.0]
        self._n = 0
        self.bytes_out = 0
        self.frames = 0
        self.started = time.time()
        self._scte_q: deque[tuple[GstMpegts.SCTESIT, str]] = deque()
        self._scte_busy = False
        self.on_preview: Callable[[bytes], None] | None = None

        self.pipeline.get_by_name("preview").connect("new-sample", self._on_preview)
        self.pipeline.get_by_name("probe").connect("new-sample", self._on_probe)
        self.pipeline.get_by_name("audio").connect("new-sample", self._on_audio)
        self.srt.get_static_pad("sink").add_probe(
            Gst.PadProbeType.BUFFER | Gst.PadProbeType.BUFFER_LIST, self._count_out)
        self.pipeline.get_by_name("venc").get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, self._count_frame)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::warning", self._on_warning)

    # ---- lifecycle
    def start(self) -> None:
        self.pipeline.set_state(Gst.State.PLAYING)
        ROUTER.start()
        self.gateway.start()
        if CONFIG.hls_enabled:
            self.hls.start()
        self.notify("OUTPUT", f"SRT output listening on {CONFIG.output_srt} (1080p25 H.264 "
                              f"{CONFIG.video_bitrate_kbps} kb/s + AAC stereo, SCTE-35 PID {CONFIG.scte_pid})", "info")

    def stop(self) -> None:
        ROUTER.stop()
        self.gateway.stop()
        self.hls.stop()
        self.pipeline.set_state(Gst.State.NULL)

    # ---- callbacks (streaming threads)
    def _on_error(self, _bus, msg) -> None:
        err, _ = msg.parse_error()
        self.notify("OUTPUT", f"output error: {err.message}", "error")

    def _on_warning(self, _bus, msg) -> None:
        w, _ = msg.parse_warning()
        self.notify("OUTPUT", f"output warning: {w.message}", "warn")

    def _count_out(self, _pad, info):
        if info.type & Gst.PadProbeType.BUFFER_LIST:
            bl = info.get_buffer_list()
            self.bytes_out += sum(bl.get(i).get_size() for i in range(bl.length()))
        else:
            buf = info.get_buffer()
            if buf:
                self.bytes_out += buf.get_size()
        return Gst.PadProbeReturn.OK

    def _count_frame(self, _pad, _info):
        self.frames += 1
        return Gst.PadProbeReturn.OK

    def _on_preview(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        ok, m = buf.map(Gst.MapFlags.READ)
        if ok:
            self.preview_jpeg = bytes(m.data)
            self.preview_seq += 1
            buf.unmap(m)
        return Gst.FlowReturn.OK

    def _on_probe(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        ok, m = buf.map(Gst.MapFlags.READ)
        if ok:
            y = np.frombuffer(m.data, dtype=np.uint8, count=64 * 36)
            self.luma = float(y.mean())
            self.luma_std = float(y.std())
            buf.unmap(m)
        return Gst.FlowReturn.OK

    def _on_audio(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        ok, m = buf.map(Gst.MapFlags.READ)
        if ok:
            a = np.frombuffer(m.data, dtype=np.float32).reshape(-1, 2).copy()
            buf.unmap(m)
            if len(a):
                pk = np.abs(a).max(axis=0)
                sq = (a.astype(np.float64) ** 2).sum(axis=0)
                with self._lock:
                    for ch in (0, 1):
                        self._peak[ch] = max(self._peak[ch], float(pk[ch]))
                        self._sumsq[ch] += float(sq[ch])
                    self._n += len(a)
                    self.loud.push(a)
        return Gst.FlowReturn.OK

    # ---- metering (called by engine ~10 Hz)
    def take_meters(self) -> dict:
        with self._lock:
            n = max(1, self._n)
            peak = [_db(p) for p in self._peak]
            rms = [_db(math.sqrt(s / n)) for s in self._sumsq]
            self._peak = [0.0, 0.0]
            self._sumsq = [0.0, 0.0]
            self._n = 0
            m, s, i = self.loud.momentary, self.loud.short_term, self.loud.integrated
        fin = lambda v: round(v, 1) if math.isfinite(v) else None  # noqa: E731
        return {"peak": [round(x, 1) for x in peak], "rms": [round(x, 1) for x in rms],
                "lufs_m": fin(m), "lufs_s": fin(s), "lufs_i": fin(i)}

    def reset_loudness(self) -> None:
        with self._lock:
            self.loud.reset()

    # ---- SCTE-35
    def running_time(self) -> int:
        clock = self.pipeline.get_clock()
        if clock is None:
            return 0
        return clock.get_time() - self.pipeline.get_base_time()

    def force_keyframe(self, running_time: int) -> None:
        """Ask the encoder for an IDR at this running time (splice points must start a GOP
        so HLS/SSAI can cut a segment exactly there)."""
        ev = GstVideo.video_event_new_upstream_force_key_unit(running_time, True, 0)
        self.pipeline.get_by_name("venc").get_static_pad("src").send_event(ev)

    def splice_out(self, event_id: int, duration_s: float, preroll_s: float = 0.0) -> None:
        at = self.running_time() + int(max(0.1, preroll_s) * Gst.SECOND)
        self.force_keyframe(at)
        sit = GstMpegts.scte_splice_out_new(event_id, at, int(duration_s * Gst.SECOND))
        sit.is_running_time = True
        self._queue_scte(sit, f"splice_insert OUT event={event_id} duration={duration_s:.1f}s "
                              f"preroll={preroll_s:.1f}s")

    def splice_in(self, event_id: int, preroll_s: float = 0.0) -> None:
        at = self.running_time() + int(max(0.1, preroll_s) * Gst.SECOND)
        self.force_keyframe(at)
        sit = GstMpegts.scte_splice_in_new(event_id, at)
        sit.is_running_time = True
        self._queue_scte(sit, f"splice_insert IN event={event_id} preroll={preroll_s:.1f}s")

    def _queue_scte(self, sit, desc: str) -> None:
        # mpegtsmux only holds one pending section, so space sends out.
        self._scte_q.append((sit, desc))
        if not self._scte_busy:
            self._scte_busy = True
            GLib.idle_add(self._send_next_scte)

    def _send_next_scte(self) -> bool:
        if not self._scte_q:
            self._scte_busy = False
            return False
        sit, desc = self._scte_q.popleft()
        sec = GstMpegts.Section.from_scte_sit(sit, CONFIG.scte_pid)
        ok = sec.send_event(self.mux)
        self.notify("SCTE35", f"{desc} -> PID {CONFIG.scte_pid}" + ("" if ok else " (REJECTED by mux)"),
                    "info" if ok else "error")
        GLib.timeout_add(500, self._send_next_scte)
        return False

    def stats(self) -> dict:
        return {"clients": self.gateway.callers, "srt": self.gateway.summary(),
                "hls": self.hls.summary() if CONFIG.hls_enabled else None,
                "gateway_restarts": self.gateway.restarts, "bytes_out": self.bytes_out, "frames": self.frames,
                "luma": round(self.luma, 1), "uptime": round(time.time() - self.started),
                "repeated_frames": ROUTER.repeated, "dropped_frames": ROUTER.dropped,
                "audio_underruns": ROUTER.underruns}
