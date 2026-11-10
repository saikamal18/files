"""Episode folders and resumable state."""
from __future__ import annotations

import time
from pathlib import Path

from .util import read_json, write_json

STAGES = ["research", "script", "voice", "media", "render", "package"]
STAGE_LABEL = {
    "research": "Gate 1 · Facts & sources",
    "script": "Gate 2 · Script",
    "voice": "Gate 3 · Voice-over",
    "media": "Gate 4 · Your photos & footage",
    "render": "Gate 5 · Rough cut",
    "package": "Gate 6 · Caption, cover & final go",
}

# status values: working | awaiting_approval | collecting | failed | dropped | done


def now() -> float:
    return time.time()


class Episode:
    def __init__(self, root: Path, ep_id: str):
        self.id = ep_id
        self.dir = root / ep_id
        self.state: dict = read_json(self.dir / "state.json", {}) or {}

    @classmethod
    def create(cls, episodes_dir: Path, topic: dict, publish_date: str, post_time: str = "") -> "Episode":
        ep = cls(episodes_dir, topic["id"])
        ep.dir.mkdir(parents=True, exist_ok=True)
        ep.state = {
            "id": topic["id"],
            "topic": topic,
            "publish_date": publish_date,
            "post_time": post_time,
            "stage": "research",
            "status": "working",
            "versions": {s: 0 for s in STAGES},
            "gate": {},
            "feedback": {s: [] for s in STAGES},
            "voice_overrides": {},
            "created": now(),
            "events": [],
        }
        ep.log("episode created")
        ep.save()
        return ep

    @classmethod
    def exists(cls, episodes_dir: Path, ep_id: str) -> bool:
        return (episodes_dir / ep_id / "state.json").exists()

    # -- state helpers
    def save(self) -> None:
        write_json(self.dir / "state.json", self.state)

    def log(self, msg: str) -> None:
        self.state.setdefault("events", []).append({"t": now(), "msg": msg})
        self.state["events"] = self.state["events"][-200:]

    @property
    def stage(self) -> str:
        return self.state["stage"]

    @property
    def status(self) -> str:
        return self.state["status"]

    @property
    def topic(self) -> dict:
        return self.state["topic"]

    @property
    def publish_date(self) -> str:
        return self.state["publish_date"]

    def version(self, stage: str | None = None) -> int:
        return self.state["versions"][stage or self.stage]

    def bump(self, stage: str | None = None) -> int:
        s = stage or self.stage
        self.state["versions"][s] += 1
        return self.state["versions"][s]

    def feedback(self, stage: str | None = None) -> list[str]:
        return list(self.state["feedback"].get(stage or self.stage, []))

    def add_feedback(self, text: str, stage: str | None = None) -> None:
        self.state["feedback"].setdefault(stage or self.stage, []).append(text)

    def set(self, stage: str | None = None, status: str | None = None) -> None:
        if stage:
            self.state["stage"] = stage
        if status:
            self.state["status"] = status

    # -- artifacts
    def path(self, name: str) -> Path:
        return self.dir / name

    def versioned(self, stem: str, ext: str, stage: str | None = None) -> Path:
        return self.dir / f"{stem}.v{self.version(stage)}.{ext}"

    def read(self, name: str, default=None):
        return read_json(self.dir / name, default)

    def write(self, name: str, data) -> Path:
        p = self.dir / name
        write_json(p, data)
        return p


def list_episodes(episodes_dir: Path) -> list[Episode]:
    if not episodes_dir.exists():
        return []
    return [Episode(episodes_dir, p.name) for p in sorted(episodes_dir.iterdir()) if (p / "state.json").exists()]
