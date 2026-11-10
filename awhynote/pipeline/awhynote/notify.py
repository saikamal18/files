"""Notifier backends. All speak the same tiny interface so the pipeline never knows which one it talks to.

Incoming events (from poll()):
  {"type": "text", "text": str}
  {"type": "callback", "id": str, "data": str}
  {"type": "file", "kind": "photo|document|video", "file_id": str, "name": str, "caption": str, "size": int}
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

TG_TEXT_LIMIT = 4000
TG_UPLOAD_LIMIT = 49 * 1024 * 1024
TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024


class TelegramError(RuntimeError):
    pass


class TelegramAPI:
    def __init__(self, token: str, session=None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"
        self.http = session or requests

    def call(self, method: str, _timeout: float = 40, **params):
        r = self.http.post(f"{self.base}/{method}", json=params, timeout=_timeout)
        data = r.json()
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', data)}")
        return data["result"]

    def upload(self, method: str, field: str, path: Path, timeout: float = 600, **params):
        with open(path, "rb") as f:
            r = self.http.post(f"{self.base}/{method}", data=params, files={field: (path.name, f)}, timeout=timeout)
        data = r.json()
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', data)}")
        return data["result"]

    def download(self, file_id: str) -> bytes:
        info = self.call("getFile", file_id=file_id)
        r = self.http.get(f"{self.file_base}/{info['file_path']}", timeout=120)
        if r.status_code != 200:
            raise TelegramError(f"download failed: HTTP {r.status_code}")
        return r.content


class Notifier:
    long_polls = True  # poll() blocks until something arrives

    def send_text(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None: ...

    def send_file(self, path: Path, caption: str = "", kind: str = "document") -> None: ...

    def poll(self, timeout: float = 0) -> list[dict]:
        return []

    def download(self, file_id: str) -> bytes:
        raise NotImplementedError

    def answer(self, callback_id: str, text: str = "") -> None: ...


class ConsoleNotifier(Notifier):
    """Prints to the terminal; used by `awhynote console` for testing without Telegram."""

    long_polls = False

    def __init__(self):
        self.inbox: list[dict] = []

    def send_text(self, text, buttons=None):
        print("\n" + "─" * 60 + "\n" + text)
        if buttons:
            print("   buttons: " + "  ".join(f"[{lbl}]" for row in buttons for lbl, _ in row))

    def send_file(self, path, caption="", kind="document"):
        print(f"\n📎 {kind}: {path}" + (f"\n   {caption}" if caption else ""))

    def poll(self, timeout=0):
        out, self.inbox = self.inbox, []
        return out


class TelegramNotifier(Notifier):
    def __init__(self, api: TelegramAPI, chat_id: str):
        self.api = api
        self.chat_id = str(chat_id)
        self.offset = 0
        self._last_error = ""

    @staticmethod
    def _clean(text: str) -> str:
        return text.replace("`", "")

    def send_text(self, text, buttons=None):
        text = self._clean(text)
        chunks = [text[i:i + TG_TEXT_LIMIT] for i in range(0, len(text), TG_TEXT_LIMIT)] or [""]
        for i, chunk in enumerate(chunks):
            params = {"chat_id": self.chat_id, "text": chunk, "disable_web_page_preview": True}
            if buttons and i == len(chunks) - 1:
                params["reply_markup"] = {"inline_keyboard": [[{"text": lbl, "callback_data": data} for lbl, data in row] for row in buttons]}
            self.api.call("sendMessage", **params)

    def send_file(self, path, caption="", kind="document"):
        path = Path(path)
        if path.stat().st_size > TG_UPLOAD_LIMIT:
            self.send_text(f"📦 {path.name} is {path.stat().st_size / 1e6:.0f} MB, over Telegram's 50 MB bot limit.\nFind it at: {path}")
            return
        method, field = {"photo": ("sendPhoto", "photo"), "video": ("sendVideo", "video"), "audio": ("sendAudio", "audio")}.get(kind, ("sendDocument", "document"))
        params = {"chat_id": self.chat_id}
        if caption:
            params["caption"] = self._clean(caption)[:1000]
        if kind == "video":
            params["supports_streaming"] = "true"
        self.api.upload(method, field, path, **params)

    def answer(self, callback_id, text=""):
        try:
            self.api.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:150])
        except TelegramError:
            pass

    def download(self, file_id):
        return self.api.download(file_id)

    def poll(self, timeout=25):
        try:
            updates = self.api.call("getUpdates", _timeout=timeout + 15, timeout=int(timeout), offset=self.offset, allowed_updates=["message", "callback_query"])
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            if msg != self._last_error:  # log each distinct failure once, not every retry
                print(f"[telegram] poll failed ({msg}); retrying", flush=True)
                self._last_error = msg
            time.sleep(3)
            return []
        self._last_error = ""
        events = []
        for u in updates:
            self.offset = max(self.offset, u["update_id"] + 1)
            cb = u.get("callback_query")
            if cb:
                if str(((cb.get("message") or {}).get("chat") or {}).get("id")) == self.chat_id:
                    events.append({"type": "callback", "id": cb["id"], "data": cb.get("data", "")})
                continue
            m = u.get("message")
            if not m or str(m["chat"]["id"]) != self.chat_id:
                continue
            cap = m.get("caption", "")
            if m.get("photo"):
                p = m["photo"][-1]
                events.append({"type": "file", "kind": "photo", "file_id": p["file_id"], "name": "photo.jpg", "caption": cap, "size": p.get("file_size", 0)})
            elif m.get("video"):
                v = m["video"]
                events.append({"type": "file", "kind": "video", "file_id": v["file_id"], "name": v.get("file_name", "video.mp4"), "caption": cap, "size": v.get("file_size", 0)})
            elif m.get("document"):
                d = m["document"]
                events.append({"type": "file", "kind": "document", "file_id": d["file_id"], "name": d.get("file_name", "file"), "caption": cap, "size": d.get("file_size", 0)})
            elif m.get("text"):
                events.append({"type": "text", "text": m["text"]})
        return events


class FakeNotifier(Notifier):
    """Records everything; tests push events into `.inbox`."""

    long_polls = False

    def __init__(self, files: dict[str, bytes] | None = None):
        self.sent: list[dict] = []
        self.inbox: list[dict] = []
        self.answers: list[tuple[str, str]] = []
        self.files = files or {}

    def send_text(self, text, buttons=None):
        self.sent.append({"kind": "text", "text": text, "buttons": buttons})

    def send_file(self, path, caption="", kind="document"):
        self.sent.append({"kind": kind, "path": str(path), "caption": caption})

    def answer(self, callback_id, text=""):
        self.answers.append((callback_id, text))

    def poll(self, timeout=0):
        out, self.inbox = self.inbox, []
        return out

    def download(self, file_id):
        return self.files[file_id]

    def last_buttons(self):
        for m in reversed(self.sent):
            if m.get("buttons"):
                return m["buttons"]
        return None
