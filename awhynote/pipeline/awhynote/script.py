"""Stage 2: shots+beats script with local normalisation, validation, auto-repair and a fact-check pass."""
from __future__ import annotations

import re

from . import prompts
from .config import Config
from .llm import LLM
from .store import Episode

WORDS_PER_SEC = 2.5
MAX_SECONDS = 58.0
VISUAL_TYPES = {"macro", "archival", "map", "illustration", "stat", "stock", "text"}
DEFAULT_STYLE = {"archival": "card", "stat": "text", "text": "text"}
OUTRO = "Everything has a why."


def words(s: str) -> list[str]:
    return re.findall(r"\S+", s)


def clean_word(w: str) -> str:
    return re.sub(r"^\W+|\W+$", "", w, flags=re.U).lower()


def estimate_seconds(beats: list[dict]) -> float:
    n = sum(len(words(b["narration"])) for b in beats)
    return n / WORDS_PER_SEC + sum(float(b.get("pause_after_s", 0) or 0) for b in beats)


def normalize(script: dict) -> dict:
    """Fix what can be fixed deterministically so the validator only reports real problems."""
    shots = []
    for i, s in enumerate(script.get("shots") or [], 1):
        vt = str(s.get("visual_type", "stock")).lower()
        vt = vt if vt in VISUAL_TYPES else "stock"
        style = str(s.get("style") or DEFAULT_STYLE.get(vt, "full")).lower()
        if style not in ("full", "card", "text"):
            style = DEFAULT_STYLE.get(vt, "full")
        if vt in ("stat", "text"):
            style = "text"
        shots.append({
            "id": int(s.get("id", i)), "visual_type": vt, "style": style,
            "brief": str(s.get("brief", "")).strip(), "stat_text": str(s.get("stat_text", "")).strip(),
            "year_stamp": str(s.get("year_stamp", "")).strip(), "location_tag": str(s.get("location_tag", "")).strip(),
        })
    beats = []
    for i, b in enumerate(script.get("beats") or [], 1):
        text = re.sub(r"\s+", " ", str(b.get("narration", ""))).strip()
        hl = b.get("highlight") or []
        if isinstance(hl, str):
            hl = [hl]
        toks = {clean_word(w) for w in words(text)}
        hl = [h for h in hl if clean_word(str(h)) in toks][:2]
        try:
            pause = max(0.0, min(1.0, float(b.get("pause_after_s", 0) or 0)))
        except (TypeError, ValueError):
            pause = 0.0
        beats.append({"id": i, "shot": int(b.get("shot", 1) or 1), "narration": text, "highlight": hl, "pause_after_s": pause})
    out = dict(script)
    out.update({"shots": shots, "beats": beats})
    out.setdefault("sources_line", "")
    # the sign-off is a brand rule, so enforce it locally instead of spending an LLM retry on it
    if beats and OUTRO.lower().rstrip(".") not in beats[-1]["narration"].lower():
        beats.append({"id": len(beats) + 1, "shot": beats[-1]["shot"], "narration": OUTRO, "highlight": ["why"], "pause_after_s": 0.0})
        out["auto_fixed"] = ["added sign-off line"]
    return out


