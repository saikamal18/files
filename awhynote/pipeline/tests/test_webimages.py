import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from awhynote import media, webimages
from awhynote.store import Episode

from conftest import image_bytes
from test_pipeline import media_shots, new_pipeline, press, say, texts

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "commons_charminar.json").read_text(encoding="utf-8"))  # a real Commons API reply


class Resp:
    def __init__(self, status=200, body=b"", ctype="image/jpeg", data=None):
        self.status_code, self._body, self.headers, self._data = status, body, {"content-type": ctype}, data

    def json(self):
        return self._data

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]


class FakeWeb:
    """Answers Commons searches with the recorded reply, Openverse with an error, and any image URL with a synthetic photo."""

    def __init__(self, image=None, ctype="image/jpeg"):
        self.calls, self.image, self.ctype = [], image, ctype

    def get(self, url, params=None, timeout=None, headers=None, stream=False):
        self.calls.append(url)
        if url.startswith(webimages.COMMONS_API):
            if not (params or {}).get("gsrsearch", "").startswith("Charminar"):  # other queries get their own, distinct pictures
                tag = str(abs(hash((params or {}).get("gsrsearch", ""))))
                data = json.loads(json.dumps(FIXTURE))
                for page in data["query"]["pages"].values():
                    ii = page["imageinfo"][0]
                    ii["thumburl"] += "&v=" + tag
                    ii["url"] += "&v=" + tag
                return Resp(data=data)
            return Resp(data=FIXTURE)
        if url.startswith(webimages.OPENVERSE_API):
            return Resp(status=504)
        seed = sum(map(ord, url)) % 50
        return Resp(body=self.image or image_bytes(1600, 2000, seed=seed), ctype=self.ctype)


@pytest.fixture
def web(monkeypatch):
    fake = FakeWeb()
    monkeypatch.setattr(webimages, "requests", fake)
    return fake


@pytest.mark.parametrize("name,strict,tier", [
    ("CC0", False, 3), ("Public domain", False, 3), ("PD-old-70", False, 3), ("Pexels License", False, 3),
    ("CC BY 4.0", False, 2), ("CC BY-SA 3.0", False, 1), ("CC BY-SA 3.0", True, None), ("Creative Commons Attribution-Share Alike 3.0", False, 1),
    ("CC BY-NC 2.0", False, None), ("CC BY-ND 4.0", False, None), ("Fair use", False, None), ("", False, None), ("GFDL", False, None),
])
def test_license_tiers(name, strict, tier):
    assert webimages.license_tier(name, strict) == tier


def test_banknote_topics_are_never_fetched():
    assert webimages.is_note_topic("Macro of the ₹10 note's Ashoka pillar")
    assert webimages.is_note_topic("security thread of a banknote")
    assert webimages.is_note_topic("an old rupee note")
    assert not webimages.is_note_topic("Charminar at dusk, Hyderabad 1938")
    assert not webimages.is_note_topic("a note about history")


def test_heuristic_queries_use_place_year_and_brief():
    qs = webimages.heuristic_queries({"brief": "Close-up of the Charminar arches at dusk", "location_tag": "HYDERABAD", "year_stamp": "1938"}, "The Charminar story")
    assert qs[0].startswith("Hyderabad Charminar arches") and any("1938" in q for q in qs) and len(qs) <= 4


def test_note_episodes_do_not_search_by_their_own_title():
    qs = webimages.heuristic_queries({"brief": "Hyderabad skyline", "location_tag": "", "year_stamp": ""}, "The temple hiding on your ₹10 note")
    assert qs == ["Hyderabad skyline"]


def test_commons_reply_is_parsed():
    cands = webimages.search_commons(FakeWeb(), "Charminar")
    assert len(cands) == 4
    c = sorted(cands, key=lambda c: c.rank)[0]
    assert c.title == "Charminar Hyderabad 1" and c.license == "CC BY-SA 3.0" and "<" not in c.author and c.author
    assert c.url.startswith("https://") and "1920px" in c.url or "1600px" in c.url
    assert c.page_url.startswith("https://commons.wikimedia.org/wiki/File:") and (c.width, c.height) == (2174, 3071)


def test_pick_prefers_big_portrait_and_honours_strict_and_skip(cfg):
    c = replace(cfg, web_sources="commons")
    web = FakeWeb()
    best = webimages.pick(c, ["Charminar"], set(), web)
    assert best and best.title == "Charminar Hyderabad 1"
    assert webimages.pick(c, ["Charminar"], {best.url}, web).url != best.url
    assert webimages.pick(replace(c, web_licenses="strict"), ["Charminar"], set(), web) is None  # all are BY-SA


