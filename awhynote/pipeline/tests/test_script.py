from awhynote import script
from awhynote.mock import mock_llm


def _mock():
    return script.normalize(mock_llm("script", {"topic": {"hook_title": "T", "first_3s_hook": "Look at this note closely today."}, "next_title": "Where does the internet actually live"}))


def test_mock_script_is_valid():
    s = _mock()
    errors, warns = script.validate(s)
    assert errors == []
    assert 95 <= sum(len(script.words(b["narration"])) for b in s["beats"]) <= 140


def test_outro_is_enforced_locally():
    s = _mock()
    s["beats"][-1]["narration"] = "Goodbye for now."
    fixed = script.normalize(s)
    assert fixed["beats"][-1]["narration"] == "Everything has a why."
    assert fixed["auto_fixed"]


def test_highlight_must_exist_in_narration():
    s = {"shots": [{"id": 1, "visual_type": "macro", "brief": "b"}],
         "beats": [{"id": 1, "shot": 1, "narration": "The temple is real.", "highlight": ["temple", "banana"]}]}
    n = script.normalize(s)
    assert n["beats"][0]["highlight"] == ["temple"]


def test_validation_catches_problems():
    s = _mock()
    s["beats"][3]["shot"] = 99
    s["beats"][4]["narration"] = "Hi."
    errors, _ = script.validate(s)
    assert any("unknown shot" in e for e in errors)
    assert any("beat 5" in e for e in errors)


def test_too_long_is_rejected():
    s = _mock()
    for b in s["beats"]:
        b["narration"] += " and a lot more words to make it very long indeed"
    errors, _ = script.validate(s)
    assert any("words in total" in e or "over the" in e for e in errors)


def test_style_defaults_and_unknown_types():
    n = script.normalize({"shots": [{"id": 1, "visual_type": "hologram"}, {"id": 2, "visual_type": "archival"}, {"id": 3, "visual_type": "stat", "style": "full"}],
                          "beats": [{"id": 1, "shot": 1, "narration": "a b c d e"}]})
    assert [s["visual_type"] for s in n["shots"]] == ["stock", "archival", "stat"]
    assert [s["style"] for s in n["shots"]] == ["full", "card", "text"]
