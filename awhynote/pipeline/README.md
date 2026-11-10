# awhynote pipeline

Turns a backlog topic into a finished Instagram Reel (under 60 s, 1080×1920) **with your approval at every stage**, via Telegram on your phone. Nothing advances without your tap and nothing is ever posted for you.

```
research → script → voice → your photos/footage → render → caption & cover → you post
 Gate 1     Gate 2   Gate 3        Gate 4            Gate 5      Gate 6
```

You supply the real photos and videos. The pipeline only does the research, script, voice-over, light enhancement, captions, edit and packaging.

## 1. Setup (once)

```bash
cd ~/awhynote/pipeline
.venv/bin/awhynote init        # creates .env, downloads the 3 brand fonts, makes data/
.venv/bin/awhynote doctor      # tells you exactly what is still missing
```

Optional: `export PATH="$HOME/awhynote/pipeline/.venv/bin:$PATH"` so you can type `awhynote …` anywhere. If the venv is ever lost: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`.

### LLM key (needed for real content)

A ChatGPT/Claude **chat subscription is not API access**. Create an API key and put it in `.env`:

```
LLM_PROVIDER=openai          # any OpenAI-compatible API, or: anthropic
LLM_API_KEY=...
LLM_MODEL=...                # use a current model name from your provider
```

Recommended extras: `LLM_CHECK_MODEL` (a different model for the hostile fact-check) and `LLM_RESEARCH_*` (a search-grounded model such as Perplexity, base URL `https://api.perplexity.ai`). Without a search-grounded model, research comes from the LLM's own knowledge plus a URL check, so **read Gate 1 carefully and verify**. Each script costs a few cents.

`LLM_PROVIDER=mock` (the default) produces placeholder content so you can try the whole flow for free.

### Telegram bot

1. In Telegram, message **@BotFather** → `/newbot` → copy the token.
2. Put it in `.env` as `TELEGRAM_BOT_TOKEN=...`
3. Run `awhynote telegram-setup`, open the bot link on your phone, press START and send the 4-digit code shown. This pairs your chat; the bot **ignores every other chat**.

### Voice

Default is free Microsoft Edge voices: `EDGE_VOICE=en-IN-PrabhatNeural` (try `en-IN-NeerjaNeural`). At Gate 3 you can reply `voice en-IN-NeerjaNeural` or `rate +8%`. ElevenLabs is supported (`TTS_PROVIDER=elevenlabs`, key and voice id) but has not been tried with a real key.

## 2. Running it

```bash
awhynote run              # the Telegram bot + scheduler; leave it running
awhynote console [M01]    # same flow with approvals typed in the terminal (no Telegram)
awhynote plan             # calendar and what is due
awhynote status           # episodes and what each is waiting for
awhynote new M01          # start a topic now (picked up by `run`)
```

The scheduler starts each calendar post `AWN_LEAD_DAYS` (5) before its publish date, at most `AWN_MAX_ACTIVE` (3) at once. Reminders go out every `AWN_REMINDER_HOURS` (4) between 08:00 and 22:00, at most `AWN_MAX_REMINDERS` (3) per gate, then it waits quietly.

**Your Mac must be awake and online** while `run` is going. Messages sent to the bot while it was off are delivered when it restarts (Telegram keeps them about 24 h); unfinished work resumes automatically.

### Keep it running in the background (optional, launchd)

Save as `~/Library/LaunchAgents/com.awhynote.pipeline.plist` (it is not installed for you):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.awhynote.pipeline</string>
  <key>ProgramArguments</key><array>
    <string>/Users/i1000719/awhynote/pipeline/.venv/bin/awhynote</string><string>run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/Users/i1000719/awhynote/pipeline/data/launchd.err</string>
</dict></plist>
```

Then `launchctl load ~/Library/LaunchAgents/com.awhynote.pipeline.plist` (and `launchctl unload …` to stop).

## 3. What you do at each gate

| Gate | You see | You can |
|---|---|---|
| 1 Facts & sources | Claims with 🟢/🟡/🔴 confidence, sources, legend warnings | Approve · Edit ("also check X") · Redo · Skip topic. Only 🟢/🟡 claims reach the script. |
| 2 Script | Shot list + beats, word count and time, second-model fact-check | Same. The "Everything has a why." sign-off is enforced. |
| 3 Voice | `voice.m4a` | Approve · `voice NAME` · `rate +N%` · script changes |
| 4 Your media | Numbered shot list with what to photograph | Send files, then **I've sent everything** |
| 5 Render | `preview.mp4` and a contact sheet | Approve · "media: swap shot 2" goes back to Gate 4, anything else back to the script |
| 6 Package | Caption, hashtags, sources, cover | Approve → `final.mp4` arrives as a file with a posting checklist |

**Sending media (Gate 4):**
- Send a photo/video in Telegram with the **shot number as the caption** (`3`, or `M01 3` if several episodes are open).
- Telegram bots can only download files up to **20 MB**. For bigger clips, copy them into `data/episodes/M01/media/in/` named `s3_anything.mp4` (shots can have several files).
- A direct image URL also works: `3 https://…`
- `/skip 2` turns a shot into a text card, `/clear 4` removes shot 4's files, `/callout 4 0.55 0.30 0.10` draws the red circle (x, y, radius as 0–1 fractions of the image).
- Enhancement: orientation fix, up to 3× upscale, contrast and sharpening, a sepia blend for archival shots, denoise/sharpen/colour for video (30 fps, audio stripped, max 20 s per clip).

All commands: `/help` in the bot. Every button belongs to one gate version, so tapping an old button is refused instead of acting on the wrong step.

## 4. Files

```
data/episodes/M01/   state.json, research.json, script.json, voice.m4a, media/, preview.mp4, final.mp4, cover.jpg, package/
data/pipeline.log
../03_performance_tracker.csv   gets a row when an episode is delivered
```

Posting stays manual on purpose: upload `final.mp4`, paste the caption, turn on Instagram's **AI-generated label** (the voice is synthetic), pin the sources comment, and add trending audio quietly if you want it.

## 5. Tests

```bash
.venv/bin/python -m pytest tests -q     # 56 tests, about a minute, no network except one marked test
```

Covered: script validation, TTS timings, captions, media intake and enhancement, rendering (1080×1920, loudness), the full approval flow with a mock LLM, and the Telegram layer against a fake HTTP server.

**Not verified against the real thing yet:** a live Telegram bot, a live LLM provider, ElevenLabs, the optional `realesrgan-ncnn-vulkan` upscaler hook, HEIC conversion (uses macOS `sips`), and the optional music bed (`AWN_MUSIC_FILE`). Try each once on a throwaway episode before relying on it. Many backlog facts are marked "(verify)"; treat every claim as unverified until Gate 1 shows a source you have checked.
