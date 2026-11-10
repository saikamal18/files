# awhynote: Master Prompts

Paste each block into your LLM or image/video tool and replace the `{PLACEHOLDERS}`. Always verify the output against primary sources before publishing.

---

## 0. Style bible (paste at the start of any chat)
```
You are the writer for "awhynote": 45-second explainers on the hidden history, design and tech of everyday things.
Voice: a curious friend who teaches well. Short sentences. One idea per line. No hype, no clickbait that the video does not pay off.
Rules:
- Every claim must be factual and verifiable. If something is a legend or a theory, say so ("reportedly", "one theory").
- Never invent numbers, dates or quotes. If you are not sure, put the line in "needs_verification".
- Plain English, 8th-grade reading level, India-first examples when relevant, but explain for a global audience.
- Never give instructions for counterfeiting. Only discuss publicly published security features.
```

## 1. Research brief
```
Topic: {TOPIC}
Region: {IN|GL}
Give me:
1. A claims table: claim | confidence (high/medium/low) | best primary source (name + URL) | second source.
2. The 5 most surprising, verified facts, ranked by "wow".
3. Common myths about this topic and the truth.
4. Anything controversial, disputed or legend-only that I must label as such.
5. Suggested search terms for archival images (public domain / Wikimedia Commons).
Do not guess. Mark unknowns as UNKNOWN.
```

## 2. Script writer (outputs JSON for the pipeline)
The pipeline (`pipeline/awhynote/prompts.py`) uses this same prompt automatically. Paste it manually only if you are writing a script by hand.
```
Using only the verified claims below, write a 45-second script for topic "{TOPIC}".
Constraints:
- 105-125 spoken words in total (the pipeline rejects anything outside 95-140 or over 58 s). Hook <= 12 words.
- Structure: HOOK (0-3s) -> CONTEXT (3-12s) -> FACT 1 -> FACT 2 -> FACT 3 -> TWIST (35-42s) -> OUTRO (42-48s).
- Hook style: {A: question | B: number/shock | C: myth-bust | D: "you've seen this a thousand times"}.
- Start with this hook line (rewrite only if it is inaccurate): "{FIRST_3S_HOOK}". Build toward this payoff: "{TWIST_PAYOFF}".
- 16-20 beats of 5-9 words each (one caption-sized thought), grouped into 8-10 shots (a shot can span 1-3 beats). Each shot is something the creator can photograph or film; the pipeline never invents imagery.
- OUTRO: one question for viewers to answer in the comments, then a teaser of the next episode "{NEXT_TOPIC}", then the sign-off "Everything has a why."
Output valid JSON only, using this schema:
{
  "series_tag": "NOTE 001",
  "title": "",
  "hook_style": "A|B|C|D",
  "shots": [
    {"id": 1, "visual_type": "macro|archival|map|illustration|stat|stock|text",
     "style": "full|card|text",
     "brief": "specific description of the real photo/footage the creator should provide",
     "stat_text": "big text for stat/text cards, else empty",
     "year_stamp": "e.g. 1938, else empty",
     "location_tag": "e.g. KONARK · ODISHA, else empty"}
  ],
  "beats": [
    {"id": 1, "shot": 1, "narration": "5-9 spoken words", "highlight": ["one key word from the narration"], "pause_after_s": 0.0}
  ],
  "sources_line": "SHORT · SOURCE · NAMES"
}
Verified claims:
{PASTE_CLAIMS}
```
Caption, hashtags and the pinned-comment sources are produced in a separate step (section 7).

## 3. Adversarial fact-check (run in a second model/chat)
```
You are a hostile fact-checker. Below is a script for a short explainer.
For every sentence: mark TRUE / FALSE / UNVERIFIABLE / MISLEADING, give the best source URL, and suggest a corrected line if needed.
Pay extra attention to dates, numbers, names, "first/only/biggest" claims, and legend-presented-as-fact.
Script:
{PASTE_SCRIPT}
```

## 4. Image prompts

**Shared style tail (append to every image prompt)**
```
cinematic, muted warm tones, subtle film grain, soft vignette, shallow depth of field, vertical 9:16, no text, no logos, no watermarks, no real currency or banknote replicas
```

| Visual type | Prompt template |
|---|---|
| Archival | `black-and-white archival photograph, {SUBJECT}, {ERA}, {LOCATION}, aged paper edges, slightly faded, 1:1 documentary look` + style tail |
| Atmospheric b-roll | `{SCENE}, golden hour, moody sky, natural colours` + style tail |
| Illustration | `flat paper-cut illustration of {SUBJECT}, limited palette ink black, cream and yellow, minimal` + style tail |
| Map | `minimal paper-toned map of {REGION}, thin lines, a single yellow dot marking {PLACE}, no labels` |

**Negative prompt:** `text, letters, logos, watermark, banknote, currency, deformed hands, extra fingers, modern signage, blurry`

**Hybrid documentary rule:** real photos and licensed footage are the foundation. Use AI only to animate selected images, build maps and diagrams, and fill gaps. Never present AI imagery as a real photograph of a real person or place.

**Notes on real objects:**
- Don't AI-generate currency. Use your own photo/scan of the note and add the red-circle callouts in the editor.
- Use real archival images from Wikimedia Commons or museum open-access collections and credit them.

## 5. Image-to-video prompt (Runway / Kling / Veo / Luma)
```
Slow {push-in|pan left|pan right} on the image, 5 seconds, subtle parallax, no morphing, no new objects, keep all details stable, cinematic, no text.
```
Keep clips to 3-5 s and reuse the best ones. Static images with a Ken Burns zoom in the editor are fine for most beats.

## 6. Voice-over directions
```
Calm, warm, measured pace around 150 words per minute. Slight emphasis on numbers and names. Short pause (0.3s) after the hook and before the twist. No dramatic announcer voice.
```
Use commas and ellipses in the script to shape pauses. Keep the same voice across all videos.

## 7. Caption, hashtags and pinned comment
```
From this script: {SCRIPT}
Write:
1. An Instagram caption (max 4 lines): line 1 is a search-friendly hook with the topic keywords, line 2 is one surprising fact, line 3 is "Save this. Send it to someone who'd ask why.", line 4 is "Sources in pinned comment."
2. 3-5 relevant hashtags (no generic spam like #viral).
3. A pinned comment listing each claim with its source and URL.
```

## 8. Cover prompt
```
Cover for a short video titled "{TITLE}". Dark ink-black background, large bold white headline of at most 6 words with one word in a yellow highlight box, series tag "{TAG} 001" in monospace top-left, subject image in the centre 3:4 safe area, no clutter.
```
(Generate the background image with AI, but add all text in your editor so the spelling is exact.)

## 9. Weekly analytics review
```
Here is my posting tracker for the last 7 days (CSV):
{PASTE_CSV}
Tell me:
1. Which 2 videos performed best on shares per 1k reach and 3-second hold, and what they have in common (topic, hook style, visual, length).
2. Which 2 performed worst, and the most likely reason.
3. Which hook style (A/B/C) and pillar to do more of next week.
4. 7 new topic ideas in the same direction (and 3 that are different, to test).
Be specific, and do not give generic advice.
```

## 10. Topic generator (to keep the backlog full)
```
Suggest 20 new topics for awhynote in the pillar {Money|History|Tech|Myth}, region {IN|GL}.
For each: a hook title (<= 12 words), the one verifiable surprising fact behind it, and the best source to verify it.
Avoid topics already in this list: {PASTE_EXISTING_TITLES}
```
