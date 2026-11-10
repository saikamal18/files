"""Pillow drawing primitives for the awhynote look (see ../04_brand_kit.md)."""
from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from .util import ensure_fonts

W, H = 1080, 1920
INK, PAPER, YELLOW, RED, BROWN = (14, 14, 15), (237, 230, 214), (255, 214, 10), (229, 57, 43), (107, 74, 43)
CANVAS_SCALE = 1.2  # Ken Burns source canvas = 1.2x output, so a 1.0-1.17x window never upsamples

_FONT_FILES = {"head": "ArchivoBlack-Regular.ttf", "serif": "InstrumentSerif-Italic.ttf", "mono": "SpaceMono-Bold.ttf"}
_paths: dict[str, str] | None = None


@lru_cache(maxsize=64)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    global _paths
    if _paths is None:
        _paths = ensure_fonts(download=True)
    return ImageFont.truetype(_paths[_FONT_FILES[kind]], size)


def smooth(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 0.5 * t + 0.5 * (t * t * (3 - 2 * t))


def ink_background(w: int, h: int) -> Image.Image:
    bg = Image.new("RGB", (w, h), INK)
    noise = Image.effect_noise((w, h), 5).convert("RGB")
    return ImageChops.add(bg, noise, 1, -128)


def wrap(text: str, fnt: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if cur and fnt.getlength(trial) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def make_text_card(w: int, h: int, text: str, scale: float = 1.0, cy: float = 0.5) -> Image.Image:
    img = ink_background(w, h)
    d = ImageDraw.Draw(img)
    text = text.upper().strip() or "WHY?"
    max_w = w * 0.84
    size = max(10, int(170 * scale))
    while True:
        fnt = font("head", size)
        lines = wrap(text, fnt, max_w)
        if (len(lines) <= 4 and all(fnt.getlength(l) <= max_w for l in lines)) or size <= 12 * scale:
            break
        size = int(size * 0.92)
    lh = int(size * 1.18)
    y = h * cy - lh * len(lines) / 2 - 30 * scale
    for line in lines:
        x = (w - fnt.getlength(line)) / 2
        pos = x
        for tok in line.split(" "):
            col = YELLOW if any(c.isdigit() for c in tok) else PAPER
            d.text((pos, y), tok, font=fnt, fill=col)
            pos += fnt.getlength(tok + " ")
        y += lh
    bar_w = 300 * scale
    d.rectangle([(w - bar_w) / 2, y + 36 * scale, (w + bar_w) / 2, y + 36 * scale + 10 * scale], fill=YELLOW)
    return img


# -- callouts ----------------------------------------------------------------
def draw_callout(img: Image.Image, x: float, y: float, r: float) -> None:
    """Red ring plus a white arrow, coordinates as fractions of the image."""
    d = ImageDraw.Draw(img)
    cx, cy, rad = x * img.width, y * img.height, max(18, r * img.width)
    lw = max(6, int(img.width * 0.008))
    d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=RED, width=lw)
    ang = math.radians(215 if cx > img.width * 0.4 else 325)
    tail = (cx + math.cos(ang) * rad * 3.0, cy + math.sin(ang) * rad * 3.0)
    tip = (cx + math.cos(ang) * (rad + lw * 2), cy + math.sin(ang) * (rad + lw * 2))
    tail = (min(max(tail[0], 20), img.width - 20), min(max(tail[1], 20), img.height - 20))
    for col, width in (((0, 0, 0), lw + 6), ((255, 255, 255), lw)):
        d.line([tail, tip], fill=col, width=width)
        for da in (-0.5, 0.5):
            a2 = math.atan2(tip[1] - tail[1], tip[0] - tail[0]) + math.pi + da
            d.line([tip, (tip[0] + math.cos(a2) * rad * 0.55, tip[1] + math.sin(a2) * rad * 0.55)], fill=col, width=width)


# -- Ken Burns source canvases ----------------------------------------------
def _canvas_size() -> tuple[int, int]:
    return round(W * CANVAS_SCALE), round(H * CANVAS_SCALE)


def prepare_full(img: Image.Image, callout: dict | None = None) -> tuple[Image.Image, tuple[float, float]]:
    """Full-bleed canvas. Wide photos get a blurred backdrop with the photo fitted to the width."""
    cw, ch = _canvas_size()
    img = img.copy()
    focus = (0.5, 0.5)
    if img.width / img.height > 1.25:
        bg = ImageOps.fit(img, (cw, ch), Image.LANCZOS).filter(ImageFilter.GaussianBlur(28))
        bg = Image.eval(bg, lambda v: int(v * 0.45))
        fg_w = cw
        fg_h = round(img.height * fg_w / img.width)
        fg = img.resize((fg_w, fg_h), Image.LANCZOS)
        if callout:
            draw_callout(fg, callout["x"], callout["y"], callout["r"])
        top = (ch - fg_h) // 2
        bg.paste(fg, (0, top))
        if callout:
            focus = (callout["x"], (top + callout["y"] * fg_h) / ch)
        return bg, focus
    sc = max(cw / img.width, ch / img.height)
    big = img.resize((math.ceil(img.width * sc), math.ceil(img.height * sc)), Image.LANCZOS)
    if callout:
        draw_callout(big, callout["x"], callout["y"], callout["r"])
        fx, fy = callout["x"], callout["y"]
    else:
        fx, fy = 0.5, 0.5
    left = min(max(round(fx * big.width - cw / 2), 0), big.width - cw)
    top = min(max(round(fy * big.height - ch / 2), 0), big.height - ch)
    crop = big.crop((left, top, left + cw, top + ch))
    if callout:
        focus = ((fx * big.width - left) / cw, (fy * big.height - top) / ch)
    return crop, focus


def prepare_card(img: Image.Image, callout: dict | None = None) -> tuple[Image.Image, tuple[float, float]]:
    """Photo as a bordered print, slightly rotated, on the dark textured background (archival look)."""
    cw, ch = _canvas_size()
    bg = ink_background(cw, ch)
    photo = img.copy()
    if callout:
        draw_callout(photo, callout["x"], callout["y"], callout["r"])
    max_w, max_h = cw * 0.80, ch * 0.36
    s = min(max_w / photo.width, max_h / photo.height)
    photo = photo.resize((round(photo.width * s), round(photo.height * s)), Image.LANCZOS)
    b = round(26 * CANVAS_SCALE)
    framed = Image.new("RGB", (photo.width + 2 * b, photo.height + 2 * b), PAPER)
    framed.paste(photo, (b, b))
    framed = framed.convert("RGBA")
    rot = framed.rotate(-2.0, resample=Image.BICUBIC, expand=True)
    shadow = Image.new("RGBA", rot.size, (0, 0, 0, 0))
    shadow.putalpha(rot.getchannel("A").point(lambda v: int(v * 0.55)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    px, py = (cw - rot.width) // 2, int(ch * 0.378 - rot.height / 2)
    bg.paste(shadow, (px + 14, py + 22), shadow)
    bg.paste(rot, (px, py), rot)
    focus = (0.5, 0.378)
    if callout:
        focus = ((px + rot.width / 2 + (callout["x"] - 0.5) * photo.width) / cw, (py + rot.height / 2 + (callout["y"] - 0.5) * photo.height) / ch)
    return bg, focus


def prepare_text(text: str) -> tuple[Image.Image, tuple[float, float]]:
    cw, ch = _canvas_size()
    # centred in the upper area so it never sits under the captions (captions live around y=1260)
    return make_text_card(cw, ch, text, scale=CANVAS_SCALE, cy=0.36), (0.5, 0.36)


class KenBurns:
    """Sub-pixel push/pull/pan over a pre-built canvas using one affine transform per frame."""

    PRESETS = ["push", "pan_lr", "pull", "focus", "pan_rl", "drift_up", "push_br"]
    GENTLE = ["push", "pull"]  # for cards and text: only zoom, never drag the print under the UI

    def __init__(self, canvas: Image.Image, preset: str, duration: float, focus: tuple[float, float] = (0.5, 0.5)):
        self.s = canvas
        self.dur = max(0.1, duration)
        sw, sh = canvas.size
        self.sw, self.sh = sw, sh
        z0, z1 = 1.0, 1.10
        f0, f1 = (0.5, 0.5), (0.5, 0.5)
        if preset == "push":
            pass
        elif preset == "pull":
            z0, z1 = 1.12, 1.0
        elif preset == "pan_lr":
            z0, z1, f0, f1 = 1.10, 1.10, (0.0, 0.5), (1.0, 0.5)
        elif preset == "pan_rl":
            z0, z1, f0, f1 = 1.10, 1.10, (1.0, 0.5), (0.0, 0.5)
        elif preset == "drift_up":
            z0, z1, f0, f1 = 1.06, 1.12, (0.5, 1.0), (0.5, 0.0)
        elif preset == "push_br":
            z0, z1, f0, f1 = 1.0, 1.14, (0.5, 0.5), (0.85, 0.85)
        elif preset == "focus":
            z0, z1 = 1.0, 1.16
            ww = sw / z1
            f1 = (self._frac(focus[0] * sw, ww, sw), self._frac(focus[1] * sh, ww * H / W, sh))
        self.z0, self.z1, self.f0, self.f1 = z0, z1, f0, f1

    @staticmethod
    def _frac(centre: float, win: float, total: float) -> float:
        room = total - win
        return 0.5 if room <= 0 else min(1.0, max(0.0, (centre - win / 2) / room))

    def frame(self, t: float) -> Image.Image:
        e = smooth(t / self.dur)
        z = self.z0 + (self.z1 - self.z0) * e
        fx = self.f0[0] + (self.f1[0] - self.f0[0]) * e
        fy = self.f0[1] + (self.f1[1] - self.f0[1]) * e
        ww = self.sw / z
        wh = ww * H / W
        left = (self.sw - ww) * fx
        top = (self.sh - wh) * fy
        k = ww / W
        return self.s.transform((W, H), Image.AFFINE, (k, 0, left, 0, k, top), Image.BICUBIC)


# -- overlays ----------------------------------------------------------------
def pill(text: str, fnt: ImageFont.FreeTypeFont, fg=PAPER, bg=(0, 0, 0, 170), pad=(22, 12)) -> Image.Image:
    tw = int(fnt.getlength(text))
    asc, desc = fnt.getmetrics()
    w, h = tw + pad[0] * 2, asc + desc + pad[1] * 2
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=bg)
    ImageDraw.Draw(im).text((pad[0], pad[1]), text, font=fnt, fill=fg)
    return im


def static_tags(series_tag: str, handle: str) -> list[tuple[Image.Image, tuple[int, int]]]:
    fm = font("mono", 28)
    left = f"AWHYNOTE · {series_tag}".upper()
    a = Image.new("RGBA", (int(fm.getlength(left)) + 8, 44), (0, 0, 0, 0))
    ImageDraw.Draw(a).text((2, 2), left, font=fm, fill=(*PAPER, 235), stroke_width=2, stroke_fill=(0, 0, 0, 200))
    hand = handle.upper().lstrip("@")
    hand = "@" + hand
    b = Image.new("RGBA", (int(fm.getlength(hand)) + 8, 44), (0, 0, 0, 0))
    ImageDraw.Draw(b).text((2, 2), hand, font=fm, fill=(*PAPER, 130), stroke_width=2, stroke_fill=(0, 0, 0, 90))
    return [(a, (65, 262)), (b, (W - 65 - b.width, 262))]


def location_chip(text: str) -> tuple[Image.Image, tuple[int, int]]:
    return pill("| " + text.upper(), font("mono", 28)), (65, 330)


def year_stamp(text: str, seed: int = 3) -> tuple[Image.Image, tuple[int, int]]:
    rnd = random.Random(seed + sum(map(ord, text)))
    fnt = font("head", 112)
    tw = int(fnt.getlength(text))
    w, h = tw + 90, 190
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pts = []
    n = 26
    for i in range(n + 1):
        pts.append((i * w / n, rnd.randint(0, 9)))
    for i in range(1, 7):
        pts.append((w - rnd.randint(0, 9), i * h / 7))
    for i in range(n, -1, -1):
        pts.append((i * w / n, h - rnd.randint(0, 9)))
    for i in range(6, 0, -1):
        pts.append((rnd.randint(0, 9), i * h / 7))
    d.polygon(pts, fill=YELLOW)
    d.text(((w - tw) / 2, 22), text, font=fnt, fill=(14, 14, 15))
    im = im.rotate(-4, resample=Image.BICUBIC, expand=True)
    return im, (W - im.width - 55, 380)


def source_pill(text: str) -> tuple[Image.Image, tuple[int, int]]:
    im = pill("SOURCE: " + text.upper(), font("mono", 26), bg=(0, 0, 0, 190))
    if im.width > W - 130:
        im = im.resize((W - 130, round(im.height * (W - 130) / im.width)), Image.LANCZOS)
    return im, ((W - im.width) // 2, 1430)


def vignette(strength: float = 0.32) -> Image.Image:
    """RGB multiply layer: 1.0 in the middle, darker toward the corners."""
    small = Image.new("L", (270, 480))
    px = small.load()
    for y in range(480):
        for x in range(270):
            dx, dy = (x - 135) / 135, (y - 240) / 240
            r = min(1.0, math.sqrt(dx * dx * 0.7 + dy * dy * 0.9) / 1.15)
            px[x, y] = round(255 * (1 - strength * (r ** 2.2)))
    return small.resize((W, H), Image.BICUBIC).convert("RGB")


def grain_frames(n: int = 4, sigma: int = 4) -> list[Image.Image]:
    return [Image.effect_noise((W, H), sigma).convert("RGB") for _ in range(n)]


def apply_grade(frame: Image.Image, vig: Image.Image, grain: Image.Image) -> Image.Image:
    frame = ImageChops.multiply(frame, vig)
    return ImageChops.add(frame, grain, 1, -128)
