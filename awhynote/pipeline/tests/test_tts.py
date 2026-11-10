import array

import pytest

from awhynote import tts
from awhynote.store import Episode


def test_normalise_rupees():
    assert tts.normalise_speech("The ₹10 note and ₹1,000 too") == "The 10 rupees note and 1,000 rupees too"
    assert tts.normalise_speech("Rs. 500 only") == "500 rupees only"


def test_map_words_one_to_one_and_proportional():
    spoken = [{"w": "a", "start": 1.0, "end": 1.2}, {"w": "b", "start": 1.2, "end": 1.6}]
    m = tts.map_words(["A", "B"], spoken, offset=1.0)
    assert [round(x["start"], 2) for x in m] == [0.0, 0.2]
    m2 = tts.map_words(["₹10", "note"], [{"w": "10", "start": 0, "end": 0.3}, {"w": "rupees", "start": 0.3, "end": 0.7}, {"w": "note", "start": 0.7, "end": 1.0}], 0.0)
    assert len(m2) == 2 and m2[0]["start"] == 0 and abs(m2[-1]["end"] - 1.0) < 1e-6


def test_trim_silence():
    a = array.array("h", [0] * 4410 + [5000] * 4410 + [0] * 4410)
    out, trimmed = tts.trim_silence(a)
    assert len(out) < len(a)
    assert 0.05 < trimmed < 0.1


def test_mock_synthesis_timings_monotonic(cfg, tmp_path):
    from awhynote.mock import mock_llm
    from awhynote.script import normalize

    sc = normalize(mock_llm("script", {"topic": {"hook_title": "T"}, "next_title": "N"}))
    ep = Episode.create(cfg.episodes_dir, {"id": "T01", "hook_title": "T"}, "2026-12-01")
    meta = tts.synthesize(cfg, ep, sc)
    assert meta["duration"] > 10
    last = -1
    for b in meta["beats"]:
        assert b["start"] >= last
        assert b["end"] > b["start"]
        ws = [w["start"] for w in b["words"]]
        assert ws == sorted(ws)
        last = b["end"]
    assert ep.path("voice.wav").exists() and ep.path("voice.m4a").exists()


@pytest.mark.network
def test_real_edge_tts_word_timings(cfg):
    cfg.tts_provider = "edge"
    ep = Episode.create(cfg.episodes_dir, {"id": "T02", "hook_title": "T"}, "2026-12-01")
    sc = {"beats": [{"id": 1, "shot": 1, "narration": "The ₹10 note is a story.", "highlight": ["story"]},
                    {"id": 2, "shot": 1, "narration": "Everything has a why.", "highlight": []}]}
    try:
        meta = tts.synthesize(cfg, ep, sc)
    except RuntimeError as e:
        pytest.skip(f"edge-tts unreachable: {e}")
    assert 3 < meta["duration"] < 12
    assert [w["w"] for w in meta["beats"][0]["words"]] == ["The", "₹10", "note", "is", "a", "story."]
    assert any(w["hl"] for w in meta["beats"][0]["words"])
