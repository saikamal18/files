"""Background music: pick a track from the library (or a generated ambient pad) for each episode."""
from __future__ import annotations

import array
import math
import wave
import zlib
from pathlib import Path

from .config import ROOT, Config

EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
LIBRARY = ROOT / "assets" / "music"
AMBIENT_NAME = "_ambient_pad.wav"


def library_dir(cfg: Config) -> Path:
    return Path(cfg.music_dir).expanduser() if cfg.music_dir else LIBRARY


def library(cfg: Config) -> list[Path]:
    d = library_dir(cfg)
    if not d.exists():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in EXTS and not p.name.startswith((".", "_")))


def pick(cfg: Config, ep_id: str) -> Path | None:
    """AWN_MUSIC=off -> none. Otherwise AWN_MUSIC_FILE wins, then a stable pick from the library (auto mode),
    then the generated ambient pad (always in ambient mode, and as the fallback in auto mode)."""
    if cfg.music_mode == "off":
        return None
    if cfg.music_file:
        f = Path(cfg.music_file).expanduser()
        if f.exists():
            return f
    tracks = library(cfg) if cfg.music_mode != "ambient" else []
    if tracks:
        return tracks[zlib.crc32(ep_id.encode()) % len(tracks)]
    return ensure_ambient(library_dir(cfg) / AMBIENT_NAME)


# A slow chord loop (Am9 - Fmaj7 - Cmaj9 - G) of soft, slightly detuned sines. Each chord swells in and out, 8 s each,
# so the 32 s loop repeats without a seam.
_CHORDS = [
    [110.00, 164.81, 220.00, 261.63, 329.63, 493.88],
    [87.31, 130.81, 174.61, 220.00, 329.63],
    [130.81, 196.00, 329.63, 493.88, 587.33],
    [98.00, 146.83, 196.00, 246.94, 293.66],
]
_SR = 11025
_LOOP_S = 32.0


def ensure_ambient(path: Path) -> Path:
    if path.exists() and path.stat().st_size > 100_000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(_SR * _LOOP_S)
    half = int(_SR * _LOOP_S / 4)  # each chord's window reaches 8 s either side of its centre
    two_pi = 2 * math.pi
    mix = [0.0] * n
    for ci, chord in enumerate(_CHORDS):
        centre = int(_SR * (ci * _LOOP_S / 4 + _LOOP_S / 8))
        env = [math.cos(math.pi * k / (2 * half)) ** 2 for k in range(-half, half)]
        for f in chord:
            for det, amp in ((1.0, 1.0), (1.004, 0.7)):
                w1, w2 = two_pi * f * det / _SR, two_pi * f * det * 2 / _SR
                g = amp / (len(chord) * 1.5)
                for j, e in enumerate(env):
                    u = centre - half + j  # unwrapped sample index keeps the tone continuous across the loop seam
                    mix[u % n] += e * g * (math.sin(w1 * u) + 0.25 * math.sin(w2 * u))
    peak = max(abs(v) for v in mix) or 1.0
    data = array.array("h", (int(v / peak * 0.6 * 32767) for v in mix))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SR)
        w.writeframes(data.tobytes())
    return path
