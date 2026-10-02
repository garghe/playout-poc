import numpy as np

from playout.engine.loudness import LoudnessMeter


def sine(dbfs: float, secs: float = 4.0, f: float = 997.0) -> np.ndarray:
    t = np.arange(int(48000 * secs)) / 48000
    x = (10 ** (dbfs / 20) * np.sin(2 * np.pi * f * t)).astype(np.float32)
    return np.stack([x, x], axis=1)


def test_stereo_sine_reads_its_level():
    # EBU Tech 3341: 1 kHz stereo sine at -20 dBFS reads -20 LUFS (+-0.1)
    m = LoudnessMeter()
    sig = sine(-20)
    for i in range(0, len(sig), 960):          # feed in 20 ms chunks like the engine does
        m.push(sig[i:i + 960])
    assert abs(m.momentary + 20) < 0.2
    assert abs(m.short_term + 20) < 0.2
    assert abs(m.integrated + 20) < 0.2


def test_silence_is_gated():
    m = LoudnessMeter()
    m.push(np.zeros((48000 * 4, 2), dtype=np.float32))
    assert m.integrated == -np.inf
