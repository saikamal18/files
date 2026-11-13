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
.venv/bin/awhynote init        # creates .env, downloads the brand fonts, makes data/
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

Default is free Microsoft Edge voices: `EDGE_VOICE=en-IN-PrabhatNeural` (try `en-IN-NeerjaNeural`) at `EDGE_RATE=+12%` (a brisker read than the voice's default). If your `.env` was created earlier it still says `EDGE_RATE=+0%`: change it. At Gate 3 you can reply `voice en-IN-NeerjaNeural` or `rate +20%`. ElevenLabs is supported (`TTS_PROVIDER=elevenlabs`, key and voice id) but has not been tried with a real key.

### Pictures from the web (for shots you have no photo for)

At Gate 4 tap **🌐 Fill missing shots from the web** (or send `/web`; `/web 3` for one shot). For each shot without media the bot asks the LLM for 2-3 search queries, searches **Wikimedia Commons** and **Openverse** (no key) and **Pexels** (free key, `PEXELS_API_KEY`), and keeps only pictures you may reuse with credit: public domain, CC0, CC BY and CC BY-SA (`AWN_WEB_LICENSES=strict` drops BY-SA). Anything NC/ND or unlicensed is dropped, as are tiny images. You get each pick as a photo with its author and licence.

- `/web 3` again swaps shot 3 for the next candidate; `/clear 3` removes it so you can send your own. A web picture never replaces a file you sent.
- Every picture is credited under **Images:** in the sources comment you pin. CC BY-SA asks for credit and the same licence on adaptations; check the licence in each message, this is not legal advice.
- **Banknote shots are never fetched:** shots about the note itself stay for your own photo. Fetched pictures are real photos, so check each one actually shows what the narration says.
- `AWN_WEB_IMAGES=auto` fills missing shots as soon as Gate 4 opens; `off` hides the button.

### Look and sound

| Setting (`.env`) | Values | What it does |
|---|---|---|
| `AWN_CAPTION_STYLE` | `poppins` (default), `montserrat`, `anton`, `bebas` | Caption and text-card typeface and highlight style. Every one has the ₹ glyph. Words pop in as they are spoken. See them with `awhynote styles` or `/styles` in the bot, switch with `/style anton` |
| `AWN_TRANSITION` | `mix` (default), `fade`, `cut` | `mix` picks per shot: flash into stat/text cards, dip or fade into archival photos, push into maps and stock, zoom-through into macro shots, a short fade inside a shot, a fade at the end. `push`, `zoom` and `flash` carry a soft whoosh or hit |
| `AWN_MUSIC` | `auto` (default), `ambient`, `off` | `auto` uses a track from `assets/music/` (stable pick per episode) and otherwise a soft generated pad. `ambient` always uses the pad. The music ducks under the voice |
| `AWN_MUSIC_FILE`, `AWN_MUSIC_DIR` | path | One fixed track, or a different music folder |
| `AWN_MUSIC_DB`, `AWN_SFX_DB` | dB (default `-19`, `-20`) | Music and effects level relative to the voice. Lower is quieter |

Use only music you have the rights to (royalty-free or Instagram-cleared). Drop `.mp3`/`.wav` files into `assets/music/`; names starting with `_` are ignored. Fonts are downloaded into `assets/fonts/` on the first run; `awhynote doctor` flags any font that is still a system fallback.

Rendering is now x264 `medium`, CRF 19, capped at 6 Mbit/s (about 42 MB for a 55 s Reel). Telegram only accepts files up to 50 MB, so if the result is bigger the Telegram copy `final.mp4` is re-encoded to about 46 MB and the full-quality `final_master.mp4` stays on your Mac in the episode folder.

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
data/episodes/M01/   state.json, research.json, script.json, voice.m4a, media/, preview.mp4, final.mp4 (final_master.mp4 if it had to be shrunk), cover.jpg, package/
data/pipeline.log
../03_performance_tracker.csv   gets a row when an episode is delivered
```

Posting stays manual on purpose: upload `final.mp4`, paste the caption, turn on Instagram's **AI-generated label** (the voice is synthetic), pin the sources comment, and add trending audio quietly if you want it.

## 5. Tests

```bash
.venv/bin/python -m pytest tests -q     # about 115 tests, about 4 minutes, no network except one marked test
```

Covered: script validation, TTS timings, captions, media intake and enhancement, rendering (1080×1920, loudness), the full approval flow with a mock LLM, and the Telegram layer against a fake HTTP server.

**Not verified against the real thing yet:** a live Telegram bot, a live LLM provider, ElevenLabs, the optional `realesrgan-ncnn-vulkan` upscaler hook, HEIC conversion (uses macOS `sips`), Openverse and Pexels replies (only the Wikimedia Commons reply was checked against the real API; Openverse timed out when I tried), and the generated music pad and sound effects (they are checked by measurement, not by ear: listen once on a throwaway episode and adjust `AWN_MUSIC_DB`). Try each once on a throwaway episode before relying on it. Many backlog facts are marked "(verify)"; treat every claim as unverified until Gate 1 shows a source you have checked.
