import csv
from dataclasses import replace
from datetime import datetime

import pytest

from awhynote import media, research
from awhynote.notify import FakeNotifier
from awhynote.pipeline import Pipeline

from conftest import image_bytes


def press(p, n, act, which=-1):
    """Click the button whose action is `act` on the newest message that has one."""
    for m in reversed(n.sent):
        if m.get("buttons"):
            for row in m["buttons"]:
                for _, data in row:
                    if data.endswith(f":{act}"):
                        return p.handle_event({"type": "callback", "id": "cb", "data": data})
    raise AssertionError(f"no {act} button on any message")


def say(p, text):
    p.handle_event({"type": "text", "text": text})


def texts(n):
    return "\n".join(m.get("text", "") for m in n.sent if m["kind"] == "text")


def new_pipeline(cfg, files=None):
    n = FakeNotifier(files=files or {})
    return Pipeline(cfg, n, sync=True, check_urls=False), n


@pytest.fixture
def at_media(cfg):
    p, n = new_pipeline(cfg, {f"f{i}": image_bytes(1200, 1600 if i % 2 else 900, seed=i) for i in range(1, 12)})
    p.start_episode("M01")
    press(p, n, "approve")  # research
    press(p, n, "approve")  # script
    press(p, n, "approve")  # voice
    ep = p.get("M01")
    assert (ep.stage, ep.status) == ("media", "collecting")
    return p, n


def test_gates_in_order_and_nothing_advances_without_approval(cfg):
    cfg.max_active = 1  # keep the scheduler from opening other episodes in this test
    p, n = new_pipeline(cfg)
    p.start_episode("M01")
    ep = p.get("M01")
    assert (ep.stage, ep.status) == ("research", "awaiting_approval")
    assert "Gate 1" in texts(n)
    p.tick(datetime(2026, 11, 20, 3, 0))  # quiet hours: no reminders, and no advance either
    assert p.get("M01").stage == "research"
    press(p, n, "approve")
    ep = p.get("M01")
    assert (ep.stage, ep.status) == ("script", "awaiting_approval")
    assert "Gate 2" in texts(n)
    assert ep.state["gate"]["research"]["version"] == 1


def test_edit_at_script_gate_feeds_back_and_bumps_version(cfg):
    p, n = new_pipeline(cfg)
    p.start_episode("M01")
    press(p, n, "approve")
    assert p.get("M01").version() == 1
    press(p, n, "edit")
    assert p.get("M01").state["pending_edit"]["stage"] == "script"
    say(p, "Make the hook punchier")
    ep = p.get("M01")
    assert ep.version() == 2 and ep.feedback("script") == ["Make the hook punchier"]
    assert ep.status == "awaiting_approval" and "pending_edit" not in ep.state


def test_stale_buttons_are_refused(cfg):
    p, n = new_pipeline(cfg)
    p.start_episode("M01")
    old = n.last_buttons()[0][0][1]  # research approve
    press(p, n, "approve")
    n.answers.clear()
    p.handle_event({"type": "callback", "id": "x", "data": old})
    assert p.get("M01").stage == "script"
    assert "out of date" in n.answers[-1][1]
    p.handle_event({"type": "callback", "id": "y", "data": "garbage"})
    assert n.answers[-1][1] == "Unknown button"


def test_voice_edit_changes_rate_without_touching_script(cfg):
    p, n = new_pipeline(cfg)
    p.start_episode("M01")
    press(p, n, "approve")
    press(p, n, "approve")
    assert p.get("M01").stage == "voice"
    press(p, n, "edit")
    say(p, "rate +8%")
    ep = p.get("M01")
    assert ep.stage == "voice" and ep.state["voice_overrides"]["rate"] == "+8%" and ep.version() == 2
    assert ep.version("script") == 1
    assert any(m["kind"] == "audio" for m in n.sent)


def test_free_text_when_nothing_pending(cfg):
    p, n = new_pipeline(cfg)
    say(p, "hello?")
    assert "wasn't waiting" in texts(n)


def test_skip_topic_is_final(cfg):
    cfg.max_active = 1
    p, n = new_pipeline(cfg)
    p.start_episode("M01")
    press(p, n, "skip")
    assert p.get("M01").status == "dropped"
    started = p.tick(datetime(2026, 11, 20, 10, 0))["started"]
    assert "M01" not in started


def test_failure_is_reported_and_retry_works(cfg, monkeypatch):
    p, n = new_pipeline(cfg)
    real = research.run
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("API key rejected")
        return real(*a, **k)

    monkeypatch.setattr(research, "run", flaky)
    p.start_episode("M01")
    ep = p.get("M01")
    assert ep.status == "failed" and "API key rejected" in ep.state["error"]
    assert "failed" in texts(n)
    press(p, n, "redo")
    assert p.get("M01").status == "awaiting_approval"


def test_scheduler_respects_lead_window_and_capacity(cfg):
    cfg.max_active = 2
    p, n = new_pipeline(cfg)
    r = p.tick(datetime(2026, 11, 20, 10, 0))
    assert len(r["started"]) == 2 and r["started"][0] == "M01"
    assert p.tick(datetime(2026, 11, 20, 10, 5))["started"] == []  # at capacity until something is approved/finished
    far = Pipeline(replace(cfg, data_dir=cfg.data_dir.parent / "data2"), FakeNotifier(), sync=True, check_urls=False)
    assert far.tick(datetime(2026, 11, 1, 10, 0))["started"] == []  # more than 5 days before the first post


