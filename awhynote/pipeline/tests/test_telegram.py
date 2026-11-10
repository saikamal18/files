import json

import pytest

from awhynote import notify
from awhynote.notify import TelegramAPI, TelegramError, TelegramNotifier
from awhynote.pipeline import Pipeline

from conftest import image_bytes


class Resp:
    def __init__(self, payload=None, status=200, content=b""):
        self.payload, self.status_code, self.content = payload, status, content

    def json(self):
        return self.payload


class FakeHTTP:
    """Stands in for `requests`: records calls and serves queued updates."""

    def __init__(self, files=None):
        self.calls = []
        self.updates = []
        self.files = files or {}
        self.fail_with = None

    def post(self, url, json=None, data=None, files=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        self.calls.append({"method": method, "json": json, "data": data, "files": list(files or {}), "timeout": timeout})
        if self.fail_with:
            return Resp({"ok": False, "description": self.fail_with})
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return Resp({"ok": True, "result": out})
        if method == "getFile":
            return Resp({"ok": True, "result": {"file_path": f"docs/{json['file_id']}"}})
        return Resp({"ok": True, "result": {"message_id": 1}})

    def get(self, url, timeout=None):
        name = url.rsplit("/", 1)[-1]
        if name in self.files:
            return Resp(status=200, content=self.files[name])
        return Resp(status=404)

    def sent(self, method):
        return [c for c in self.calls if c["method"] == method]


@pytest.fixture
def tg():
    http = FakeHTTP()
    return http, TelegramNotifier(TelegramAPI("TOKEN", session=http), "42")


def msg(uid, chat=42, **kw):
    return {"update_id": uid, "message": {"chat": {"id": chat}, **kw}}


def test_send_text_builds_keyboard_and_strips_backticks(tg):
    http, n = tg
    n.send_text("run `awhynote status`", buttons=[[("✅ Approve", "A:M01:research:1:approve"), ("🔁 Redo", "A:M01:research:1:redo")]])
    (c,) = http.sent("sendMessage")
    assert c["json"]["chat_id"] == "42" and "`" not in c["json"]["text"]
    kb = c["json"]["reply_markup"]["inline_keyboard"]
    assert kb[0][0] == {"text": "✅ Approve", "callback_data": "A:M01:research:1:approve"}
    assert all(len(b["callback_data"]) <= 64 for row in kb for b in row)


def test_long_text_is_split_and_buttons_go_on_the_last_chunk(tg):
    http, n = tg
    n.send_text("x" * 9000, buttons=[[("OK", "A:M01:script:1:approve")]])
    calls = http.sent("sendMessage")
    assert [len(c["json"]["text"]) for c in calls] == [4000, 4000, 1000]
    assert [("reply_markup" in c["json"]) for c in calls] == [False, False, True]


def test_poll_parses_every_event_type_and_advances_offset(tg):
    http, n = tg
    http.updates = [
        msg(10, text="/status"),
        msg(11, photo=[{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 999}], caption="3"),
        msg(12, document={"file_id": "d1", "file_name": "a.heic", "file_size": 5}, caption="M01 2"),
        msg(13, video={"file_id": "v1", "file_size": 7}),
        {"update_id": 14, "callback_query": {"id": "cb1", "data": "A:M01:research:1:approve", "message": {"chat": {"id": 42}}}},
        msg(15, text="intruder", chat=999),
        {"update_id": 16, "callback_query": {"id": "cb2", "data": "A:M01:research:1:approve", "message": {"chat": {"id": 999}}}},
        {"update_id": 17, "callback_query": {"id": "cb3", "data": "x"}},
    ]
    ev = n.poll(timeout=0)
    assert [e["type"] for e in ev] == ["text", "file", "file", "file", "callback"]
    assert ev[1] == {"type": "file", "kind": "photo", "file_id": "big", "name": "photo.jpg", "caption": "3", "size": 999}
    assert ev[2]["kind"] == "document" and ev[2]["name"] == "a.heic" and ev[2]["caption"] == "M01 2"
    assert ev[3]["name"] == "video.mp4"
    assert ev[4] == {"type": "callback", "id": "cb1", "data": "A:M01:research:1:approve"}
    assert n.offset == 18
    n.poll(timeout=0)
    assert http.sent("getUpdates")[-1]["json"]["offset"] == 18


def test_poll_survives_network_errors(tg, monkeypatch, capsys):
    http, n = tg
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)
    http.fail_with = "Unauthorized"
    assert n.poll(timeout=0) == []
    assert n.poll(timeout=0) == []
    assert capsys.readouterr().out.count("poll failed") == 1  # same error is logged once
    http.fail_with = None
    http.updates = [msg(1, text="hi")]
    assert n.poll(timeout=0)[0]["text"] == "hi"


