"""awhynote command line."""
from __future__ import annotations

import argparse
import logging
import random
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from . import plan
from .config import ROOT, Config
from .notify import ConsoleNotifier, TelegramAPI, TelegramError, TelegramNotifier
from .pipeline import Pipeline
from .util import FONT_DIR, ensure_fonts, media_info, set_env

ENV_EXAMPLE = """# awhynote pipeline settings. Copy to .env and fill in. Never commit this file.

# --- LLM (chat subscriptions like ChatGPT Plus do NOT include API access; you need an API key) ---
# provider: openai (any OpenAI-compatible API) | anthropic | mock (offline test content)
LLM_PROVIDER=mock
LLM_API_KEY=
LLM_MODEL=
# LLM_BASE_URL=https://api.openai.com/v1
# Optional: a different model for the hostile fact-check pass (recommended) ...
# LLM_CHECK_MODEL=
# ... and a search-grounded model for research (e.g. Perplexity: LLM_RESEARCH_BASE_URL=https://api.perplexity.ai)
# LLM_RESEARCH_PROVIDER=openai
# LLM_RESEARCH_API_KEY=
# LLM_RESEARCH_MODEL=
# LLM_RESEARCH_BASE_URL=

# --- Voice ---
TTS_PROVIDER=edge
EDGE_VOICE=en-IN-PrabhatNeural
EDGE_RATE=+12%
# ElevenLabs (optional, untested without a key): TTS_PROVIDER=elevenlabs
# ELEVENLABS_API_KEY=
# ELEVENLABS_VOICE_ID=

# --- Telegram approvals (create a bot with @BotFather, then run: awhynote telegram-setup) ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# --- Scheduling ---
AWN_HANDLE=@awhynote
AWN_LEAD_DAYS=5
AWN_REMINDER_HOURS=4
AWN_MAX_REMINDERS=3
AWN_MAX_ACTIVE=3

# --- Pictures from the web, for shots you have no photo for ---
# ask = tap "Fill missing shots from the web" or send /web (default) | auto = fill them as soon as the media step opens | off
AWN_WEB_IMAGES=ask
# commons = Wikimedia Commons, openverse, pexels (needs a free key from pexels.com/api, put it in PEXELS_API_KEY)
AWN_WEB_SOURCES=commons,openverse,pexels
# PEXELS_API_KEY=
# attribution = CC0, public domain, CC BY, CC BY-SA | strict = CC0, public domain, CC BY only
AWN_WEB_LICENSES=attribution

# --- Look and sound ---
# Caption look: poppins (default) | montserrat | anton | bebas. See them side by side: awhynote styles
AWN_CAPTION_STYLE=poppins
# Cuts between shots: mix (a variety, chosen per shot) | fade | cut
AWN_TRANSITION=mix
# Background music, ducked under the voice:
#   auto    = a track from assets/music/ if there is one, otherwise a soft generated pad
#   ambient = always the generated pad       off = no music
AWN_MUSIC=auto
# AWN_MUSIC_FILE=/Users/you/Music/bed.mp3   (one fixed track; wins over the folder)
# AWN_MUSIC_DIR=/Users/you/Music/awhynote   (default: assets/music)
# AWN_MUSIC_DB=-19    (music level under the voice; lower = quieter)
# AWN_SFX_DB=-20      (whoosh / hit level on transitions)
"""


def setup_logging(cfg: Config) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(cfg.data_dir / "pipeline.log", encoding="utf-8"), logging.StreamHandler(sys.stderr)])


def make_notifier(cfg: Config, console: bool) -> tuple:
    if console:
        return ConsoleNotifier(), None
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        sys.exit("Telegram is not configured. Run `awhynote telegram-setup`, or try `awhynote console`.")
    api = TelegramAPI(cfg.telegram_token)
    return TelegramNotifier(api, cfg.telegram_chat_id), api


# -- commands ----------------------------------------------------------------
def cmd_init(cfg: Config, a) -> int:
    env = ROOT / ".env"
    if env.exists():
        print(f".env already exists at {env}")
    else:
        env.write_text(ENV_EXAMPLE, encoding="utf-8")
        env.chmod(0o600)
        print(f"Created {env}. Edit it, then run `awhynote doctor`.")
    (ROOT / ".gitignore").write_text(".env\ndata/\n.venv/\n__pycache__/\n*.pyc\n*.egg-info/\n.pytest_cache/\nassets/fonts/\nassets/music/\n", encoding="utf-8")
    ensure_fonts(download=True)
    cfg.episodes_dir.mkdir(parents=True, exist_ok=True)
    print("Fonts and data folder ready.")
    return 0


