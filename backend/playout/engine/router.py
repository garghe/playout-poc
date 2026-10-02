"""The on-air router: forwards frames/audio from the ONE on-air source into
the output pipeline at a steady 25 fps cadence.

Every source pipeline ends in appsinks that call video_in()/audio_in().
Only the source currently set as `active` is accepted; everything else is
dropped. A clock-paced thread then pushes exactly one video frame and
1920 audio samples (40 ms @ 48 kHz) per tick into the output appsrcs:
  * no new frame   -> repeat the last one (black after 1 s)
  * audio underrun -> pad with silence; overrun -> drop the oldest audio
so cuts are clean and the output never stalls.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

from ..config import CONFIG
from .gst import Gst

SAMPLES_PER_FRAME = 48000 // CONFIG.fps
MAX_AUDIO = 48000 // 4            # keep at most 250 ms queued
FRAME_NS = Gst.SECOND // CONFIG.fps
# Video jitter buffer: frames (esp. from live/network sources) arrive in bursts, so they
# are queued and released one per tick. Start releasing once PRIME frames are queued
# after a switch (80 ms); never hold more than MAX_QUEUE (bounded latency / drift).
PRIME = 2
MAX_QUEUE = 6


def _black_frame() -> Gst.Buffer:
    w, h = CONFIG.width, CONFIG.height
    data = bytes([16]) * (w * h) + bytes([128]) * (w * h // 2)
    return Gst.Buffer.new_wrapped(data)


class Router:
    def __init__(self) -> None:
        self.active: object | None = None     # chosen by automation
        self.override: object | None = None   # emergency slate: beats `active` while set
        self._lock = threading.Lock()
        self._frame: Gst.Buffer | None = None      # last frame shown (repeated on underrun)
        self._frame_t = 0.0                         # when the last new frame arrived
        self._queue: deque[Gst.Buffer] = deque()
        self._primed = False
        self._a_primed = False     # audio: start consuming once PRIME frames' worth is queued
        self._audio = np.zeros((0, 2), dtype=np.float32)
        self._black = _black_frame()
        self._vsrc = self._asrc = self._pipeline = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.repeated = 0
        self.dropped = 0
        self.underruns = 0

    # ---- inputs (called from source streaming threads)
    def set_active(self, token: object | None) -> None:
        with self._lock:
            if token is not self.active:
                self._reset_video()
            self.active = token
            self._audio = self._audio[-SAMPLES_PER_FRAME * 2:]  # keep ~80 ms to avoid a hard gap
            self._a_primed = False

    def release(self, token: object) -> None:
        with self._lock:
            if self.active is token:
                self.active = None

    @property
    def on_air(self) -> object | None:
        return self.override or self.active

    def set_override(self, token: object | None) -> None:
        with self._lock:
            if token is not self.override:
                self._reset_video()
            self.override = token
            self._audio = self._audio[:0]
            self._a_primed = False

    def _reset_video(self) -> None:
        # a cut: drop frames queued from the previous source (lock held by caller)
        self._queue.clear()
        self._primed = False

    def video_in(self, token: object, buf: Gst.Buffer) -> None:
        if token is not (self.override or self.active):
            return
        with self._lock:
            if token is (self.override or self.active):
                self._queue.append(buf)
                self._frame_t = time.monotonic()
                while len(self._queue) > MAX_QUEUE:
                    self._queue.popleft()
                    self.dropped += 1

    def audio_in(self, token: object, samples: np.ndarray) -> None:
        if token is not (self.override or self.active):
            return
        with self._lock:
            if token is (self.override or self.active):
                a = np.concatenate([self._audio, samples]) if len(self._audio) else samples
                self._audio = a[-MAX_AUDIO:]

    # ---- output side
    def attach(self, pipeline: Gst.Pipeline, vsrc: Gst.Element, asrc: Gst.Element) -> None:
        self._pipeline, self._vsrc, self._asrc = pipeline, vsrc, asrc

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, name="router", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        clock = None
        while clock is None and self._running:
            clock = self._pipeline.get_clock()
            if clock is None:
                time.sleep(0.01)
        base = self._pipeline.get_base_time()
        start = clock.get_time() - base
        n = 0
        while self._running:
            pts = start + n * FRAME_NS
            wait = (pts + base) - clock.get_time()
            if wait > 0:
                time.sleep(wait / Gst.SECOND)
            elif wait < -FRAME_NS * 5:
                # we fell far behind (e.g. machine stall): resync instead of bursting
                start = clock.get_time() - base
                n = 0
                continue
            self._push(pts)
            n += 1

    def _push(self, pts: int) -> None:
        with self._lock:
            if not self._primed and len(self._queue) >= PRIME:
                self._primed = True
            if self._primed and self._queue:
                self._frame = self._queue.popleft()
            else:
                if self._primed:
                    self._primed = False    # ran dry: re-prime so we absorb the next burst
                if self._frame is not None and self.on_air is not None:
                    self.repeated += 1
            frame = self._frame
            if frame is None or time.monotonic() - self._frame_t > 1.0:
                frame = self._black
            if not self._a_primed and len(self._audio) >= SAMPLES_PER_FRAME * PRIME:
                self._a_primed = True
            if self._a_primed:
                a = self._audio[:SAMPLES_PER_FRAME]
                self._audio = self._audio[SAMPLES_PER_FRAME:]
                if len(a) < SAMPLES_PER_FRAME:          # ran dry: pad and re-prime
                    self._a_primed = False
                    if self.on_air is not None:
                        self.underruns += 1
            else:
                a = self._audio[:0]
        if len(a) < SAMPLES_PER_FRAME:
            a = np.concatenate([a, np.zeros((SAMPLES_PER_FRAME - len(a), 2), dtype=np.float32)])
        v = frame.copy()          # shallow: shares memory, new timestamps
        v.pts = v.dts = pts
        v.duration = FRAME_NS
        ab = Gst.Buffer.new_wrapped(np.ascontiguousarray(a, dtype=np.float32).tobytes())
        ab.pts = ab.dts = pts
        ab.duration = FRAME_NS
        self._vsrc.emit("push-buffer", v)
        self._asrc.emit("push-buffer", ab)


ROUTER = Router()
