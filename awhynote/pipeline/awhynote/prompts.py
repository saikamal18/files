"""Prompt builders. Mirrors ../05_master_prompts.md; the script schema here is the source of truth."""
from __future__ import annotations

import json

STYLE_BIBLE = """You are the writer for "awhynote": 45-second explainers on the hidden history, design and tech of everyday things.
Voice: a curious friend who teaches well. Short sentences. One idea per line. No hype, no clickbait that the video does not pay off.
Rules:
- Every claim must be factual and verifiable. If something is a legend or a theory, say so ("reportedly", "one theory").
- Never invent numbers, dates, names or quotes. If you are not sure, put it in "needs_verification" instead of stating it.
- Plain English, 8th-grade reading level. India-first examples when relevant, but explain for a global audience.
- Never give instructions for counterfeiting. Only discuss publicly published security features.
- Reply with valid JSON only, no commentary."""

RESEARCH_SCHEMA = """{
  "topic": "",
  "claims": [
    {"claim": "", "confidence": "high|medium|low", "source_name": "", "source_url": "", "second_source": ""}
  ],
  "surprising_facts": [""],
  "myths": [{"myth": "", "truth": ""}],
  "label_as_legend": [""],
  "shot_ideas": ["what real photo/footage would illustrate this"]
}"""

SCRIPT_SCHEMA = """{
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
}"""

FACTCHECK_SCHEMA = """{"verdicts": [{"line": "", "verdict": "TRUE|FALSE|UNVERIFIABLE|MISLEADING", "note": "", "fix": ""}]}"""

PACKAGE_SCHEMA = """{
  "caption": "4 lines: search-friendly hook / one surprising fact / 'Save this. Send it to someone who'd ask why.' / 'Sources in pinned comment.'",
  "hashtags": ["3-5 relevant, no generic spam"],
  "pinned_comment_sources": [{"claim": "", "source": "", "url": ""}],
  "cover": {"title": "6 words or fewer", "highlight": "one word from the title"}
}"""


def _fmt_feedback(feedback: list[str]) -> str:
    if not feedback:
        return ""
    lines = "\n".join(f"- {f}" for f in feedback)
    return f"\nThe creator gave this feedback on earlier versions. Apply ALL of it:\n{lines}\n"


def topic_block(topic: dict) -> str:
    keys = ["id", "series", "pillar", "region", "hook_title", "first_3s_hook", "twist_payoff", "hook_type", "angle_to_explore", "verify_with"]
    return json.dumps({k: topic.get(k, "") for k in keys}, ensure_ascii=False, indent=2)


def research_prompt(topic: dict, feedback: list[str]) -> tuple[str, str]:
    user = f"""Topic brief:
{topic_block(topic)}

Build a research brief as JSON with this schema:
{RESEARCH_SCHEMA}

Requirements:
- 6-10 claims, each atomic and checkable. Prefer primary sources (central banks, museums, archives, official bodies).
- confidence = "high" only if you are sure AND can name a primary source; otherwise "medium" or "low". Do not guess: if unknown, use "low" and say so in the claim.
- source_url must be a real page you are confident exists (the creator's pipeline will open it). If unsure, leave it empty.
- Flag anything that is legend, disputed or theory in label_as_legend.
- shot_ideas: real photos or footage the creator can shoot or source (own photos of the object, archival images, maps).
{_fmt_feedback(feedback)}"""
    return STYLE_BIBLE, user


def script_prompt(topic: dict, claims: list[dict], legend: list[str], next_title: str, feedback: list[str]) -> tuple[str, str]:
    claims_json = json.dumps(claims, ensure_ascii=False, indent=1)
    user = f"""Write a 45-second script for this topic.

Topic brief:
{topic_block(topic)}

Verified claims you may use (use NOTHING else as fact):
{claims_json}

Label as legend/theory if used: {json.dumps(legend, ensure_ascii=False)}

Constraints:
- 105-125 spoken words in total across all beats. Each beat is 5-9 words (one caption-sized thought).
- 16-20 beats, grouped into 8-10 shots (a shot is one piece of real media on screen; a shot can span 1-3 beats).
- Structure: HOOK (beat 1, use this first line unless inaccurate: "{topic.get('first_3s_hook', '')}") -> CONTEXT -> 3 facts -> TWIST (build toward: "{topic.get('twist_payoff', '')}") -> OUTRO.
- Hook style: {topic.get('hook_type', 'A')} (A question, B number/shock, C myth-bust, D "you've seen this a thousand times").
- OUTRO (last 2 beats): one question for viewers to answer in the comments, a teaser for the next episode ("{next_title}"), and the final line "Everything has a why."
- The creator supplies REAL photos/footage. In each shot's "brief" say exactly what to photograph or source. Use visual_type "archival" for old photos (style "card"), "macro" for close-ups of the real object, "map", "stock" for b-roll, "stat"/"text" for big-number or text cards (style "text", no media needed).
- Never ask for AI-generated banknotes. Banknote shots must be the creator's own photo/scan.
- highlight: one word per beat that deserves the yellow caption box (a number, name or key term, exactly as in the narration).

Return JSON with this schema:
{SCRIPT_SCHEMA}
{_fmt_feedback(feedback)}"""
    return STYLE_BIBLE, user


def factcheck_prompt(narration: str, claims: list[dict]) -> tuple[str, str]:
    system = "You are a hostile fact-checker for a short educational video. Reply with valid JSON only."
    user = f"""For every sentence in the script below, give a verdict: TRUE / FALSE / UNVERIFIABLE / MISLEADING, a brief note, and a corrected line if needed.
Pay special attention to dates, numbers, names, "first/only/biggest" claims, and legends presented as fact.

Approved claims the script was supposed to stay within:
{json.dumps(claims, ensure_ascii=False)}

Script:
{narration}

Return JSON: {FACTCHECK_SCHEMA}"""
    return system, user


def package_prompt(topic: dict, narration: str, claims: list[dict], feedback: list[str]) -> tuple[str, str]:
    user = f"""Write the Instagram package for this finished video.

Topic: {topic.get('hook_title', '')}
Narration:
{narration}

Approved claims and sources:
{json.dumps(claims, ensure_ascii=False)}

Return JSON with this schema:
{PACKAGE_SCHEMA}
Rules: caption line 1 is a search-friendly hook with the topic keywords; no emojis spam; pinned_comment_sources lists each used claim with its source and URL (leave URL empty if unknown).
{_fmt_feedback(feedback)}"""
    return STYLE_BIBLE, user
