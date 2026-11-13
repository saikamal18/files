import array
import math
import re
import subprocess
import wave
from pathlib import Path
from dataclasses import replace

import pytest
from PIL import Image, ImageChops

from awhynote import music, render, sfx
from awhynote import transitions as TR
from awhynote.util import media_info

from test_render import short_ep  # noqa: F401  (fixture)


def _solid(c, size=(108, 192)):
    return Image.new("RGB", size, c)


@pytest.mark.parametrize("kind", sorted(TR.LENGTH_S))
def test_transition_endpoints_and_size(kind):
    a, b = _solid((200, 30, 30)), _solid((30, 30, 200))
    assert ImageChops.difference(TR.blend(kind, a, b, 0.0), a).getbbox() is None or max(ImageChops.difference(TR.blend(kind, a, b, 0.0), a).getextrema())[1] < 6
    end = TR.blend(kind, a, b, 1.0)
    assert max(max(ex) for ex in ImageChops.difference(end, b).getextrema()) < 6
    mid = TR.blend(kind, a, b, 0.5)
    assert mid.size == a.size and mid.mode == "RGB"


def test_choose_rules():
    assert TR.choose("cut", {"vtype": "macro"}, False, 0, False) is None
    assert TR.choose("fade", {"vtype": "macro"}, False, 0, False) == "fade"
    assert TR.choose("mix", {"vtype": "macro"}, True, 0, False) == "fade"
    assert TR.choose("mix", {"vtype": "macro", "style": "full"}, False, 0, False) == "zoom"
    assert TR.choose("mix", {"vtype": "map", "style": "full"}, False, 0, False) == "push"
    assert TR.choose("mix", {"vtype": "text", "style": "text"}, False, 0, False) == "flash"
    assert TR.choose("mix", {"vtype": "stock", "style": "full"}, False, 1, True) == "fade"


def test_sfx_track_places_sound_at_the_cuts(tmp_path):
    assert sfx.build_track([], 5.0, tmp_path / "none.wav") is None
    p = sfx.build_track([(2.0, "whoosh"), (4.0, "hit")], 6.0, tmp_path / "s.wav")
    with wave.open(str(p)) as w:
        sr, data = w.getframerate(), array.array("h", w.readframes(w.getnframes()))

    def peak(a, b):
        return max(abs(v) for v in data[int(a * sr):int(b * sr)])

    assert peak(1.8, 2.3) > 3000 and peak(4.0, 4.2) > 3000  # sound at the cuts
    assert peak(0.0, 1.0) == 0 and peak(2.9, 3.8) == 0       # silence elsewhere
    assert peak(0, 6) <= 32767