def test_reminders_obey_quiet_hours_and_cap(cfg):
    cfg.max_active = 1
    cfg.reminder_hours = 0
    cfg.max_reminders = 2
    p, n = new_pipeline(cfg)
    p.start_episode("M01")
    assert p.tick(datetime(2026, 11, 20, 23, 30))["reminded"] == []
    assert p.tick(datetime(2026, 11, 20, 10, 0))["reminded"] == ["M01"]
    assert p.tick(datetime(2026, 11, 20, 11, 0))["reminded"] == ["M01"]
    assert p.tick(datetime(2026, 11, 20, 12, 0))["reminded"] == []
    assert "Still waiting" in texts(n)


# ---- media stage ------------------------------------------------------------
def media_shots(p):
    sc = p.get("M01").read("script.json")
    return [s["id"] for s in sc["shots"] if s["style"] != "text"]


def test_done_is_blocked_until_every_shot_has_media_or_skip(at_media):
    p, n = at_media
    assert "Shot list" in texts(n)
    press(p, n, "done")
    assert p.get("M01").status == "collecting" and "still missing" in texts(n)


def test_file_intake_commands_and_full_media_flow(at_media):
    p, n = at_media
    shots = media_shots(p)
    p.handle_event({"type": "file", "kind": "photo", "file_id": "f1", "name": "photo.jpg", "caption": str(shots[0]), "size": 5000})
    p.handle_event({"type": "file", "kind": "document", "file_id": "f2", "name": "scan.png", "caption": f"s{shots[1]}", "size": 5000})
    say(p, f"/callout {shots[1]} 0.5 0.4 0.1")
    for s in shots[2:]:
        say(p, f"/skip {s}")
    assert media.scan(p.get("M01"), p.get("M01").read("script.json"))["ready"]
    press(p, n, "done")
    ep = p.get("M01")
    assert (ep.stage, ep.status) == ("media", "awaiting_approval")
    assert any(m["kind"] == "photo" and "contact" in m["caption"].lower() for m in n.sent)
    man = ep.read("media.json")
    assert man["shots"][shots[1] - 1]["callout"]["r"] == 0.1


def test_caption_missing_big_file_and_unknown_shot(at_media):
    p, n = at_media
    p.handle_event({"type": "file", "kind": "photo", "file_id": "f1", "name": "photo.jpg", "caption": "", "size": 10})
    assert "shot number" in texts(n)
    p.handle_event({"type": "file", "kind": "video", "file_id": "big", "name": "v.mp4", "caption": "1", "size": 40_000_000})
    assert "20 MB" in texts(n) and str(media.inbox(p.get("M01"))) in texts(n)
    p.handle_event({"type": "file", "kind": "photo", "file_id": "f1", "name": "photo.jpg", "caption": "77", "size": 10})
    assert "no shot 77" in texts(n)


def test_url_intake_and_clear(at_media, monkeypatch):
    p, n = at_media

    class Resp:
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            yield image_bytes(800, 600)

    monkeypatch.setattr(media.requests, "get", lambda *a, **k: Resp())
    say(p, "1 https://example.org/photo.jpg")
    ep = p.get("M01")
    assert len(list(media.inbox(ep).glob("s1_*"))) == 1
    say(p, "/clear 1")
    assert not list(media.inbox(ep).glob("s1_*")) and list(ep.path("media/trash").glob("s1_*"))


def test_edit_at_media_review_reopens_collecting(at_media):
    p, n = at_media
    for s in media_shots(p):
        say(p, f"/skip {s}")
    press(p, n, "done")
    press(p, n, "edit")
    say(p, "replace the third picture")
    ep = p.get("M01")
    assert ep.status == "collecting" and ep.feedback("media") == ["replace the third picture"]


def test_whole_pipeline_to_delivery(at_media, cfg):
    p, n = at_media
    shots = media_shots(p)
    for i, s in enumerate(shots[:3], 1):
        p.handle_event({"type": "file", "kind": "photo", "file_id": f"f{i}", "name": "photo.jpg", "caption": str(s), "size": 1000})
    for s in shots[3:]:
        say(p, f"/skip {s}")
    press(p, n, "done")
    press(p, n, "approve")  # media -> render (sync: renders now)
    ep = p.get("M01")
    assert (ep.stage, ep.status) == ("render", "awaiting_approval")
    assert any(m["kind"] == "video" and m["path"].endswith("preview.mp4") for m in n.sent)
    press(p, n, "approve")  # render -> package
    ep = p.get("M01")
    assert (ep.stage, ep.status) == ("package", "awaiting_approval")
    assert "Caption" in texts(n) and ep.path("cover.jpg").exists()
    before = len(n.sent)
    press(p, n, "approve")  # final approval -> delivery
    ep = p.get("M01")
    assert ep.status == "done"
    n.sent = n.sent[before:]
    kinds = [(m["kind"], m.get("path", "")[-9:]) for m in n.sent if m["kind"] != "text"]
    assert ("document", "final.mp4") in kinds
    assert "AI-generated" in texts(n)
    rows = list(csv.DictReader(open(cfg.tracker_csv, encoding="utf-8-sig")))
    assert rows[0]["topic_id"] == "M01" and rows[0]["date"] == "2026-11-23"
    assert p.tick(datetime(2026, 11, 20, 10, 0))["started"] != ["M01"]


def test_render_gate_edit_media_vs_script(at_media):
    p, n = at_media
    ep = p.get("M01")
    with p.edit("M01") as e:
        e.set("render", "awaiting_approval")
        e.state["pending_edit"] = {"stage": "render", "at": 1}
    say(p, "media: swap shot 2")
    assert (p.get("M01").stage, p.get("M01").status) == ("media", "collecting")
    with p.edit("M01") as e:
        e.set("render", "awaiting_approval")
        e.state["pending_edit"] = {"stage": "render", "at": 2}
    say(p, "the second fact is wrong")
    ep = p.get("M01")
    assert ep.stage == "script" and ep.feedback("script") == ["the second fact is wrong"]
