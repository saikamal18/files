"""The approval-gated state machine: research -> script -> voice -> media -> render -> package.

Nothing moves to the next stage without an explicit approval from the creator. There is no auto-publish.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from . import media, plan, prompts, render, research, script as script_mod, tts, webimages
from .config import ROOT, Config
from .llm import LLM
from .notify import TG_DOWNLOAD_LIMIT, Notifier
from .store import STAGE_LABEL, STAGES, Episode, list_episodes
from .styles import STYLES
from .util import set_env, write_json

log = logging.getLogger("awhynote")
EP_RE = r"[A-Za-z]\d{2}"
ACTIVE_STATUS = {"working", "awaiting_approval", "collecting", "failed"}
WAKING_HOURS = (8, 22)

HELP = """awhynote bot. I run the pipeline and wait for your OK at every gate.

Buttons under each message: ✅ Approve · ✏️ Edit (then type what to change) · 🔄 Redo · ⏭ Skip topic

Commands
/status  what is waiting on you
/new ID  start a topic now (e.g. /new M01)
/restart ID  start a topic over from the research step (the old run is kept in data/archive)
/edit [ID] text  give feedback on the current gate
/approve [ID]  /redo [ID]  /drop ID
Media stage: send photos/videos with the shot number as the caption (3), or `M01 3` if several episodes are open.
/rerender [ID]  at the render or package gate: render again with the current look and music
/styles  see the caption looks · /style NAME  pick one (poppins, montserrat, anton, bebas)
/web [shot]  no photo for a shot? fetch an open-licence picture from the web (/web 3 swaps shot 3 for another one)
/done  I've sent everything · /skip 2  use a text card for shot 2
/callout 4 0.55 0.30 0.10  red circle on shot 4 (x y radius, 0-1) · /clear 4  remove shot 4's files"""

POST_CHECKLIST = """To post (manual on purpose):
1. Instagram → Reel → upload final.mp4 (save from Telegram to Files/Photos first)
2. Pick the cover from the file above; paste the caption; add trending audio at LOW volume if you like
3. Turn ON the 'AI-generated' label (the voice is synthetic)
4. Post the sources as the first comment and pin it
5. Reply to early comments for the first hour"""


def _fmt_date(d: str) -> str:
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return d
    delta = (dt - date.today()).days
    when = "today" if delta == 0 else ("tomorrow" if delta == 1 else (f"in {delta} days" if delta > 1 else f"{-delta} days ago"))
    return f"{dt.strftime('%a %d %b')} ({when})"


