"""Word-by-word captions: 2-4 word chunks, progressive reveal, one yellow highlight box per chunk."""
from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .script import clean_word
from .styles import Style, get_style
from .visuals import H, PAPER, W, YELLOW, font

STOP = {"the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "is", "was", "it", "this", "that", "for", "with", "as",
        "you", "your", "we", "i", "he", "she", "they", "are", "be", "by", "from", "has", "have", "had", "so", "now", "then", "not"}
MAX_W = 930
CENTRE_Y = 1260
BOX_GAP = 14
POP_S = 0.14
POP_SCALES = (0.76, 0.92, 1.07, 1.0)  # newest word: grow in with a little overshoot


@dataclass
class Chunk:
    words: list[dict]
    show_start: float
    show_end: float
    hl_index: int | None


def _pick_highlight(words: list[dict]) -> int | None:
    for i, w in enumerate(words):
        if w.get("hl"):
            return i
    for i, w in enumerate(words):
        if any(c.isdigit() for c in w["w"]):
            return i
    cands = [(len(clean_word(w["w"])), i) for i, w in enumerate(words) if clean_word(w["w"]) not in STOP and len(clean_word(w["w"])) >= 4]
    return max(cands)[1] if cands and len(words) >= 2 else None


def build_chunks(voice: dict) -> list[Chunk]:
    chunks: list[Chunk] = []
    for b in voice["beats"]:
        groups: list[list[dict]] = []
        cur: list[dict] = []
        for w in b["words"]:
            cur.append(w)
            chars = sum(len(x["w"]) for x in cur)
            punct = re.search(r"[.?!,;:—–-]$", w["w"]) is not None
            if len(cur) >= 4 or (len(cur) == 3 and chars >= 14) or (len(cur) >= 2 and punct) or chars >= 22:
                groups.append(cur)
                cur = []
        if cur:
            if len(cur) == 1 and groups and len(groups[-1]) < 4:
                groups[-1].extend(cur)
            else:
                groups.append(cur)
        for gi, g in enumerate(groups):
            start = g[0]["start"]
            if gi + 1 < len(groups):
                end = groups[gi + 1][0]["start"]
            else:
                end = g[-1]["end"] + 0.35
            chunks.append(Chunk(g, start, end, _pick_highlight(g)))
    for a, b in zip(chunks, chunks[1:]):
        a.show_end = min(a.show_end, b.show_start)  # a lingering caption must never cover the next beat's first word
    return chunks


