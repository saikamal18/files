"""Tiny procedural sound design (no sample files, so nothing to license): whooshes and a soft hit, mixed under the voice."""
from __future__ import annotations

import array
import math
import random
import wave
from pathlib import Path

SR = 44100


def whoosh(length: float = 0.55, seed: int = 7) -> list[float]:
    rng = random.Random(seed)
    n = int(length * SR)
    peak_at = 0.62
    y = z = 0.0
    out = []
    for i in range(n):
        x = i / n
        a = 0.015 + 0.30 * (x ** 1.6)  # low-pass cutoff sweeps upward
        y += a * (rng.uniform(-1, 1) - y)
        z += 0.004 * (y - z)  # strip the rumble
        env = (x / peak_at) ** 1.7 if x < peak_at else ((1 - x) / (1 - peak_at)) ** 1.3
        out.append((y - z) * env)
    top = max(abs(v) for v in out) or 1.0
    return [v / top * 0.9 for v in out]


def hit(length: float = 0.38) -> list[float]:
    n = int(length * SR)
    out, phase = [], 0.0
    for i in range(n):
        t = i / SR
        f = 48 + 70 * math.exp(-t * 22)  # quick pitch drop for a soft, low thump
        phase += 2 * math.pi * f / SR
        env = math.exp(-t * 11) * min(1.0, t / 0.004)
        out.append(math.sin(phase) * env * 0.95)
    return out


def build_track(events: list[tuple[float, str]], total: float, path: Path) -> Path | None:
    """events: [(cut_time_s, 'whoosh'|'hit')]. Whooshes peak on the cut. Returns None when there is nothing to play."""
    if not events:
        return None
    n = int((total + 0.5) * SR)
    mix = [0.0] * n
    sounds = {"whoosh": (whoosh(), 0.62), "hit": (hit(), 0.0)}
    for t, kind in events:
        samples, peak_at = sounds[kind]
        start = int((t - peak_at * len(samples) / SR) * SR)
        for i, v in enumerate(samples):
            j = start + i
            if 0 <= j < n:
                mix[j] += v
    data = array.array("h", (max(-32767, min(32767, int(v * 30000))) for v in mix))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return path
