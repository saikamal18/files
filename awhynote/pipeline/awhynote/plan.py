"""Backlog / calendar / tracker CSV access and scheduling."""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import Config


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_backlog(cfg: Config) -> dict[str, dict]:
    return {r["id"]: r for r in _read(cfg.backlog_csv)}


def load_calendar(cfg: Config) -> list[dict]:
    rows = _read(cfg.calendar_csv)
    rows.sort(key=lambda r: (r["date"], r["post_time_IST"]))
    return rows


def topic_for(cfg: Config, topic_id: str) -> dict | None:
    return load_backlog(cfg).get(topic_id)


def calendar_row(cfg: Config, topic_id: str) -> dict | None:
    return next((r for r in load_calendar(cfg) if r["topic_id"] == topic_id), None)


def next_title(cfg: Config, topic_id: str) -> str:
    cal = load_calendar(cfg)
    for i, r in enumerate(cal):
        if r["topic_id"] == topic_id and i + 1 < len(cal):
            return cal[i + 1]["hook_title"]
    return ""


def due(cfg: Config, started: set[str], today: date | None = None) -> list[tuple[dict, dict]]:
    """Calendar rows whose publish date falls within the lead window and that have not been started."""
    today = today or date.today()
    horizon = today + timedelta(days=cfg.lead_days)
    backlog = load_backlog(cfg)
    out = []
    for r in load_calendar(cfg):
        d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        if r["topic_id"] in started or r["topic_id"] not in backlog:
            continue
        if d <= horizon:
            out.append((backlog[r["topic_id"]], r))
    return out


TRACKER_FIELDS = ["date", "topic_id", "hook_type", "post_time", "reach", "views", "3s_hold_pct", "avg_watch_pct",
                  "shares", "saves", "comments", "profile_visits", "follows", "what_to_repeat_or_fix"]


def append_tracker(cfg: Config, topic: dict, publish_date: str, post_time: str) -> None:
    path = cfg.tracker_csv
    exists = path.exists() and path.stat().st_size > 0
    if exists and any(r.get("topic_id") == topic["id"] for r in _read(path)):
        return
    row = {k: "" for k in TRACKER_FIELDS}
    row.update({"date": publish_date, "topic_id": topic["id"], "hook_type": topic.get("hook_type", ""), "post_time": post_time})
    with open(path, "a", encoding="utf-8-sig" if not exists else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRACKER_FIELDS)
        if not exists:
            w.writeheader()
        w.writerow(row)