class Pipeline:
    def __init__(self, cfg: Config, notifier: Notifier, sync: bool = False, check_urls: bool = True, env_path: Path | None = None):
        self.cfg, self.n, self.sync, self.check_urls = cfg, notifier, sync, check_urls
        self.env_path = env_path or (ROOT / ".env")
        self.lock = threading.RLock()
        self.q: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        cfg.episodes_dir.mkdir(parents=True, exist_ok=True)

    # ---- plumbing --------------------------------------------------------
    @contextmanager
    def edit(self, ep_id: str):
        with self.lock:
            ep = Episode(self.cfg.episodes_dir, ep_id)
            yield ep
            ep.save()

    def get(self, ep_id: str) -> Episode:
        return Episode(self.cfg.episodes_dir, ep_id)

    def episodes(self) -> list[Episode]:
        return list_episodes(self.cfg.episodes_dir)

    def _say(self, text: str, buttons=None) -> None:
        try:
            self.n.send_text(text, buttons)
        except Exception as e:  # a notifier hiccup must never kill the worker
            log.error("send_text failed: %s", e)

    def _file(self, path: Path, caption: str = "", kind: str = "document") -> None:
        try:
            self.n.send_file(path, caption, kind)
        except Exception as e:
            log.error("send_file failed: %s", e)
            self._say(f"⚠️ Could not send {Path(path).name}: {e}\nIt is saved at {path}")

    def start_worker(self) -> None:
        if self.sync or (self._worker and self._worker.is_alive()):
            return

        def loop():
            while not self._stop.is_set():
                try:
                    ep_id = self.q.get(timeout=1)
                except queue.Empty:
                    continue
                try:
                    self._job(ep_id)
                except Exception:
                    log.exception("job crashed for %s", ep_id)

        self._worker = threading.Thread(target=loop, name="awhynote-worker", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()

    def enqueue(self, ep_id: str) -> None:
        if self.sync:
            self._job(ep_id)
        else:
            self.q.put(ep_id)

    def resume(self) -> None:
        """After a restart, re-queue anything that was mid-flight."""
        for ep in self.episodes():
            if ep.status == "working":
                self.enqueue(ep.id)

    # ---- starting / advancing -------------------------------------------
    def start_episode(self, topic_id: str, publish_date: str | None = None, post_time: str = "") -> Episode | None:
        if Episode.exists(self.cfg.episodes_dir, topic_id):
            return None
        topic = plan.topic_for(self.cfg, topic_id)
        if not topic:
            return None
        cal = plan.calendar_row(self.cfg, topic_id)
        pub = publish_date or (cal["date"] if cal else date.today().isoformat())
        pt = post_time or (cal["post_time_IST"] if cal else "")
        with self.lock:
            ep = Episode.create(self.cfg.episodes_dir, topic, pub, pt)
        self._say(f"🆕 Starting {ep.id}: {topic['hook_title']}\n📅 publishes {_fmt_date(pub)} {pt}")
        self._begin(ep.id, "research")
        return ep

    def _begin(self, ep_id: str, stage: str, phase: str | None = None) -> None:
        with self.edit(ep_id) as ep:
            ep.set(stage, "working")
            if phase is None:
                ep.bump(stage)
            ep.state["phase"] = phase
            ep.state.pop("pending_edit", None)
            ep.state.pop("error", None)
            ep.log(f"start {stage}{'/' + phase if phase else ''} v{ep.version(stage)}")
        self.enqueue(ep_id)

    def _job(self, ep_id: str) -> None:
        with self.lock:
            ep = self.get(ep_id)
            if ep.status != "working":
                return
            stage, ver, phase = ep.stage, ep.version(), ep.state.get("phase")
        try:
            outcome = self._execute(ep, stage, phase)
        except Exception as e:
            log.exception("%s failed at %s", ep_id, stage)
            with self.edit(ep_id) as e2:
                if e2.stage == stage and e2.version(stage) == ver:
                    e2.set(status="failed")
                    e2.state["error"] = f"{type(e).__name__}: {e}"[:600]
                    e2.state["last_notified"] = time.time()
                    e2.state["reminders"] = 0
            self._send_gate(ep_id)
            return
        with self.edit(ep_id) as e2:
            if e2.stage != stage or e2.version(stage) != ver or e2.status != "working":
                return  # superseded while we were working
            e2.set(status="collecting" if outcome == "collect" else "awaiting_approval")
            e2.state["phase"] = None
            e2.state["last_notified"] = time.time()
            e2.state["reminders"] = 0
            e2.log(f"{stage} ready for review")
        self._send_gate(ep_id)

    def _execute(self, ep: Episode, stage: str, phase: str | None) -> str:
        cfg = self.cfg
        if stage == "research":
            research.run(cfg, ep, check_urls=self.check_urls)
        elif stage == "script":
            script_mod.run(cfg, ep, plan.next_title(cfg, ep.id))
        elif stage == "voice":
            tts.synthesize(cfg, ep, ep.read("script.json"))
        elif stage == "media":
            if phase != "prepare":
                if cfg.web_images == "auto":
                    miss = media.scan(ep, ep.read("script.json"))["missing"]
                    if miss:
                        self._web_run(ep.id, miss, report=False)
                return "collect"
            media.prepare(cfg, ep, ep.read("script.json"), ep.read("voice.json"))
        elif stage == "render":
            self._say(f"🎞 Rendering {ep.id}… this takes a minute or two.")
            render.render_video(cfg, ep, progress=lambda p: self._say(f"🎞 {ep.id} rendering… {p}%"))
        elif stage == "package":
            self._package(ep)
        return "gate"

    def _package(self, ep: Episode) -> None:
        sc = ep.read("script.json")
        research_data = ep.read("research.json", {}) or {}
        claims = research_data.get("usable_claims") or []
        system, user = prompts.package_prompt(ep.topic, script_mod.narration_text(sc), claims, ep.feedback("package"))
        data = LLM(self.cfg).complete_json(system, user, task="package", mock_payload={"topic": ep.topic})
        cover = data.get("cover") or {}
        title = cover.get("title") or ep.topic["hook_title"]
        render.make_cover(self.cfg, ep, title, cover.get("highlight", ""), sc.get("series_tag", ""))
        tags = " ".join(h if h.startswith("#") else "#" + h for h in data.get("hashtags", [])[:5])
        caption = (data.get("caption", "").strip() + "\n\n" + tags).strip()
        sources = data.get("pinned_comment_sources") or []
        src_text = "Sources:\n" + "\n".join(f"• {s.get('claim', '')} — {s.get('source', '')} {s.get('url', '')}".strip() for s in sources) if sources else "Sources: (add before posting)"
        credits = webimages.credit_lines(ep)
        if credits:
            src_text += "\n\n" + credits
        ep.path("package").mkdir(exist_ok=True)
        ep.path("package/caption.txt").write_text(caption, encoding="utf-8")
        ep.path("package/sources.txt").write_text(src_text, encoding="utf-8")
        write_json(ep.path("package.json"), data)

    # ---- gate messages ---------------------------------------------------
    def _buttons(self, ep: Episode):
        s, v = ep.stage, ep.version()

        def cb(act: str) -> str:
            return f"A:{ep.id}:{s}:{v}:{act}"

        if ep.status == "collecting":
            rows = [[("✅ I've sent everything", cb("done"))]]
            if s == "media" and self.cfg.web_images != "off":
                rows.append([("🌐 Fill missing shots from the web", cb("web"))])
            return rows + [[("⏭ Skip topic", cb("skip"))]]
        if ep.status == "failed":
            return [[("🔄 Retry", cb("redo")), ("⏭ Skip topic", cb("skip"))]]
        approve = "✅ Approve & send final to my phone" if s == "package" else "✅ Approve"
        return [[(approve, cb("approve")), ("✏️ Edit", cb("edit"))], [("🔄 Redo", cb("redo")), ("⏭ Skip topic", cb("skip"))]]

    def _send_gate(self, ep_id: str, reminder: bool = False) -> None:
        ep = self.get(ep_id)
        stage = ep.stage
        head = f"{'⏰ Still waiting · ' if reminder else '🚦 '}{STAGE_LABEL[stage]} · {ep.id}\n{ep.topic['hook_title']}\n📅 publishes {_fmt_date(ep.publish_date)} {ep.state.get('post_time', '')}"
        if ep.status == "failed":
            self._say(f"{head}\n\n⚠️ This step failed:\n{ep.state.get('error', '')}\n\nCheck your API keys/network, then Retry.", self._buttons(ep))
            return
        body = ""
        if stage == "research":
            body = research.summary_text(ep.read("research.json"))
        elif stage == "script":
            body = script_mod.summary_text(ep.read("script.json"))
        elif stage == "voice":
            if not reminder:
                self._file(ep.path("voice.m4a"), f"Voice-over · {ep.id}", "audio")
            body = tts.summary_text(ep.read("voice.json"))
        elif stage == "media":
            sc, voice = ep.read("script.json"), ep.read("voice.json")
            if ep.status == "collecting":
                body = media.checklist_text(ep, sc, voice, media.scan(ep, sc))
                if self.cfg.web_images != "off":
                    body += "\n🌐 No photo for a shot? Tap the web button or send /web (or /web 3 for one shot). I only use openly licensed pictures and credit each one in the sources comment."
            else:
                if not reminder:
                    self._file(ep.path("media/contact.jpg"), f"Contact sheet · {ep.id}", "photo")
                body = "🖼 This is how each shot will be framed (cards, blur-pad, red circles included).\n✅ Approve to render · ✏️ Edit to add/replace media · 🔄 Redo to re-enhance"
        elif stage == "render":
            if not reminder:
                self._file(ep.path("preview.mp4"), f"Rough cut preview · {ep.id}", "video")
            info = ep.read("render.json", {}) or {}
            body = (f"🎬 Rough cut: {info.get('duration', '?')}s · full-quality file {info.get('size_mb', '?')} MB\n"
                    "✅ Approve → I'll write the caption and cover.\n✏️ Edit: type `media` to swap footage, or describe a script change (voice and edits get redone).")
        elif stage == "package":
            if not reminder:
                self._file(ep.path("cover.jpg"), f"Cover · {ep.id}", "photo")
            body = ("📦 Caption:\n" + ep.path("package/caption.txt").read_text(encoding="utf-8") + "\n\n" + ep.path("package/sources.txt").read_text(encoding="utf-8")
                    + "\n\n✅ Approve → I'll send the final video, cover and caption to this chat for you to post.")
        self._say(f"{head}\n\n{body}", self._buttons(ep))

    # ---- incoming events --------------------------------------------------
    def handle_event(self, ev: dict) -> None:
        try:
            if ev["type"] == "callback":
                self._on_callback(ev)
            elif ev["type"] == "file":
                self._on_file(ev)
            elif ev["type"] == "text":
                self._on_text(ev["text"])
        except Exception as e:
            log.exception("event failed")
            self._say(f"⚠️ Something went wrong handling that: {e}")

    def poll_once(self, timeout: float = 0) -> int:
        evs = self.n.poll(timeout)
        for ev in evs:
            self.handle_event(ev)
        return len(evs)

    def _on_callback(self, ev: dict) -> None:
        parts = ev["data"].split(":")
        if len(parts) != 5 or parts[0] != "A":
            self.n.answer(ev["id"], "Unknown button")
            return
        _, ep_id, stage, ver, act = parts
        if not Episode.exists(self.cfg.episodes_dir, ep_id):
            self.n.answer(ev["id"], "Episode not found")
            return
        ep = self.get(ep_id)
        if ep.stage != stage or str(ep.version(stage)) != ver or ep.status in ("done", "dropped"):
            self.n.answer(ev["id"], "That button is out of date — use the newest message.")
            return
        ok = self.act(ep_id, act)
        self.n.answer(ev["id"], "Done" if ok else "Not possible right now")

    def act(self, ep_id: str, act: str) -> bool:
        ep = self.get(ep_id)
        st = ep.status
        stage = ep.stage
        if act == "skip" and st in ACTIVE_STATUS:
            with self.edit(ep_id) as e:
                e.set(status="dropped")
                e.log("dropped by creator")
            self._say(f"⏭ {ep_id} skipped. It will not be scheduled again.")
            return True
        if act == "web" and stage == "media" and st == "collecting":
            self._web(ep_id, None)
            return True
        if act == "approve" and st == "awaiting_approval":
            with self.edit(ep_id) as e:
                e.state["gate"][stage] = {"approved": time.time(), "version": e.version(stage)}
                e.log(f"approved {stage}")
            if stage == "package":
                self._deliver(ep_id)
            else:
                self._begin(ep_id, STAGES[STAGES.index(stage) + 1])
            return True
        if act == "redo" and st in ("awaiting_approval", "failed"):
            self._begin(ep_id, stage, "prepare" if stage == "media" else None)
            return True
        if act == "edit" and st == "awaiting_approval":
            with self.edit(ep_id) as e:
                e.state["pending_edit"] = {"stage": stage, "at": time.time()}
            hint = {
                "research": "Tell me what to add, fix or investigate.",
                "script": "What should change? (tone, facts, length, order…)",
                "voice": "Type `voice en-IN-NeerjaNeural` or `rate +8%`, or describe a script change.",
                "media": "Type what to change; I'll reopen the shot list so you can add/replace media (then press ✅).",
                "render": "Type `media` to swap footage, or describe what to change in the script.",
                "package": "What should change in the caption/cover?",
            }[stage]
            self._say(f"✏️ {ep_id} · {hint}")
            return True
        if act == "done" and st == "collecting" and stage == "media":
            sc = ep.read("script.json")
            scanned = media.scan(ep, sc)
            if scanned["missing"]:
                self._say(f"⏳ {ep_id}: still missing media for shot(s) {', '.join(map(str, scanned['missing']))}. Send them, or /skip N to use a text card.\n\n"
                          + media.checklist_text(ep, sc, ep.read("voice.json"), scanned), self._buttons(ep))
                return False
            self._begin(ep_id, "media", "prepare")
            self._say(f"🛠 {ep_id}: enhancing your media and building the contact sheet…")
            return True
        return False

    def _on_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if text.startswith("/"):
            return self._command(text)
        m = re.match(rf"^(?:({EP_RE})\s+)?s?(\d{{1,2}})\s+(https?://\S+)$", text)
        if m:
            ep = self._media_episode(m.group(1))
            if ep is None:
                return
            try:
                p = media.add_url(ep, int(m.group(2)), m.group(3))
            except Exception as e:
                self._say(f"⚠️ Couldn't fetch that URL: {e}")
                return
            return self._media_saved(ep, int(m.group(2)), p.name)
        pending = [e for e in self.episodes() if e.state.get("pending_edit") and e.status == "awaiting_approval"]
        if not pending:
            self._say("I wasn't waiting for text. /status shows what's open, /help lists commands.")
            return
        pending.sort(key=lambda e: e.state["pending_edit"]["at"], reverse=True)
        self._apply_edit(pending[0].id, text)

    def _apply_edit(self, ep_id: str, text: str) -> None:
        ep = self.get(ep_id)
        stage = ep.stage
        low = text.strip().lower()
        if stage == "voice":
            mv = re.match(r"^voice\s+(\S+)$", low)
            mr = re.match(r"^rate\s*([+-]?\d{1,2})\s*%?$", low)
            if mv or mr:
                with self.edit(ep_id) as e:
                    if mv:
                        e.state["voice_overrides"]["voice"] = text.split()[1]
                    else:
                        n = int(mr.group(1))
                        e.state["voice_overrides"]["rate"] = f"{'+' if n >= 0 else '-'}{abs(n)}%"
                self._say(f"🎙 {ep_id}: re-recording with the new setting…")
                return self._begin(ep_id, "voice")
        if stage == "media":
            with self.edit(ep_id) as e:
                e.add_feedback(text, "media")
                e.set(status="collecting")
                e.state.pop("pending_edit", None)
                e.state["last_notified"] = time.time()
            self._send_gate(ep_id)
            return
        if stage == "render" and low.startswith("media"):
            with self.edit(ep_id) as e:
                e.add_feedback(text, "media")
                e.set("media", "collecting")
                e.state.pop("pending_edit", None)
                e.state["last_notified"] = time.time()
            self._send_gate(ep_id)
            return
        target = stage if stage in ("research", "script", "package") else "script"
        with self.edit(ep_id) as e:
            e.add_feedback(text, target)
        if target != stage:
            self._say(f"↩️ {ep_id}: going back to the script with your note. Voice, media check and render will be redone. Files you already sent stay in media/in, so re-check the shot numbers when the new shot list arrives.")
        else:
            self._say(f"🔁 {ep_id}: redoing {stage} with your note…")
        self._begin(ep_id, target)

    # ---- commands ---------------------------------------------------------
    def _resolve(self, token: str | None, stages: tuple[str, ...] | None = None, statuses=("awaiting_approval", "collecting", "failed")) -> Episode | None:
        if token:
            tok = token.upper()
            if Episode.exists(self.cfg.episodes_dir, tok):
                return self.get(tok)
            self._say(f"I don't know episode {tok}.")
            return None
        cands = [e for e in self.episodes() if e.status in statuses and (stages is None or e.stage in stages)]
        if len(cands) == 1:
            return cands[0]
        if not cands:
            self._say("Nothing is waiting for that right now. /status")
        else:
            self._say("Which episode? Add its id, e.g. " + ", ".join(e.id for e in cands))
        return None

    def _media_episode(self, token: str | None) -> Episode | None:
        ep = self._resolve(token, ("media",), ("collecting", "awaiting_approval"))
        if ep and ep.stage != "media":
            self._say(f"{ep.id} is not at the media step.")
            return None
        return ep

    def _web(self, ep_id: str, shot: int | None) -> None:
        if self.cfg.web_images == "off":
            return self._say("Web pictures are switched off. Set AWN_WEB_IMAGES=ask in .env and restart.")
        ep = self.get(ep_id)
        sc = ep.read("script.json")
        by_id = {x["id"]: x for x in sc["shots"]}
        scanned = media.scan(ep, sc)
        if shot is None:
            targets = list(scanned["missing"])
            if not targets:
                return self._say("Every shot already has media or a text card. /web 3 swaps a fetched picture for shot 3.")
        else:
            if shot not in by_id:
                return self._say(f"{ep.id} has no shot {shot}.")
            if by_id[shot]["style"] == "text":
                return self._say(f"Shot {shot} is a text card by design.")
            have = [Path(f).name for f in scanned["shots"][shot]["files"]]
            mine = webimages.web_files(ep).get(shot)
            if have and have != [mine]:
                return self._say(f"Shot {shot} has your own file. /clear {shot} first if you'd rather have a web picture.")
            if mine:
                webimages.discard(ep, shot)
            media.set_skip(ep, shot, False)
            targets = [shot]
        self._say(f"🌐 Looking for open-licence pictures for shot{'s' if len(targets) > 1 else ''} {', '.join(map(str, targets))}… (about {10 * len(targets)} s)")
        if self.sync:
            self._web_run(ep_id, targets)
        else:
            threading.Thread(target=self._web_run, args=(ep_id, targets), daemon=True).start()

    def _web_run(self, ep_id: str, targets: list[int], report: bool = True) -> None:
        ep = self.get(ep_id)
        try:
            results = webimages.fill(self.cfg, ep, ep.read("script.json"), targets)
        except Exception as e:
            log.exception("web pictures failed")
            return self._say(f"⚠️ Couldn't search the web just now: {e}\nSend your own photo, or try /web again.")
        for r in results:
            if r.ok:
                self._file(webimages.preview(ep, r.path), f"🌐 Shot {r.shot} · {r.message}\n/web {r.shot} for another · /clear {r.shot} to remove", "photo")
            else:
                self._say(f"🌐 Shot {r.shot}: {r.message}")
        if not report:
            return
        with self.edit(ep_id) as e:
            if e.status == "awaiting_approval" and e.stage == "media":
                e.set(status="collecting")
        ep = self.get(ep_id)
        miss = media.scan(ep, ep.read("script.json"))["missing"]
        tail = f"Still need: {', '.join(map(str, miss))}" if miss else "Everything has media. Check the pictures above, then press ✅."
        self._say(f"🌐 {ep_id}: done. {tail}", self._buttons(ep))

    def _send_styles(self) -> None:
        try:
            sheet = render.style_samples(self.cfg, self.cfg.data_dir / "styles")[0]
            self._file(sheet, f"Caption styles. Now: {self.cfg.caption_style}. Pick with /style NAME", kind="photo")
        except Exception as e:
            log.exception("style samples failed")
            self._say(f"Couldn't render the samples: {e}")

    def _restart(self, ep_id: str) -> None:
        if not Episode.exists(self.cfg.episodes_dir, ep_id):
            return self._say(f"{ep_id} hasn't been started yet. Use /new {ep_id}.")
        if not plan.topic_for(self.cfg, ep_id):
            return self._say(f"{ep_id} is not in the backlog, so I can't start it again.")
        with self.lock:
            if self.get(ep_id).status == "working":
                return self._say(f"{ep_id} is busy right now. Wait for its next message, or /drop {ep_id} first.")
            archive = self.cfg.data_dir / "archive"
            archive.mkdir(parents=True, exist_ok=True)
            dest = archive / f"{ep_id}_{datetime.now():%Y%m%d-%H%M%S}"
            (self.cfg.episodes_dir / ep_id).rename(dest)
        self._say(f"📦 The old {ep_id} run is kept in {dest}")
        self.start_episode(ep_id)

    def _set_style(self, name: str) -> None:
        if name not in STYLES:
            return self._say(f"Usage: /style NAME, one of {', '.join(STYLES)}. /styles shows them.")
        self.cfg.caption_style = name
        try:
            set_env(self.env_path, "AWN_CAPTION_STYLE", name)
        except OSError as e:
            log.warning("could not save AWN_CAPTION_STYLE: %s", e)
        self._say(f"✅ Captions will use {name} from the next render (🔄 Redo at the render gate to re-render).")

    def _command(self, text: str) -> None:
        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]

        def is_ep(tok: str) -> bool:
            return bool(re.fullmatch(EP_RE, tok)) and tok[0].upper() != "S"  # "s10" is a shot, not an episode

        ep_tok = None
        if args and is_ep(args[-1]):
            ep_tok, args = args[-1], args[:-1]
        elif args and is_ep(args[0]):
            ep_tok, args = args[0], args[1:]
        if cmd in ("/start", "/help"):
            return self._say(HELP)
        if cmd == "/status":
            return self._say(self.status_text())
        if cmd == "/web":
            ep = self._media_episode(ep_tok)
            if not ep:
                return
            try:
                shot = int(args[0].lstrip("sS")) if args else None
            except ValueError:
                return self._say("Usage: /web [shot number]")
            return self._web(ep.id, shot)
        if cmd == "/rerender":
            ep = self._resolve(ep_tok, stages=("render", "package"), statuses=("awaiting_approval", "failed"))
            if ep:
                self._say(f"🔁 {ep.id}: rendering again with the current settings (captions {self.cfg.caption_style}, transitions {self.cfg.transition}, music {self.cfg.music_mode}). You'll get the render gate again.")
                self._begin(ep.id, "render")
            return
        if cmd == "/styles":
            return self._send_styles()
        if cmd == "/style":
            return self._set_style(args[0].lower() if args else "")
        if cmd == "/new":
            tid = (args[0] if args else ep_tok or "").upper()
            if not tid:
                return self._say("Usage: /new M01")
            if Episode.exists(self.cfg.episodes_dir, tid):
                return self._say(f"{tid} already exists. /status\nTo run it again from the start: /restart {tid}")
            if not self.start_episode(tid):
                self._say(f"{tid} is not in the backlog.")
            return
        if cmd == "/restart":
            tid = (args[0] if args else ep_tok or "").upper()
            if not tid:
                return self._say("Usage: /restart M01")
            return self._restart(tid)
        if cmd in ("/approve", "/redo", "/drop"):
            ep = self._resolve(ep_tok)
            if ep:
                self.act(ep.id, {"/approve": "approve", "/redo": "redo", "/drop": "skip"}[cmd])
            return
        if cmd == "/edit":
            ep = self._resolve(ep_tok, statuses=("awaiting_approval",))
            if ep and args:
                return self._apply_edit(ep.id, " ".join(args))
            return self._say("Usage: /edit [ID] what to change")
        if cmd == "/done":
            ep = self._media_episode(ep_tok)
            if ep:
                self.act(ep.id, "done") if ep.status == "collecting" else self._say("Media is already under review.")
            return
        if cmd in ("/skip", "/clear", "/callout"):
            ep = self._media_episode(ep_tok)
            if not ep:
                return
            try:
                shot = int(args[0].lstrip("sS"))
            except (IndexError, ValueError):
                return self._say(f"Usage: {cmd} <shot number>" + (" x y radius" if cmd == "/callout" else ""))
            sc = ep.read("script.json")
            if shot not in [s["id"] for s in sc["shots"]]:
                return self._say(f"{ep.id} has no shot {shot}.")
            if cmd == "/skip":
                media.set_skip(ep, shot, True)
                self._say(f"⏭ Shot {shot} will use a text card.")
            elif cmd == "/clear":
                n = 0
                trash = ep.path("media/trash")
                trash.mkdir(parents=True, exist_ok=True)
                for f in media.inbox(ep).iterdir():
                    if media.parse_shot_number(f.name) == shot:
                        f.rename(trash / f.name)
                        n += 1
                media.set_skip(ep, shot, False)
                self._say(f"🗑 Removed {n} file(s) for shot {shot}.")
            else:
                try:
                    x, y, r = (float(v) for v in args[1:4])
                except ValueError:
                    return self._say("Usage: /callout 4 0.55 0.30 0.10   (x y radius as 0-1 fractions of the photo)")
                media.set_callout(ep, shot, x, y, r)
                self._say(f"🔴 Red circle set on shot {shot}.")
            if ep.status == "collecting":
                self._say("Press ✅ when you're done.", self._buttons(self.get(ep.id)))
            return
        self._say("Unknown command. /help")

    def _on_file(self, ev: dict) -> None:
        cap = (ev.get("caption") or "").strip()
        m = re.match(rf"^(?:({EP_RE})[\s:]+)?(?:s|shot)?\s*(\d{{1,2}})\b", cap, re.I)
        if m:
            ep_tok, shot = m.group(1), int(m.group(2))
        else:
            shot = media.parse_shot_number(ev.get("name", ""))
            ep_tok = None
            if shot is None:
                return self._say("📎 I need a shot number as the caption, e.g. 3 (or M01 3 if several episodes are open).")
        ep = self._media_episode(ep_tok)
        if not ep:
            return
        if shot not in [s["id"] for s in ep.read("script.json")["shots"]]:
            return self._say(f"{ep.id} has no shot {shot}.")
        if ev.get("size", 0) > TG_DOWNLOAD_LIMIT:
            return self._say(f"📦 That file is {ev['size'] / 1e6:.0f} MB; bots can only download 20 MB. Copy it into this folder instead, named like s{shot}_name.mp4:\n{media.inbox(ep)}")
        try:
            data = self.n.download(ev["file_id"])
            p = media.add_file(ep, shot, data, ev.get("name") or "file.jpg")
        except Exception as e:
            return self._say(f"⚠️ Couldn't save that file: {e}")
        if ep.status == "awaiting_approval":
            with self.edit(ep.id) as e:
                e.set(status="collecting")
        self._media_saved(ep, shot, p.name)

    def _media_saved(self, ep: Episode, shot: int, name: str) -> None:
        scanned = media.scan(ep, ep.read("script.json"))
        n = len(scanned["shots"][shot]["files"])
        miss = scanned["missing"]
        tail = f"Still need: {', '.join(map(str, miss))}" if miss else "Everything has media. Press ✅ when you're done."
        self._say(f"📥 {ep.id} shot {shot}: saved ({n} file{'s' if n != 1 else ''}). {tail}", self._buttons(self.get(ep.id)))

    # ---- finishing --------------------------------------------------------
    def _deliver(self, ep_id: str) -> None:
        ep = self.get(ep_id)
        self._say(f"📤 Sending the final files for {ep.id}…")
        self._file(ep.path("final.mp4"), f"{ep.id} · final 1080×1920", "document")
        self._file(ep.path("cover.png"), f"{ep.id} · cover", "document")
        self._say("📝 Caption (copy):\n\n" + ep.path("package/caption.txt").read_text(encoding="utf-8"))
        self._say(ep.path("package/sources.txt").read_text(encoding="utf-8") + "\n\n" + POST_CHECKLIST)
        try:
            plan.append_tracker(self.cfg, ep.topic, ep.publish_date, ep.state.get("post_time", ""))
        except OSError as e:
            self._say(f"⚠️ Couldn't update the tracker CSV (is it open in Excel?): {e}")
        with self.edit(ep_id) as e:
            e.set(status="done")
            e.log("delivered")
        self._say(f"✅ {ep_id} is ready to post. After 48 h, fill its row in 03_performance_tracker.csv.")

    # ---- scheduler / reminders -------------------------------------------
    def status_text(self) -> str:
        eps = self.episodes()
        if not eps:
            return "No episodes yet. The scheduler starts them automatically, or use /new M01."
        icon = {"working": "⚙️", "awaiting_approval": "🟡 waiting for you", "collecting": "📥 waiting for your media", "failed": "⛔ failed", "done": "✅ done", "dropped": "⏭ skipped"}
        lines = []
        for e in sorted(eps, key=lambda e: e.publish_date):
            lines.append(f"{e.id} · {e.topic['hook_title'][:38]} · {e.publish_date} · {STAGE_LABEL[e.stage].split(' · ')[0]} {icon.get(e.status, e.status)}")
        return "\n".join(lines)

    def tick(self, now: datetime | None = None) -> dict:
        """Called about once a minute: start due episodes, send reminders."""
        now = now or datetime.now()
        started, reminded = [], []
        eps = self.episodes()
        active = [e for e in eps if e.status in ACTIVE_STATUS]
        capacity = self.cfg.max_active - len(active)
        if capacity > 0:
            for topic, row in plan.due(self.cfg, {e.id for e in eps}, now.date()):
                if capacity <= 0:
                    break
                if self.start_episode(topic["id"], row["date"], row["post_time_IST"]):
                    started.append(topic["id"])
                    capacity -= 1
        if WAKING_HOURS[0] <= now.hour < WAKING_HOURS[1]:
            for e in self.episodes():
                if e.status not in ("awaiting_approval", "collecting", "failed"):
                    continue
                last = e.state.get("last_notified", 0)
                if e.state.get("reminders", 0) >= self.cfg.max_reminders:
                    continue
                if time.time() - last >= self.cfg.reminder_hours * 3600:
                    with self.edit(e.id) as e2:
                        e2.state["last_notified"] = time.time()
                        e2.state["reminders"] = e2.state.get("reminders", 0) + 1
                    self._send_gate(e.id, reminder=True)
                    reminded.append(e.id)
        return {"started": started, "reminded": reminded}

    def run_forever(self) -> None:
        self.start_worker()
        self.resume()
        last_tick = 0.0
        self._say("🟢 awhynote pipeline is online. /status · /help")
        while not self._stop.is_set():
            n = self.poll_once(timeout=20)
            if not n and not getattr(self.n, "long_polls", True):
                time.sleep(0.5)
            if time.time() - last_tick >= 60:
                try:
                    self.tick()
                except Exception:
                    log.exception("tick failed")
                last_tick = time.time()
