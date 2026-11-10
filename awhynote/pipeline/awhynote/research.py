"""Stage 1: research brief with confidence filtering and source URL checks."""
from __future__ import annotations

import requests

from . import prompts
from .config import Config
from .llm import LLM
from .store import Episode

UA = {"User-Agent": "Mozilla/5.0 (awhynote research bot)"}


def check_url(url: str, timeout: float = 8.0) -> str:
    """Return 'ok', 'unreachable' or 'blocked' (many sites answer bots with 403/429; that is inconclusive)."""
    if not url or not url.startswith(("http://", "https://")):
        return "none"
    try:
        r = requests.get(url, headers=UA, timeout=timeout, stream=True, allow_redirects=True)
        r.close()
    except requests.RequestException:
        return "unreachable"
    if r.status_code < 400:
        return "ok"
    if r.status_code in (401, 403, 429):
        return "blocked"
    return "unreachable"


def run(cfg: Config, ep: Episode, *, check_urls: bool = True) -> dict:
    llm = LLM(cfg, "research")
    system, user = prompts.research_prompt(ep.topic, ep.feedback("research"))
    data = llm.complete_json(system, user, task="research", mock_payload={"topic": ep.topic})
    claims = [c for c in data.get("claims", []) if c.get("claim")]
    for c in claims:
        c["confidence"] = str(c.get("confidence", "low")).lower()
        c["url_status"] = check_url(c.get("source_url", "")) if check_urls and llm.provider != "mock" else "skipped"
        if c["url_status"] == "unreachable":
            c["confidence"] = "low"
            c["note"] = "source URL did not open - verify manually"
    usable = [c for c in claims if c["confidence"] in ("high", "medium")]
    data["claims"] = claims
    data["usable_claims"] = usable
    ep.write(f"research.v{ep.version('research')}.json", data)
    ep.write("research.json", data)
    return data


def summary_text(data: dict) -> str:
    lines = [f"🔎 Research: {data.get('topic', '')}", ""]
    icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}
    for c in data.get("claims", []):
        src = c.get("source_name") or "no source"
        url = {"ok": "✓", "blocked": "~", "unreachable": "✗", "none": "–", "skipped": ""}.get(c.get("url_status", ""), "")
        lines.append(f"{icon.get(c['confidence'], '⚪')} {c['claim']}  [{src} {url}]".rstrip())
    legends = data.get("label_as_legend") or []
    if legends:
        lines += ["", "⚠️ Present as legend/theory: " + "; ".join(legends)]
    if not data.get("usable_claims"):
        lines += ["", "⛔ No claim is solid enough to build a script on. Use Edit to point me at sources, or Redo."]
    lines += ["", f"Only 🟢/🟡 claims ({len(data.get('usable_claims', []))}) will be used in the script.",
              "Edit = tell me what to add, fix or investigate."]
    return "\n".join(lines)
