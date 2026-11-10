from awhynote import captions


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


def test_short_chunk_fits_on_one_line_long_goes_two():
    c1 = captions.Chunk([{"w": "Why", "start": 0, "end": 1}, {"w": "now", "start": 1, "end": 2}], 0, 2, None)
    placed, _, h1, _ = captions._layout(c1)
    assert len({y for _, _, _, y in placed}) == 1
    c2 = captions.Chunk([{"w": w, "start": 0, "end": 1} for w in ["Government", "announced", "demonetisation", "yesterday"]], 0, 2, None)
    placed2, _, h2, _ = captions._layout(c2)
    assert len({y for _, _, _, y in placed2}) == 2 and h2 > h1