def cmd_doctor(cfg: Config, a) -> int:
    bad = 0

    def line(ok, msg, hint=""):
        nonlocal bad
        print(("✅ " if ok else "❌ ") + msg + (f"\n     → {hint}" if hint and not ok else ""))
        bad += 0 if ok else 1

    try:
        info = media_info(cfg.ffmpeg, Path(cfg.ffmpeg))
        line(True, f"ffmpeg: {cfg.ffmpeg}")
    except Exception as e:
        line(False, f"ffmpeg not usable: {e}", "pip install imageio-ffmpeg, or install ffmpeg and set FFMPEG=…")
    fonts = ensure_fonts(download=a.online)
    for name, path in fonts.items():
        own = Path(path).parent == FONT_DIR
        line(own, f"font {name}" + ("" if own else "  (using a system fallback; captions may lack ₹ or look different)"),
             "run `awhynote init` with internet, or drop the .ttf into assets/fonts/")
    for label, p in (("backlog", cfg.backlog_csv), ("calendar", cfg.calendar_csv)):
        line(p.exists(), f"{label}: {p}")
    line(cfg.llm_provider == "mock" or bool(cfg.llm_api_key and cfg.llm_model),
         f"LLM: {cfg.llm_provider} {cfg.llm_model or ''}".strip() + ("  (mock = placeholder content, not real research!)" if cfg.llm_provider == "mock" else ""),
         "set LLM_PROVIDER, LLM_API_KEY and LLM_MODEL in .env")
    line(cfg.tts_provider in ("edge", "mock") or bool(cfg.eleven_key), f"TTS: {cfg.tts_provider}")
    line(bool(cfg.telegram_token), "Telegram bot token set", "create a bot with @BotFather and put the token in .env")
    line(bool(cfg.telegram_chat_id), "Telegram chat id set", "run `awhynote telegram-setup`")
    if cfg.music_file:
        line(Path(cfg.music_file).expanduser().exists(), f"music file: {cfg.music_file}")
    elif cfg.music_mode == "off":
        print("ℹ️  music: off")
    else:
        from . import music

        n = len(music.library(cfg)) if cfg.music_mode != "ambient" else 0
        print(f"ℹ️  music: {n} track(s) in {music.library_dir(cfg)}" if n else "ℹ️  music: soft generated pad (drop .mp3/.wav files into assets/music/ to use your own)")
    print(f"ℹ️  web pictures: {cfg.web_images} · sources {cfg.web_sources}" + ("" if cfg.pexels_key or "pexels" not in cfg.web_sources else " (Pexels needs PEXELS_API_KEY, otherwise skipped)") + f" · licences {cfg.web_licenses}")
    print(f"ℹ️  look: captions={cfg.caption_style} transitions={cfg.transition} voice rate={cfg.edge_rate}")
    if a.online:
        try:
            import edge_tts  # noqa: F401
            import asyncio

            async def probe():
                voices = await edge_tts.list_voices()
                return any(v["ShortName"] == cfg.edge_voice for v in voices)

            line(asyncio.run(probe()), f"edge-tts reachable and voice {cfg.edge_voice} exists", "check internet / voice name")
        except Exception as e:
            line(False, f"edge-tts online check failed: {e}")
        if cfg.web_images != "off":
            try:
                import requests

                from .webimages import COMMONS_API, UA

                r = requests.get(COMMONS_API, params={"action": "query", "meta": "siteinfo", "format": "json"}, headers={"User-Agent": UA}, timeout=15)
                line(r.status_code == 200, "Wikimedia Commons reachable (web pictures)", "check internet")
            except Exception as e:
                line(False, f"Wikimedia Commons check failed: {e}", "check internet")
        if cfg.telegram_token:
            try:
                me = TelegramAPI(cfg.telegram_token).call("getMe")
                line(True, f"Telegram bot reachable: @{me['username']}")
            except Exception as e:
                line(False, f"Telegram check failed: {e}", "token wrong or no internet")
        if cfg.llm_provider != "mock" and cfg.llm_api_key:
            try:
                from .llm import LLM

                out = LLM(cfg).complete('Reply with JSON only.', 'Return {"ok": true}')
                line("ok" in out, "LLM answered a test call")
            except Exception as e:
                line(False, f"LLM test call failed: {e}", "check key, model name, base URL")
    print("\nAll good." if not bad else f"\n{bad} problem(s) to fix.")
    return 1 if bad else 0


def cmd_plan(cfg: Config, a) -> int:
    started = {p.name for p in cfg.episodes_dir.glob("*")} if cfg.episodes_dir.exists() else set()
    today = date.today()
    print(f"Today {today}. Lead window: {cfg.lead_days} days. Max in flight: {cfg.max_active}.\n")
    for r in plan.load_calendar(cfg)[:a.limit]:
        flag = "started" if r["topic_id"] in started else ("DUE" if date.fromisoformat(r["date"]) <= today + timedelta(days=cfg.lead_days) else "")
        print(f"{r['date']} {r['post_time_IST']}  {r['topic_id']:<4} {r['hook_title'][:52]:<52} {flag}")
    return 0


def cmd_status(cfg: Config, a) -> int:
    p = Pipeline(cfg, ConsoleNotifier(), sync=True)
    print(p.status_text())
    return 0


