import json

import pytest
from PIL import Image

from awhynote import media, render, tts
from awhynote.store import Episode
from awhynote.util import media_info

from conftest import image_bytes, make_clip


@pytest.fixture
def short_ep(cfg):
    """A 4-shot, ~10 s episode: image (full), image card + callout, video clip, text card."""
    ep = Episode.create(cfg.episodes_dir, {"id": "R01", "hook_title": "Render test", "pillar": "Money"}, "2026-12-01")
    sc = {
        "series_tag": "NOTE 001", "title": "Render test", "sources_line": "TEST · SOURCE",
        "shots": [
            {"id": 1, "visual_type": "macro", "style": "full", "brief": "b", "stat_text": "", "year_stamp": "", "location_tag": "HYDERABAD"},
            {"id": 2, "visual_type": "archival", "style": "card", "brief": "b", "stat_text": "", "year_stamp": "1938", "location_tag": ""},
            {"id": 3, "visual_type": "stock", "style": "full", "brief": "b", "stat_text": "", "year_stamp": "", "location_tag": ""},
            {"id": 4, "visual_type": "text", "style": "text", "brief": "", "stat_text": "EVERYTHING HAS A WHY.", "year_stamp": "", "location_tag": ""},
        ],
        "beats": [
            {"id": 1, "shot": 1, "narration": "This note has a secret inside.", "highlight": ["secret"], "pause_after_s": 0},
            {"id": 2, "shot": 2, "narration": "It was printed in 1938 somewhere.", "highlight": ["1938"], "pause_after_s": 0},
            {"id": 3, "shot": 3, "narration": "Then everything changed very quickly.", "highlight": [], "pause_after_s": 0},
            {"id": 4, "shot": 4, "narration": "Everything has a why.", "highlight": ["why"], "pause_after_s": 0},
        ],
    }
    ep.write("script.json", sc)
    voice = tts.synthesize(cfg, ep, sc)
    media.add_file(ep, 1, image_bytes(1600, 1000), "wide.jpg")
    media.add_file(ep, 2, image_bytes(1200, 1500), "arch.jpg")
    media.add_file(ep, 3, make_clip(cfg.ffmpeg, ep.path("c.mp4"), 1.0), "clip.mp4")
    media.set_callout(ep, 2, 0.5, 0.5, 0.12)
    media.prepare(cfg, ep, sc, voice)
    return ep


def test_render_video_specs(cfg, short_ep):
    ticks = []
    info = render.render_video(cfg, short_ep, progress=ticks.append)
    out = media_info(cfg.ffmpeg, short_ep.path("final.mp4"))
    assert (out["width"], out["height"]) == (1080, 1920)
    assert out["has_audio"] and out["has_video"]
    voice = short_ep.read("voice.json")
    assert abs(out["duration"] - (voice["duration"] + render.TAIL_S)) < 0.4
    assert info["size_mb"] < 49
    prev = media_info(cfg.ffmpeg, short_ep.path("preview.mp4"))
    assert (prev["width"], prev["height"]) == (540, 960)


def test_cover_is_1080x1920_and_has_title(cfg, short_ep):
    p = render.make_cover(cfg, short_ep, "The secret in your note", "secret", "NOTE 001")
    im = Image.open(p)
    assert im.size == (1080, 1920)
    # the yellow highlight box must exist somewhere in the centre band
    px = im.convert("RGB").crop((0, 480, 1080, 1440)).getcolors(1_000_000)
    assert any(abs(c[0] - 255) < 6 and abs(c[1] - 214) < 6 and c[2] < 40 for n, c in px if n > 2000)


def test_audio_is_loudness_normalised(cfg, short_ep):
    import subprocess

    render.render_video(cfg, short_ep)
    res = subprocess.run([cfg.ffmpeg, "-hide_banner", "-i", str(short_ep.path("final.mp4")), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    import re

    lufs = float(re.findall(r"I:\s+(-?\d+\.\d+) LUFS", res.stderr)[-1])
    assert -17 < lufs < -11  # target -14 on a short clip, loudnorm one-pass is approximate
