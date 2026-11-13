"""Deterministic offline stand-ins for the LLM, used by tests and `LLM_PROVIDER=mock`."""
from __future__ import annotations


def _words(s: str) -> list[str]:
    return s.split()


def mock_llm(task: str, payload: dict) -> dict:
    topic = payload.get("topic", {})
    title = topic.get("hook_title", "Something everyday")
    if task == "research":
        return {
            "topic": title,
            "claims": [
                {"claim": f"{title}: fact number {i}", "confidence": "high" if i < 6 else "low",
                 "source_name": "Mock Archive", "source_url": "", "second_source": ""}
                for i in range(1, 9)
            ],
            "surprising_facts": ["A surprising mock fact"],
            "myths": [{"myth": "A mock myth", "truth": "A mock truth"}],
            "label_as_legend": ["A mock legend"],
            "shot_ideas": ["your own close-up photo", "an archival photo"],
        }
    if task == "script":
        hook = topic.get("first_3s_hook") or f"Have you ever wondered about {title}?"
        nxt = payload.get("next_title") or "the next episode"
        beats_text = [
            hook,
            "Look closely at it today.",
            "Most people never notice this detail.",
            "The story starts a long time ago.",
            "Back then, nobody expected it to last.",
            "Yet one clever idea changed everything.",
            "First, the design solved a real problem.",
            "Second, it was cheap to repeat at scale.",
            "Third, people trusted it instantly.",
            "That trust is the quiet secret here.",
            "Now comes the twist you never saw coming.",
            "The reason is far simpler than the legend.",
            "It was never about looking pretty.",
            "It was about being understood by everyone.",
            "So the next time you see one, pause.",
            "Which everyday thing should we explain next?",
            f"Tomorrow: {nxt}.",
            "Everything has a why.",
        ]
        # beats -> shots: roughly two beats per shot, the final shot is a text card
        shots, beats = [], []
        vtypes = ["macro", "archival", "stock", "map", "archival", "macro", "stat", "macro", "text"]
        for i, v in enumerate(vtypes, 1):
            style = {"archival": "card", "stat": "text", "text": "text"}.get(v, "full")
            shots.append({"id": i, "visual_type": v, "style": style, "brief": f"Real {v} footage or photo for beat group {i}",
                          "stat_text": "10 YEARS" if v == "stat" else ("EVERYTHING HAS A WHY." if v == "text" else ""),
                          "year_stamp": "1938" if v == "archival" and i == 2 else "", "location_tag": "HYDERABAD" if v == "map" else ""})
        for i, t in enumerate(beats_text, 1):
            shot = min(len(vtypes), (i + 1) // 2) if i < len(beats_text) else len(vtypes)
            ws = _words(t)
            beats.append({"id": i, "shot": shot, "narration": t, "highlight": [ws[min(2, len(ws) - 1)].strip(".,?:")], "pause_after_s": 0.0})
        return {"series_tag": "NOTE 001", "title": title, "hook_style": topic.get("hook_type", "A"),
                "shots": shots, "beats": beats, "sources_line": "MOCK · ARCHIVE"}
    if task == "factcheck":
        lines = payload.get("lines", [])
        return {"verdicts": [{"line": l, "verdict": "TRUE", "note": "", "fix": ""} for l in lines[:3]]}
    if task == "web_queries":
        title = title.strip() or "India"
        return {"queries": {str(sh["id"]): [f"{(sh.get('brief') or title)[:40]} photo"] for sh in payload.get("shots", [])}}
    if task == "package":
        return {
            "caption": f"{title}\nA surprising mock fact.\nSave this. Send it to someone who'd ask why.\nSources in pinned comment.",
            "hashtags": ["#awhynote", "#didyouknow", "#india"],
            "pinned_comment_sources": [{"claim": "mock claim", "source": "Mock Archive", "url": ""}],
            "cover": {"title": " ".join(_words(title)[:5]) or "Why", "highlight": (_words(title) or ["Why"])[0]},
        }
    return {}