def validate(script: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []
    shots, beats = script.get("shots", []), script.get("beats", [])
    if not shots or not beats:
        return ["script has no shots or beats"], warns
    ids = [s["id"] for s in shots]
    if ids != sorted(set(ids)):
        errors.append("shot ids must be unique and ascending")
    if not 6 <= len(shots) <= 12:
        (errors if not 4 <= len(shots) <= 14 else warns).append(f"{len(shots)} shots (aim for 8-10)")
    used = []
    for b in beats:
        if b["shot"] not in ids:
            errors.append(f"beat {b['id']} points to unknown shot {b['shot']}")
        used.append(b["shot"])
        n = len(words(b["narration"]))
        if n < 3 or n > 16:
            errors.append(f"beat {b['id']} has {n} words (must be 5-9, hard limits 3-16)")
        elif n < 5 or n > 14:
            warns.append(f"beat {b['id']} has {n} words")
    if used != sorted(used):
        errors.append("beats must go through the shots in order")
    for s in shots:
        if s["id"] not in used:
            errors.append(f"shot {s['id']} has no beat")
        if s["style"] != "text" and not s["brief"]:
            warns.append(f"shot {s['id']} has no brief for the creator")
    total = sum(len(words(b["narration"])) for b in beats)
    if total < 95 or total > 140:
        errors.append(f"{total} words in total; need 105-125 (hard limits 95-140)")
    elif total < 100 or total > 125:
        warns.append(f"{total} words (aim for 105-125)")
    est = estimate_seconds(beats)
    if est > MAX_SECONDS:
        errors.append(f"estimated {est:.0f}s is over the {MAX_SECONDS:.0f}s cap")
    return errors, warns


def narration_text(script: dict) -> str:
    return " ".join(b["narration"] for b in script["beats"])


def run(cfg: Config, ep: Episode, next_title: str = "") -> dict:
    research = ep.read("research.json", {}) or {}
    claims = research.get("usable_claims") or []
    if not claims:
        raise RuntimeError("research has no high/medium-confidence claims; go back and use Edit to ask for better sources")
    legend = research.get("label_as_legend") or []
    llm = LLM(cfg, "default")
    feedback = ep.feedback("script")
    payload = {"topic": ep.topic, "next_title": next_title}
    system, user = prompts.script_prompt(ep.topic, claims, legend, next_title, feedback)
    script = normalize(llm.complete_json(system, user, task="script", mock_payload=payload))
    errors, warns = validate(script)
    if errors:
        fix = feedback + ["Your last draft had these problems, fix them: " + "; ".join(errors)]
        system, user = prompts.script_prompt(ep.topic, claims, legend, next_title, fix)
        script = normalize(llm.complete_json(system, user, task="script", mock_payload=payload))
        errors, warns = validate(script)
    script["validation"] = {"errors": errors, "warnings": warns, "words": sum(len(words(b["narration"])) for b in script["beats"]),
                            "est_seconds": round(estimate_seconds(script["beats"]), 1)}
    script["fact_check"] = fact_check(cfg, script, claims)
    ep.write(f"script.v{ep.version('script')}.json", script)
    ep.write("script.json", script)
    return script


def fact_check(cfg: Config, script: dict, claims: list[dict]) -> dict:
    llm = LLM(cfg, "check")
    system, user = prompts.factcheck_prompt(narration_text(script), claims)
    try:
        data = llm.complete_json(system, user, task="factcheck", mock_payload={"lines": [b["narration"] for b in script["beats"]]})
    except Exception as e:  # a failed check must not block the gate; the human still reviews
        return {"ok": False, "error": str(e), "flags": []}
    flags = [v for v in data.get("verdicts", []) if str(v.get("verdict", "")).upper() != "TRUE"]
    return {"ok": True, "flags": flags, "checked_by": f"{llm.provider}:{llm.model or 'mock'}"}


def summary_text(script: dict) -> str:
    v = script["validation"]
    lines = [f"📝 {script.get('title', '')}  ·  {v['words']} words ≈ {v['est_seconds']}s", ""]
    by_shot: dict[int, list[str]] = {}
    for b in script["beats"]:
        by_shot.setdefault(b["shot"], []).append(b["narration"])
    for s in script["shots"]:
        brief = s["brief"] or s["stat_text"] or ""
        lines.append(f"🎞 Shot {s['id']} [{s['visual_type']}] — {brief}")
        for n in by_shot.get(s["id"], []):
            lines.append(f"   “{n}”")
    if v["errors"]:
        lines += ["", "⛔ Still failing checks: " + "; ".join(v["errors"])]
    if v["warnings"]:
        lines += ["", "⚠️ " + "; ".join(v["warnings"][:4])]
    fc = script.get("fact_check", {})
    if fc.get("ok") and fc.get("flags"):
        lines.append("")
        lines.append("🕵️ Fact-check flags:")
        for f in fc["flags"][:6]:
            lines.append(f" • {f.get('verdict')}: “{f.get('line', '')}” — {f.get('note', '')}")
    elif fc.get("ok"):
        lines += ["", "🕵️ Second-pass fact-check: no flags"]
    else:
        lines += ["", "🕵️ Fact-check could not run: " + fc.get("error", "")[:120]]
    return "\n".join(lines)
