"""EBU R128 / ITU-R BS.1770 loudness meter (momentary, short-term, integrated)."""
from __future__ import annotations

import math
from collections import deque

import numpy as np
from scipy.signal import sosfilt

# K-weighting for 48 kHz (BS.1770-4): high-shelf pre-filter + RLB high-pass.
_SOS = np.array([
    [1.53512485958697, -2.69169618940638, 1.19839281085285, 1.0, -1.69065929318241, 0.73248077421585],
    [1.0, -2.0, 1.0, 1.0, -1.99004745483398, 0.99007225036621],
])
RATE = 48000
SUB = RATE // 10          # 100 ms sub-blocks


def _lufs(power: float) -> float:
    return -0.691 + 10 * math.log10(power) if power > 0 else -math.inf


class LoudnessMeter:
    def __init__(self, channels: int = 2) -> None:
        self.channels = channels
        self.reset()

    def reset(self) -> None:
        self._zi = np.zeros((self.channels, _SOS.shape[0], 2))
        self._pending = np.zeros((0, self.channels))
        self._subs: deque[float] = deque(maxlen=30)   # last 3 s of 100 ms powers
        self._blocks: list[float] = []                 # 400 ms block powers for integrated

    def push(self, samples: np.ndarray) -> None:
        """samples: float array shaped (n, channels) in [-1, 1]."""
        weighted = np.empty_like(samples, dtype=np.float64)
        for ch in range(self.channels):
            weighted[:, ch], self._zi[ch] = sosfilt(_SOS, samples[:, ch], zi=self._zi[ch])
        buf = np.concatenate([self._pending, weighted]) if len(self._pending) else weighted
        n = len(buf) // SUB
        for i in range(n):
            blk = buf[i * SUB:(i + 1) * SUB]
            self._subs.append(float(np.sum(np.mean(blk * blk, axis=0))))
            if len(self._subs) >= 4:
                p = sum(list(self._subs)[-4:]) / 4
                if _lufs(p) > -70:
                    self._blocks.append(p)
        self._pending = buf[n * SUB:]

    @property
    def momentary(self) -> float:
        s = list(self._subs)[-4:]
        return _lufs(sum(s) / len(s)) if len(s) == 4 else -math.inf

    @property
    def short_term(self) -> float:
        return _lufs(sum(self._subs) / len(self._subs)) if len(self._subs) == 30 else -math.inf

    @property
    def integrated(self) -> float:
        if not self._blocks:
            return -math.inf
        b = np.array(self._blocks)
        rel = _lufs(float(b.mean())) - 10
        gated = b[b > 10 ** ((rel + 0.691) / 10)]
        return _lufs(float(gated.mean())) if len(gated) else -math.inf