def test_api_errors_raise(tg):
    http, n = tg
    http.fail_with = "Bad Request: chat not found"
    with pytest.raises(TelegramError, match="chat not found"):
        n.send_text("hello")


def test_send_file_methods_and_size_guard(tg, tmp_path, monkeypatch):
    http, n = tg
    f = tmp_path / "final.mp4"
    f.write_bytes(b"x" * 100)
    n.send_file(f, caption="Final `cut`", kind="document")
    n.send_file(f, kind="video")
    n.send_file(f, kind="photo")
    assert [c["method"] for c in http.calls] == ["sendDocument", "sendVideo", "sendPhoto"]
    assert http.calls[0]["data"]["caption"] == "Final cut" and http.calls[0]["files"] == ["document"]
    assert http.calls[1]["data"]["supports_streaming"] == "true"
    http.calls.clear()
    monkeypatch.setattr(notify, "TG_UPLOAD_LIMIT", 50)
    n.send_file(f)
    assert not http.sent("sendDocument")
    assert "over Telegram's 50 MB" in http.sent("sendMessage")[0]["json"]["text"]


def test_download_via_getfile(tg):
    http, n = tg
    http.files = {"abc": b"bytes!"}
    assert n.download("abc") == b"bytes!"
    with pytest.raises(TelegramError, match="HTTP 404"):
        n.download("missing")


def test_answer_callback_never_raises(tg):
    http, n = tg
    http.fail_with = "query is too old"
    n.answer("cb1", "ok")


def test_pipeline_over_fake_telegram(cfg):
    cfg.max_active = 1
    http = FakeHTTP(files={f"f{i}": image_bytes(900, 1200, seed=i) for i in range(1, 4)})
    n = TelegramNotifier(TelegramAPI("TOKEN", session=http), "42")
    p = Pipeline(cfg, n, sync=True, check_urls=False)

    def buttons():
        for c in reversed(http.sent("sendMessage")):
            kb = (c["json"].get("reply_markup") or {}).get("inline_keyboard")
            if kb:
                return {b["text"].split()[-1].lower(): b["callback_data"] for row in kb for b in row}
        return {}

    def push(uid, **upd):
        http.updates = [{"update_id": uid, **upd}]
        for e in n.poll(timeout=0):
            p.handle_event(e)

    p.start_episode("M01")
    cb = buttons()["approve"]
    push(1, callback_query={"id": "c1", "data": cb, "message": {"chat": {"id": 42}}})
    assert p.get("M01").stage == "script"
    # A stale tap on the old research button is refused, and a stranger's tap is ignored entirely.
    push(2, callback_query={"id": "c2", "data": cb, "message": {"chat": {"id": 42}}})
    assert p.get("M01").stage == "script"
    push(3, callback_query={"id": "c3", "data": buttons()["approve"], "message": {"chat": {"id": 7}}})
    assert p.get("M01").stage == "script"
    assert any(c["json"].get("text", "").startswith("That button is out of date") for c in http.sent("answerCallbackQuery"))
    push(4, message={"chat": {"id": 42}, "text": "/status"})
    assert "M01" in http.sent("sendMessage")[-1]["json"]["text"]
    json.dumps(http.calls[-1]["json"])  # every payload must be JSON-serialisable