def _layout(chunk: Chunk, style: Style) -> tuple[list[tuple[int, str, int, int]], int, int, int]:
    """Return ([(word_index, text, x, y)], block_w, block_h, size). One line if it fits, else a balanced two-line split."""
    texts = [(w["w"].upper() if style.upper else w["w"]) for w in chunk.words]
    boxed = chunk.hl_index if style.highlight == "box" else None  # a boxed word needs breathing room on both sides
    best = None
    single_min = style.sizes[2]
    for size in style.sizes:
        fnt = font("head", size)
        space = fnt.getlength(" ")
        widths = [fnt.getlength(t) + (2 * BOX_GAP if i == boxed else 0) for i, t in enumerate(texts)]

        def line_w(idx):
            return sum(widths[i] for i in idx) + space * (len(idx) - 1)

        if size >= single_min and line_w(range(len(texts))) <= MAX_W:
            best = (size, [list(range(len(texts)))])
            break
        if len(texts) >= 2:
            splits = [(max(line_w(range(k)), line_w(range(k, len(texts)))), k) for k in range(1, len(texts))]
            worst, k = min(splits)
            if worst <= MAX_W:
                best = (size, [list(range(k)), list(range(k, len(texts)))])
                break
    if best is None:  # absurdly long words: smallest size, balanced two lines (may be tight, never crashes)
        k = max(1, len(texts) // 2)
        best = (style.sizes[-1], [list(range(k)), list(range(k, len(texts)))] if len(texts) > 1 else [[0]])
    size, lines = best
    fnt = font("head", size)
    space = fnt.getlength(" ")
    lh = int(size * style.line_height)
    placed = []
    for li, line in enumerate(lines):
        lw = sum(fnt.getlength(texts[i]) + (2 * BOX_GAP if i == boxed else 0) for i in line) + space * (len(line) - 1)
        x = (W - lw) / 2
        for i in line:
            gap = BOX_GAP if i == boxed else 0
            placed.append((i, texts[i], int(x + gap), li * lh))
            x += fnt.getlength(texts[i]) + 2 * gap + space
    return placed, W, lh * len(lines), size


def _draw_word(d: ImageDraw.ImageDraw, text: str, x: float, y: float, fnt, hl: bool, style: Style) -> None:
    asc, desc = fnt.getmetrics()
    if hl and style.highlight == "box":
        d.rounded_rectangle([x - 16, y - 2, x + fnt.getlength(text) + 16, y + asc + desc + 2], radius=16, fill=YELLOW)
        d.text((x, y), text, font=fnt, fill=(14, 14, 15))
        return
    if hl:
        fill = YELLOW
    else:
        fill = PAPER
    d.text((x, y + 4), text, font=fnt, fill=(0, 0, 0, 120), stroke_width=style.stroke + 1, stroke_fill=(0, 0, 0, 120))
    d.text((x, y), text, font=fnt, fill=fill, stroke_width=style.stroke, stroke_fill=(0, 0, 0, 235))


class CaptionTrack:
    def __init__(self, voice: dict, style: Style | str | None = None):
        self.style = style if isinstance(style, Style) else get_style(style)
        self.chunks = build_chunks(voice)
        self._cache: dict[tuple, tuple[Image.Image, tuple[int, int]]] = {}
        self._layouts: dict[int, tuple] = {}

    def active(self, t: float) -> tuple[int, int] | None:
        for ci, c in enumerate(self.chunks):
            if c.show_start - 0.02 <= t < c.show_end:
                k = sum(1 for w in c.words if w["start"] - 0.02 <= t)
                return ci, max(1, k)
        return None

    def _pop_step(self, ci: int, k: int, t: float) -> int | None:
        if not self.style.pop:
            return None
        age = t - (self.chunks[ci].words[k - 1]["start"] - 0.02)
        if age >= POP_S:
            return None
        return min(len(POP_SCALES) - 1, max(0, int(age / POP_S * len(POP_SCALES))))

    def overlay(self, t: float) -> tuple[Image.Image, tuple[int, int]] | None:
        a = self.active(t)
        if not a:
            return None
        key = (*a, self._pop_step(*a, t))
        if key not in self._cache:
            self._cache[key] = self._render(*key)
        return self._cache[key]

    def _render(self, ci: int, k: int, step: int | None = None) -> tuple[Image.Image, tuple[int, int]]:
        chunk, style = self.chunks[ci], self.style
        if ci not in self._layouts:
            self._layouts[ci] = _layout(chunk, style)
        placed, bw, bh, size = self._layouts[ci]
        fnt = font("head", size)
        pad = 26
        im = Image.new("RGBA", (bw, bh + pad * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        asc, desc = fnt.getmetrics()
        newest = None
        for i, text, x, y in placed:
            if i >= k:
                continue
            if i == k - 1 and step is not None and POP_SCALES[step] != 1.0:
                newest = (i, text, x, y)
                continue
            _draw_word(d, text, x, y + pad, fnt, i == chunk.hl_index, style)
        if newest:
            i, text, x, y = newest
            m = 44  # sprite margin so the box, outline and shadow are not clipped while scaling
            sw = int(fnt.getlength(text)) + 2 * m
            sh = asc + desc + 2 * m
            spr = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
            _draw_word(ImageDraw.Draw(spr), text, m, m, fnt, i == chunk.hl_index, style)
            sc = POP_SCALES[step]
            spr = spr.resize((max(1, round(sw * sc)), max(1, round(sh * sc))), Image.BICUBIC)
            cx, cy = x + fnt.getlength(text) / 2, y + pad + (asc + desc) / 2
            layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
            layer.paste(spr, (int(cx - spr.width / 2), int(cy - spr.height / 2)))
            im = Image.alpha_composite(im, layer)
        return im, (0, int(CENTRE_Y - im.height / 2))