def cmd_new(cfg: Config, a) -> int:
    notifier, _ = make_notifier(cfg, a.console)
    p = Pipeline(cfg, notifier, sync=False)
    if not p.start_episode(a.topic.upper()):
        print("Not started (unknown id or already exists).")
        return 1
    print("Started. Keep `awhynote run` (or `console`) running to process it.")
    return 0


def cmd_styles(cfg: Config, a) -> int:
    from .render import style_samples

    out = cfg.data_dir / "styles"
    paths = style_samples(cfg, out)
    print("Caption styles rendered. Open the sheet to compare, then set AWN_CAPTION_STYLE in .env:\n")
    for p in paths:
        print(f"  {p}")
    print(f"\nSheet: {out / 'styles_sheet.jpg'}")
    return 0


def cmd_run(cfg: Config, a) -> int:
    setup_logging(cfg)
    notifier, _ = make_notifier(cfg, False)
    p = Pipeline(cfg, notifier)
    try:
        p.run_forever()
    except KeyboardInterrupt:
        p.stop()
    return 0


def cmd_console(cfg: Config, a) -> int:
    setup_logging(cfg)
    n = ConsoleNotifier()
    p = Pipeline(cfg, n)
    p.start_worker()
    p.resume()
    if a.topic:
        p.start_episode(a.topic.upper())
    print("Console mode. Type: a (approve) · r (redo) · s (skip topic) · e <note> (edit) · /status · /done · /help · q (quit)")
    alias = {"a": "/approve", "r": "/redo", "s": "/drop", "d": "/done"}
    stop = threading.Event()

    def ticker():
        while not stop.is_set():
            try:
                p.tick()
            except Exception:
                logging.getLogger("awhynote").exception("tick failed")
            stop.wait(60)

    threading.Thread(target=ticker, daemon=True).start()
    try:
        while True:
            line = input("\n› ").strip()
            if line in ("q", "quit", "exit"):
                break
            head, _, rest = line.partition(" ")
            if head in alias:
                line = alias[head] + (" " + rest if rest else "")
            elif head == "e":
                line = "/edit " + rest
            p.handle_event({"type": "text", "text": line})
    except (EOFError, KeyboardInterrupt):
        pass
    stop.set()
    p.stop()
    return 0


def cmd_telegram_setup(cfg: Config, a) -> int:
    env = ROOT / ".env"
    if not env.exists():
        env.write_text(ENV_EXAMPLE, encoding="utf-8")
    token = cfg.telegram_token or input("Paste your bot token from @BotFather: ").strip()
    api = TelegramAPI(token)
    try:
        me = api.call("getMe")
    except TelegramError as e:
        print(f"Token rejected by Telegram: {e}")
        return 1
    set_env(env, "TELEGRAM_BOT_TOKEN", token)
    api.call("deleteWebhook")
    code = f"{random.randint(1000, 9999)}"
    print(f"\nBot found: @{me['username']}\n1. Open https://t.me/{me['username']} on your phone and press START.\n2. Send this code to the bot: {code}\n(waiting up to 3 minutes…)")
    offset, deadline = 0, time.time() + 180
    while time.time() < deadline:
        for u in api.call("getUpdates", _timeout=30, timeout=10, offset=offset):
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            if (m.get("text") or "").strip() == code:
                chat_id = str(m["chat"]["id"])
                set_env(env, "TELEGRAM_CHAT_ID", chat_id)
                api.call("sendMessage", chat_id=chat_id, text="✅ Paired. Only this chat can approve awhynote steps. Run `awhynote run` on your Mac to start.")
                print(f"Paired with chat {chat_id}. Saved to {env}.")
                return 0
    print("Timed out waiting for the code. Run the command again.")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="awhynote", description="Approval-gated Reels pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create .env, download fonts")
    d = sub.add_parser("doctor", help="check setup")
    d.add_argument("--online", action="store_true", help="also test network services")
    pl = sub.add_parser("plan", help="show the calendar and what is due")
    pl.add_argument("--limit", type=int, default=40)
    sub.add_parser("status", help="list episodes")
    nw = sub.add_parser("new", help="start one topic now")
    nw.add_argument("topic")
    nw.add_argument("--console", action="store_true")
    sub.add_parser("styles", help="render sample frames of every caption style")
    sub.add_parser("run", help="run the pipeline + Telegram bot (keep it running)")
    c = sub.add_parser("console", help="run with terminal approvals instead of Telegram")
    c.add_argument("topic", nargs="?")
    sub.add_parser("telegram-setup", help="pair your phone with the bot")
    a = ap.parse_args(argv)
    cfg = Config.load()
    return {"init": cmd_init, "doctor": cmd_doctor, "plan": cmd_plan, "status": cmd_status, "new": cmd_new,
            "styles": cmd_styles, "run": cmd_run, "console": cmd_console, "telegram-setup": cmd_telegram_setup}[a.cmd](cfg, a)


if __name__ == "__main__":
    sys.exit(main())
