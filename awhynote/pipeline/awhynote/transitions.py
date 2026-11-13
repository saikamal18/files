"""Frame transitions between two shots. `choose` picks the kind for a cut, `blend` renders one frame of it."""
from __future__ import annotations

from PIL import Image

from . import visuals as V

SOUND_KINDS = {"push": "whoosh", "zoom": "whoosh", "flash": "hit"}
LENGTH_S = {"fade": 0.30, "push": 0.28, "zoom": 0.32, "flash": 0.24, "dip": 0.34}
SAME_SHOT_FADE_S = 0.18
WARM_WHITE = (255, 247, 228)


def choose(mode: str, nxt: dict, same_shot: bool, index: int, last: bool) -> str | None:
    """Kind of transition for the cut into `nxt` (a dict with 'style' and 'vtype'), or None for a hard cut."""
    if mode == "cut":
        return None
    if mode == "fade" or last:
        return "fade"
    if same_shot:
        return "fade"
    vtype, style = nxt.get("vtype", ""), nxt.get("style", "")
    if style == "text" or vtype in ("text", "stat"):
        return "flash"
    if vtype == "archival" or style == "card":
        return "dip" if index % 2 else "fade"
    if vtype == "map":
        return "push"
    if vtype == "macro":
        return "zoom"
    return ("push", "fade", "zoom")[index % 3]


def length(kind: str, same_shot: bool = False) -> float:
    return SAME_SHOT_FADE_S if same_shot and kind == "fade" else LENGTH_S[kind]


def _ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def _zoom(img: Image.Image, z: float) -> Image.Image:
    if abs(z - 1.0) < 1e-3:
        return img
    k = 1 / z
    return img.transform(img.size, Image.AFFINE, (k, 0, img.width * (1 - k) / 2, 0, k, img.height * (1 - k) / 2), Image.BILINEAR)


def _vblur(img: Image.Image, amount: float) -> Image.Image:
    """Cheap vertical motion blur: blend the frame with a vertically squashed copy."""
    if amount < 0.02:
        return img
    soft = img.resize((img.width, max(8, img.height // 14)), Image.BILINEAR).resize(img.size, Image.BILINEAR)
    return Image.blend(img, soft, min(0.85, amount))


def blend(kind: str, a: Image.Image, b: Image.Image, p: float) -> Image.Image:
    """One frame of the transition from `a` to `b`; p runs 0..1 across the transition window."""
    e = _ease(p)
    w, h = a.size
    if kind == "fade":
        return Image.blend(a, b, e)
    if kind == "push":  # the new shot scrolls up and pushes the old one off
        blur = 0.9 * (1 - abs(2 * p - 1))
        a2, b2 = _vblur(a, blur), _vblur(b, blur)
        out = Image.new("RGB", (w, h))
        off = int(e * h)
        out.paste(a2, (0, -off))
        out.paste(b2, (0, h - off))
        return out
    if kind == "zoom":  # punch through the old shot into the new one
        a2 = _zoom(a, 1 + 0.40 * e)
        b2 = _zoom(b, 1.30 - 0.30 * e)
        return Image.blend(a2, b2, _ease((p - 0.15) / 0.7))
    if kind in ("flash", "dip"):
        colour = Image.new("RGB", (w, h), WARM_WHITE if kind == "flash" else (6, 6, 8))
        peak = 0.92 if kind == "flash" else 1.0
        if p < 0.5:
            return Image.blend(a, colour, _ease(p * 2) * peak)
        return Image.blend(b, colour, (1 - _ease((p - 0.5) * 2)) * peak)
    return b if p >= 0.5 else a
