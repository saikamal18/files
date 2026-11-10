"""Word-by-word captions: 2-4 word chunks, progressive reveal, one yellow highlight box per chunk."""
from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .script import clean_word
from .visuals import H, PAPER, W, YELLOW, font

STOP = {"the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "is", "was", "it", "this", "that", "for", "with", "as",
        "you", "your", "we", "i", "he", "she", "they", "are", "be", "by", "from", "has", "have", "had", "so", "now", "then", "not"}
MAX_W = 930
CENTRE_Y = 1260


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
    return chunks


def _layout(chunk: Chunk) -> tuple[list[tuple[int, str, int, int]], int, int, int]:
    """Return ([(word_index, text, x, y)], block_w, block_h, size). One line if it fits, else a balanced two-line split."""
    texts = [w["w"].upper() for w in chunk.words]
    best = None
    for size in (86, 78, 70, 62, 54):
        fnt = font("head", size)
        space = fnt.getlength(" ")
        widths = [fnt.getlength(t) for t in texts]

        def line_w(idx):
            return sum(widths[i] for i in idx) + space * (len(idx) - 1)

        if size >= 62 and line_w(range(len(texts))) <= MAX_W:
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
        best = (54, [list(range(k)), list(range(k, len(texts)))] if len(texts) > 1 else [[0]])
    size, lines = best
    fnt = font("head", size)
    space = fnt.getlength(" ")
    lh = int(size * 1.34)
    placed = []
    for li, line in enumerate(lines):
        lw = sum(fnt.getlength(texts[i]) for i in line) + space * (len(line) - 1)
        x = (W - lw) / 2
        for i in line:
            placed.append((i, texts[i], int(x), li * lh))
            x += fnt.getlength(texts[i]) + space
    return placed, W, lh * len(lines), size


class CaptionTrack:
    def __init__(self, voice: dict):
        self.chunks = build_chunks(voice)
        self._cache: dict[tuple[int, int], tuple[Image.Image, tuple[int, int]]] = {}
        self._layouts: dict[int, tuple] = {}

    def active(self, t: float) -> tuple[int, int] | None:
        for ci, c in enumerate(self.chunks):
            if c.show_start - 0.02 <= t < c.show_end:
                k = sum(1 for w in c.words if w["start"] - 0.02 <= t)
                return ci, max(1, k)
        return None

    def overlay(self, t: float) -> tuple[Image.Image, tuple[int, int]] | None:
        a = self.active(t)
        if not a:
            return None
        if a not in self._cache:
            self._cache[a] = self._render(*a)
        return self._cache[a]

    def _render(self, ci: int, k: int) -> tuple[Image.Image, tuple[int, int]]:
        chunk = self.chunks[ci]
        if ci not in self._layouts:
            self._layouts[ci] = _layout(chunk)
        placed, bw, bh, size = self._layouts[ci]
        fnt = font("head", size)
        pad = 26
        im = Image.new("RGBA", (bw, bh + pad * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        asc, desc = fnt.getmetrics()
        for i, text, x, y in placed:
            if i >= k:
                continue
            y0 = y + pad
            if i == chunk.hl_index:
                bx0, bx1 = x - 16, x + fnt.getlength(text) + 16
                d.rounded_rectangle([bx0, y0 - 2, bx1, y0 + asc + desc + 2], radius=16, fill=YELLOW)
                d.text((x, y0), text, font=fnt, fill=(14, 14, 15))
            else:
                d.text((x, y0 + 4), text, font=fnt, fill=(0, 0, 0, 120), stroke_width=6, stroke_fill=(0, 0, 0, 120))
                d.text((x, y0), text, font=fnt, fill=PAPER, stroke_width=5, stroke_fill=(0, 0, 0, 235))
        return im, (0, int(CENTRE_Y - im.height / 2))