def test_music_selection_is_stable_and_modes_work(cfg, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    for n in ("a.mp3", "b.mp3", "c.wav", "notes.txt", "_hidden.mp3"):
        (lib / n).write_bytes(b"x")
    c = replace(cfg, music_dir=str(lib), music_mode="auto", music_file="")
    first = music.pick(c, "M01")
    assert first.name in {"a.mp3", "b.mp3", "c.wav"} and music.pick(c, "M01") == first
    assert replace(c, music_mode="off").music_mode == "off" and music.pick(replace(c, music_mode="off"), "M01") is None
    empty = replace(cfg, music_dir=str(tmp_path / "empty"), music_mode="auto", music_file="")
    amb = music.pick(empty, "M01")  # nothing in the library: auto falls back to the generated pad
    assert amb.exists() and amb.name == music.AMBIENT_NAME
    assert music.pick(replace(c, music_mode="ambient"), "M01").name == music.AMBIENT_NAME  # forced pad even with a library
    explicit = tmp_path / "mine.mp3"
    explicit.write_bytes(b"x")
    assert music.pick(replace(c, music_file=str(explicit)), "M01") == explicit


def _tone(path, freq, seconds, amp, sr=44100):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(array.array("h", (int(amp * math.sin(2 * math.pi * freq * i / sr)) for i in range(int(seconds * sr)))).tobytes())
    return path


def _goertzel(data, sr, freq):
    k = 2 * math.cos(2 * math.pi * freq / sr)
    s1 = s2 = 0.0
    for v in data:
        s1, s2 = v + k * s1 - s2, s1
    return math.sqrt(max(0.0, s1 * s1 + s2 * s2 - k * s1 * s2)) / max(1, len(data))


def test_music_ducks_under_the_voice(cfg, tmp_path):
    voice = _tone(tmp_path / "voice.wav", 1000, 2.0, 12000)  # a 2 s "voice" burst, then silence
    bed = _tone(tmp_path / "bed.wav", 220, 6.0, 12000)
    total = 6.0
    extra, graph = render._audio_filter(replace(cfg, music_db=-12.0), total, None, bed)
    out = tmp_path / "mix.wav"
    subprocess.run([cfg.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-i", str(voice), *extra,
                    "-filter_complex", graph, "-map", "[a]", "-t", str(total), "-ac", "1", "-ar", "44100", str(out)], check=True)
    with wave.open(str(out)) as w:
        sr, data = w.getframerate(), array.array("h", w.readframes(w.getnframes()))
    under_voice = _goertzel(data[int(0.8 * sr):int(1.8 * sr)], sr, 220)
    in_the_clear = _goertzel(data[int(3.0 * sr):int(4.0 * sr)], sr, 220)
    ratio_db = 20 * math.log10(in_the_clear / max(under_voice, 1e-9))
    assert ratio_db > 5, f"music only {ratio_db:.1f} dB quieter under the voice"


def test_transition_plan_respects_mode_and_lengths(cfg, short_ep):  # noqa: F811
    segs = render.build_segments(cfg, short_ep, short_ep.read("media.json"), short_ep.read("voice.json")["duration"] + render.TAIL_S, short_ep.read("script.json"))
    bounds = render.plan_transitions(cfg, segs)
    assert len(bounds) == len(segs) - 1 and any(bounds)
    for i, b in enumerate(bounds):
        assert b is None or (b[0] in TR.LENGTH_S and b[1] <= 0.4 * min(segs[i].end - segs[i].start, segs[i + 1].end - segs[i + 1].start) + 1e-6)
    assert not any(render.plan_transitions(replace(cfg, transition="cut"), segs))


@pytest.mark.parametrize("kind", sorted(TR.LENGTH_S))
def test_every_transition_kind_renders_end_to_end(cfg, short_ep, monkeypatch, kind):  # noqa: F811
    monkeypatch.setattr(TR, "choose", lambda *a, **k: kind)
    render.render_video(cfg, short_ep)
    out = media_info(cfg.ffmpeg, short_ep.path("final.mp4"))
    assert (out["width"], out["height"]) == (1080, 1920) and out["has_audio"]
    assert short_ep.path("sfx.wav").exists() == (kind in TR.SOUND_KINDS)


def test_render_with_ambient_music_still_meets_specs(cfg, short_ep, tmp_path):  # noqa: F811
    c = replace(cfg, music_mode="ambient", music_dir=str(tmp_path / "music"))
    render.render_video(c, short_ep)
    out = media_info(cfg.ffmpeg, short_ep.path("final.mp4"))
    assert (out["width"], out["height"]) == (1080, 1920) and out["has_audio"]
    res = subprocess.run([cfg.ffmpeg, "-hide_banner", "-i", str(short_ep.path("final.mp4")), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    lufs = float(re.findall(r"I:\s+(-?\d+\.\d+) LUFS", res.stderr)[-1])
    assert -17 < lufs < -11


def test_oversize_render_is_reencoded_for_telegram(cfg, short_ep, monkeypatch):  # noqa: F811
    monkeypatch.setattr(render, "TELEGRAM_TARGET_MB", 0.05)
    render.render_video(cfg, short_ep)
    assert short_ep.path("final_master.mp4").exists()
    assert short_ep.path("final.mp4").stat().st_size < short_ep.path("final_master.mp4").stat().st_size


def test_styles_and_style_commands(cfg, tmp_path):
    from awhynote.notify import FakeNotifier
    from awhynote.pipeline import Pipeline
    from awhynote.styles import STYLES

    env = tmp_path / "my.env"
    n = FakeNotifier()
    p = Pipeline(cfg, n, sync=True, check_urls=False, env_path=env)
    p.handle_event({"type": "text", "text": "/styles"})
    files = [m for m in n.sent if m["kind"] != "text"]
    assert files and files[-1]["path"].endswith("styles_sheet.jpg") and Path(files[-1]["path"]).exists()
    p.handle_event({"type": "text", "text": "/style nope"})
    assert "Usage: /style NAME" in n.sent[-1]["text"] and cfg.caption_style == "poppins"
    p.handle_event({"type": "text", "text": "/style Anton"})
    assert cfg.caption_style == "anton" and "AWN_CAPTION_STYLE=anton" in env.read_text()
    assert set(STYLES) >= {"poppins", "anton"}
