import pytest
from PIL import Image

from awhynote import media, plan
from awhynote.mock import mock_llm
from awhynote.script import normalize
from awhynote.store import Episode
from awhynote import tts

from conftest import image_bytes, make_clip


@pytest.fixture
def ep_ready(cfg):
    topic = plan.topic_for(cfg, "M01")
    ep = Episode.create(cfg.episodes_dir, topic, "2026-11-23")
    sc = normalize(mock_llm("script", {"topic": topic, "next_title": "Where does the internet actually live"}))
    ep.write("script.json", sc)
    voice = tts.synthesize(cfg, ep, sc)
    return ep, sc, voice


@pytest.mark.parametrize("name,expected", [("s3_note.jpg", 3), ("3.jpg", 3), ("shot12-x.mov", 12), ("S04.png", 4), ("2024_photo.jpg", None), ("IMG_0042.jpg", None), ("photo.jpg", None)])
def test_parse_shot_number(name, expected):
    assert media.parse_shot_number(name) == expected


def test_scan_missing_skip_and_unassigned(cfg, ep_ready):
    ep, sc, voice = ep_ready
    st = media.scan(ep, sc)
    needs_media = [s["id"] for s in sc["shots"] if s["style"] != "text"]
    assert st["missing"] == needs_media and not st["ready"]
    media.add_file(ep, 1, image_bytes(800, 1000), "a.jpg")
    (media.inbox(ep) / "IMG_0042.jpg").write_bytes(image_bytes(100, 100))
    media.set_skip(ep, 2)
    st = media.scan(ep, sc)
    assert 1 not in st["missing"] and 2 not in st["missing"]
    assert st["unassigned"] == ["IMG_0042.jpg"]
    assert st["shots"][1]["state"] == "media" and st["shots"][2]["state"] == "skipped"
    text_shots = [s["id"] for s in sc["shots"] if s["style"] == "text"]
    assert all(st["shots"][i]["state"] == "auto" for i in text_shots)


def test_add_file_rejects_bad_type_and_dedupes(cfg, ep_ready):
    ep, *_ = ep_ready
    with pytest.raises(ValueError):
        media.add_file(ep, 1, b"x", "virus.exe")
    p1 = media.add_file(ep, 1, b"1", "same.jpg")
    p2 = media.add_file(ep, 1, b"2", "same.jpg")
    assert p1 != p2 and p1.exists() and p2.exists()


def test_shot_windows_cover_the_whole_voice(ep_ready):
    ep, sc, voice = ep_ready
    win = media.shot_windows(voice, sc["shots"])
    ids = [s["id"] for s in sc["shots"]]
    assert win[ids[0]][0] == 0 and abs(win[ids[-1]][1] - voice["duration"]) < 1e-6
    assert all(win[a][1] == win[b][0] for a, b in zip(ids, ids[1:]))


def test_prepare_enhances_and_builds_contact_sheet(cfg, ep_ready):
    ep, sc, voice = ep_ready
    ids = [s["id"] for s in sc["shots"] if s["style"] != "text"]
    media.add_file(ep, ids[0], image_bytes(600, 450), "small.jpg")  # small, landscape -> upscaled + blur-pad
    media.add_file(ep, ids[1], image_bytes(2000, 3000), "big.jpg")
    media.add_file(ep, ids[2], make_clip(cfg.ffmpeg, ep.path("c.mp4")), "clip.mp4")
    for i in ids[3:]:
        media.set_skip(ep, i)
    media.set_callout(ep, ids[1], 0.5, 0.4, 0.1)
    man = media.prepare(cfg, ep, sc, voice)
    by = {s["id"]: s for s in man["shots"]}
    small = by[ids[0]]["items"][0]
    assert small["info"]["out_size"][0] >= 1800 and "upscaled" in " ".join(small["info"]["ops"])
    assert by[ids[2]]["items"][0]["type"] == "video"
    assert by[ids[3]]["style"] == "text" and by[ids[3]]["fallback"]
    assert by[ids[1]]["callout"]["x"] == 0.5
    sheet = Image.open(ep.path("media/contact.jpg"))
    assert sheet.width > 500 and sheet.height > 500
    assert (ep.dir / small["path"]).exists()


def test_enhance_does_not_alter_originals(cfg, ep_ready):
    ep, sc, voice = ep_ready
    p = media.add_file(ep, 1, image_bytes(900, 900), "keep.jpg")
    before = p.read_bytes()
    for i in [s["id"] for s in sc["shots"] if s["style"] != "text" and s["id"] != 1]:
        media.set_skip(ep, i)
    media.prepare(cfg, ep, sc, voice)
    assert p.read_bytes() == before