def test_unusable_candidates_are_filtered():
    mk = lambda **kw: webimages.Candidate(**{"source": "x", "title": "t", "url": "https://a.org/i.jpg", "page_url": "", "author": "", "license": "CC BY 4.0", "width": 2000, "height": 3000, **kw})
    assert webimages._usable(mk(), False)
    assert not webimages._usable(mk(url="http://a.org/i.jpg"), False)
    assert not webimages._usable(mk(url="https://127.0.0.1/i.jpg"), False)
    assert not webimages._usable(mk(url="https://localhost/i.jpg"), False)
    assert not webimages._usable(mk(width=500, height=600), False)
    assert not webimages._usable(mk(license="CC BY-NC 2.0"), False)
    assert not webimages._usable(mk(title="Indian 10 rupee banknote 2018"), False)
    assert not webimages._usable(mk(title="Currency of India"), False)


def test_download_checks_type_size_and_dimensions():
    c = webimages.Candidate("x", "t", "https://a.org/i.jpg", "", "", "CC0", 0, 0)
    data, ext = webimages.download(c, FakeWeb())
    assert ext == ".jpg" and Image.open(io.BytesIO(data)).size == (1600, 2000)
    with pytest.raises(ValueError, match="not a photo"):
        webimages.download(c, FakeWeb(ctype="text/html"))
    with pytest.raises(ValueError, match="too small"):
        webimages.download(c, FakeWeb(image=image_bytes(300, 300)))
    with pytest.raises(Exception):
        webimages.download(c, FakeWeb(image=b"not an image at all"))
    big = FakeWeb(image=b"\0" * (webimages.MAX_BYTES + 10))
    with pytest.raises(ValueError, match="15 MB"):
        webimages.download(c, big)


def _episode(cfg, shots):
    ep = Episode.create(cfg.episodes_dir, {"id": "W01", "hook_title": "The Charminar story", "pillar": "History"}, "2026-12-01")
    sc = {"shots": shots, "beats": []}
    ep.write("script.json", sc)
    return ep, sc


def test_fill_adds_files_credits_and_never_touches_the_web_for_banknotes(cfg, web):
    cfg = replace(cfg, web_sources="commons")
    shots = [
        {"id": 1, "visual_type": "archival", "style": "full", "brief": "Charminar arches at dusk", "stat_text": "", "year_stamp": "", "location_tag": "HYDERABAD"},
        {"id": 2, "visual_type": "macro", "style": "full", "brief": "Macro of the ₹10 note's Ashoka pillar", "stat_text": "", "year_stamp": "", "location_tag": ""},
        {"id": 3, "visual_type": "stock", "style": "full", "brief": "Hyderabad street at night", "stat_text": "", "year_stamp": "", "location_tag": ""},
    ]
    ep, sc = _episode(cfg, shots)
    res = webimages.fill(cfg, ep, sc, [1, 2, 3])
    assert [r.ok for r in res] == [True, False, True]
    assert "banknote" in res[1].message and "photograph the real one" in res[1].message
    scanned = media.scan(ep, sc)
    assert scanned["missing"] == [2] and len(scanned["shots"][1]["files"]) == 1
    assert Path(scanned["shots"][1]["files"][0]).name.startswith("s1_web-") and Image.open(scanned["shots"][1]["files"][0]).size == (1600, 2000)
    lines = webimages.credit_lines(ep)
    assert lines.startswith("Images:") and "CC BY-SA 3.0" in lines and "commons.wikimedia.org" in lines and "shot 2" not in lines
    first, third = webimages.state(ep)["shots"]["1"]["tried"], webimages.state(ep)["shots"]["3"]["tried"]
    assert not set(first) & set(third)  # the same picture is never used twice
    assert all("ashoka" not in u.lower() for u in web.calls)


def test_discard_then_fill_gives_a_different_picture_and_a_removed_file_loses_its_credit(cfg, web):
    cfg = replace(cfg, web_sources="commons")
    ep, sc = _episode(cfg, [{"id": 1, "visual_type": "stock", "style": "full", "brief": "Charminar arches", "stat_text": "", "year_stamp": "", "location_tag": ""}])
    a = webimages.fill(cfg, ep, sc, [1])[0]
    webimages.discard(ep, 1)
    assert not a.path.exists() and media.scan(ep, sc)["missing"] == [1] and webimages.credit_lines(ep) == ""
    b = webimages.fill(cfg, ep, sc, [1])[0]
    assert b.ok and b.message != a.message
    b.path.unlink()  # e.g. /clear
    assert webimages.credit_lines(ep) == ""


def test_no_result_is_reported_not_raised(cfg, monkeypatch):
    class Empty(FakeWeb):
        def get(self, url, **kw):
            return Resp(data={"query": {"pages": {}}}) if url.startswith(webimages.COMMONS_API) else Resp(status=504)

    monkeypatch.setattr(webimages, "requests", Empty())
    ep, sc = _episode(replace(cfg, web_sources="commons"), [{"id": 1, "visual_type": "stock", "style": "full", "brief": "Nothing here", "stat_text": "", "year_stamp": "", "location_tag": ""}])
    (r,) = webimages.fill(replace(cfg, web_sources="commons"), ep, sc, [1])
    assert not r.ok and "no suitable" in r.message


