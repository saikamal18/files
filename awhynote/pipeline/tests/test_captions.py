from dataclasses import replace

import pytest

from awhynote import captions
from awhynote import visuals as V
from awhynote.styles import STYLES, get_style


def _voice(words_per_beat):
    beats, t = [], 0.0
    for bi, ws in enumerate(words_per_beat, 1):
        words = []
        for w in ws:
            words.append({"w": w, "start": t, "end": t + 0.3, "hl": False})
            t += 0.35
        beats.append({"id": bi, "shot": 1, "text": " ".join(ws), "start": words[0]["start"], "end": t, "words": words})
    return {"beats": beats, "duration": t}


def test_chunks_are_two_to_four_words_and_stay_in_beat():
    v = _voice([["You've", "held", "this", "note", "a", "thousand", "times."], ["Now", "turn", "it", "over."]])
    chunks = captions.build_chunks(v)
    assert all(1 <= len(c.words) <= 4 for c in chunks)
    assert sum(len(c.words) for c in chunks) == 11
    assert not any(len(c.words) == 1 for c in chunks)
    assert [w["w"] for c in chunks for w in c.words][:2] == ["You've", "held"]


def test_each_chunk_gets_a_highlight_heuristic():
    chunks = captions.build_chunks(_voice([["Nalanda", "had", "10,000", "students."]]))
    hl_words = [c.words[c.hl_index]["w"] for c in chunks if c.hl_index is not None]
    assert hl_words and any("10,000" in w or "Nalanda" in w for w in hl_words)


def test_llm_highlight_wins():
    v = _voice([["The", "quiet", "secret", "here"]])
    v["beats"][0]["words"][1]["hl"] = True
    assert captions.build_chunks(v)[0].hl_index == 1


def test_progressive_reveal_and_layout_cache():
    track = captions.CaptionTrack(_voice([["Look", "closely", "at", "it"]]))
    a, b = track.overlay(0.01), track.overlay(0.9)
    assert a is not None and b is not None
    assert a[0] is not b[0]
    assert track.overlay(0.9)[0] is b[0]  # cached
    assert track.overlay(99.0) is None


@pytest.mark.parametrize("name", sorted(STYLES))
def test_short_chunk_fits_on_one_line_long_goes_two(name):
    style = get_style(name)
    V.use_style(style)
    c1 = captions.Chunk([{"w": "Why", "start": 0, "end": 1}, {"w": "now", "start": 1, "end": 2}], 0, 2, None)
    placed, _, h1, _ = captions._layout(c1, style)
    assert len({y for _, _, _, y in placed}) == 1
    c2 = captions.Chunk([{"w": w, "start": 0, "end": 1} for w in ["Government", "announced", "demonetisation", "yesterday"]], 0, 2, None)
    placed2, _, h2, _ = captions._layout(c2, style)
    assert len({y for _, _, _, y in placed2}) == 2 and h2 > h1


@pytest.mark.parametrize("name", sorted(STYLES))
def test_every_style_draws_every_word_inside_the_frame(name):
    style = get_style(name)
    V.use_style(style)
    track = captions.CaptionTrack(_voice([["The", "₹10", "note", "hides", "a", "temple", "from", "Odisha"]]), style)
    for t in (0.01, 0.05, 0.2, 0.6, 1.5):
        got = track.overlay(t)
        if got:
            im, (x, y) = got
            assert im.width == V.W and 0 <= y and y + im.height <= V.H
            assert im.getbbox() is not None


def test_pop_in_animates_only_the_newest_word():
    style = get_style("poppins")
    V.use_style(style)
    track = captions.CaptionTrack(_voice([["Look", "closely", "at", "it"]]), style)
    t_new = track.chunks[0].words[1]["start"]
    first, settled = track.overlay(t_new), track.overlay(t_new + 0.4)
    assert first[0].tobytes() != settled[0].tobytes()
    assert track._pop_step(0, 2, t_new + 0.4) is None
    flat = captions.CaptionTrack(_voice([["Look", "closely", "at", "it"]]), replace(style, pop=False))
    assert flat._pop_step(0, 2, t_new) is None


def test_head_fonts_have_the_rupee_sign():
    from awhynote.util import FONT_DIR

    for name in sorted(STYLES):
        style = get_style(name)
        if not (FONT_DIR / style.file).exists():
            pytest.skip("brand fonts not downloaded")
        V.use_style(style)
        f = V.font("head", 90)
        assert bytes(f.getmask("₹")) != bytes(f.getmask("\ue0ff")), f"{style.file} is missing the ₹ glyph"


def test_caption_never_overlaps_the_next_beat():
    voice = {"beats": [
        {"words": [{"w": "Nobody", "start": 0.0, "end": 0.4, "hl": False}, {"w": "noticed.", "start": 0.45, "end": 0.8, "hl": False}]},
        {"words": [{"w": "Everything", "start": 0.9, "end": 1.4, "hl": False}, {"w": "changed.", "start": 1.45, "end": 1.9, "hl": False}]},
    ]}
    chunks = captions.build_chunks(voice)
    assert all(a.show_end <= b.show_start + 1e-9 for a, b in zip(chunks, chunks[1:]))