def test_openverse_and_pexels_replies_are_parsed():
    class Ov(FakeWeb):
        def get(self, url, **kw):
            if url.startswith(webimages.OPENVERSE_API):
                return Resp(data={"results": [
                    {"title": "Old fort", "url": "https://live.staticflickr.com/a.jpg", "foreign_landing_url": "https://flickr.com/p/1", "creator": "Ann", "license": "by-sa", "license_version": "2.0", "width": 2000, "height": 1500, "filetype": "jpg"},
                    {"title": "Vector", "url": "https://x.org/a.svg", "license": "cc0", "filetype": "svg"}]})
            return Resp(data={"photos": [{"id": 7, "alt": "A bridge", "url": "https://www.pexels.com/photo/7", "photographer": "Bo", "width": 3000, "height": 4000,
                                          "src": {"large2x": "https://images.pexels.com/p7.jpeg"}}]})

    (o,) = webimages.search_openverse(Ov(), "fort")
    assert (o.license, o.author, o.source) == ("CC BY-SA 2.0", "Ann", "Openverse") and webimages.license_tier(o.license) == 1
    (p,) = webimages.search_pexels(Ov(), "KEY", "bridge")
    assert p.license == "Pexels License" and webimages.license_tier(p.license) == 3 and p.url.startswith("https://images.pexels.com/")


# -- through the bot -----------------------------------------------------------
@pytest.fixture
def at_media_web(cfg, web):
    cfg.web_sources = "commons"
    p, n = new_pipeline(cfg, {"mine": image_bytes(1200, 900)})
    p.start_episode("M01")
    for _ in range(3):
        press(p, n, "approve")
    assert (p.get("M01").stage, p.get("M01").status) == ("media", "collecting")
    return p, n


def test_web_button_and_command_fill_missing_shots(at_media_web):
    p, n = at_media_web
    assert "🌐" in texts(n) and any(lbl.startswith("🌐") for m in n.sent for row in (m.get("buttons") or []) for lbl, _ in row)
    before = len(n.sent)
    press(p, n, "web")
    photos = [m for m in n.sent[before:] if m["kind"] == "photo"]
    assert len(photos) == len(media_shots(p)) and all(Path(m["path"]).stat().st_size < 10_000_000 for m in photos)
    assert "Everything has media" in texts(n)
    sc = p.get("M01").read("script.json")
    assert media.scan(p.get("M01"), sc)["missing"] == []


def test_web_for_one_shot_swaps_only_a_fetched_picture(at_media_web):
    p, n = at_media_web
    ids = media_shots(p)
    say(p, f"/web {ids[0]}")
    ep = p.get("M01")
    tried = lambda: webimages.state(p.get("M01"))["shots"][str(ids[0])]["tried"]
    assert len(tried()) == 1 and webimages_file(ep, ids[0])
    say(p, f"/web {ids[0]}")
    assert len(tried()) == 2 and tried()[0] != tried()[1] and webimages_file(p.get("M01"), ids[0])
    assert len(list(p.get("M01").path("media/trash").glob("s*"))) == 1  # the first pick was set aside
    p.handle_event({"type": "file", "kind": "photo", "file_id": "mine", "name": "mine.jpg", "caption": str(ids[1]), "size": 10})
    say(p, f"/web {ids[1]}")
    assert "has your own file" in texts(n)
    say(p, "/web 99")
    assert "no shot 99" in texts(n)


def webimages_file(ep, shot):
    return webimages.web_files(ep).get(shot)


def test_credits_reach_the_sources_comment_and_the_delivery(at_media_web):
    p, n = at_media_web
    say(p, "/web")
    press(p, n, "done")
    press(p, n, "approve")  # media -> render
    press(p, n, "approve")  # render -> package
    ep = p.get("M01")
    assert ep.stage == "package"
    assert "Images:" in ep.path("package/sources.txt").read_text(encoding="utf-8") and "Images:" in texts(n)


def test_auto_mode_fills_when_the_media_gate_opens(cfg, web):
    cfg.web_sources, cfg.web_images = "commons", "auto"
    p, n = new_pipeline(cfg, {})
    p.start_episode("M01")
    for _ in range(3):
        press(p, n, "approve")
    ep = p.get("M01")
    assert (ep.stage, ep.status) == ("media", "collecting")
    assert media.scan(ep, ep.read("script.json"))["missing"] == [] and any(m["kind"] == "photo" for m in n.sent)


def test_off_mode_hides_the_button_and_refuses(cfg, web):
    cfg.web_images = "off"
    p, n = new_pipeline(cfg, {})
    p.start_episode("M01")
    for _ in range(3):
        press(p, n, "approve")
    assert not any("🌐" in lbl for m in n.sent for row in (m.get("buttons") or []) for lbl, _ in row)
    say(p, "/web")
    assert "switched off" in texts(n) and not web.calls
