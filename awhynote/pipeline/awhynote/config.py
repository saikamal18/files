"""Configuration: .env file + process environment (environment wins)."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # .../awhynote/pipeline
PROJECT = ROOT.parent  # .../awhynote (backlog, calendar, brand kit live here)


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


@dataclass
class Config:
    data_dir: Path
    backlog_csv: Path
    calendar_csv: Path
    tracker_csv: Path
    handle: str
    lead_days: int
    reminder_hours: float
    max_reminders: int
    max_active: int
    llm_provider: str
    llm_api_key: str
    llm_model: str
    llm_check_model: str
    llm_base_url: str
    tts_provider: str
    edge_voice: str
    edge_rate: str
    eleven_key: str
    eleven_voice: str
    telegram_token: str
    telegram_chat_id: str
    music_file: str
    music_db: float
    raw: dict | None = None
    fps: int = 30
    width: int = 1080
    height: int = 1920

    @property
    def episodes_dir(self) -> Path:
        return self.data_dir / "episodes"

    @property
    def ffmpeg(self) -> str:
        explicit = os.environ.get("FFMPEG")
        if explicit:
            return explicit
        found = shutil.which("ffmpeg")
        if found:
            return found
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()

    @classmethod
    def load(cls, env_path: Path | None = None, overrides: dict[str, str] | None = None) -> "Config":
        env = load_env_file(env_path or (ROOT / ".env"))
        env.update({k: v for k, v in os.environ.items() if k.startswith(("AWN_", "LLM_", "TTS_", "TELEGRAM_", "EDGE_", "ELEVEN"))})
        if overrides:
            env.update(overrides)

        def g(key: str, default: str = "") -> str:
            return env.get(key, default)

        data_dir = Path(g("AWN_DATA_DIR", str(ROOT / "data"))).expanduser()
        return cls(
            data_dir=data_dir,
            backlog_csv=Path(g("AWN_BACKLOG_CSV", str(PROJECT / "01_topic_backlog.csv"))),
            calendar_csv=Path(g("AWN_CALENDAR_CSV", str(PROJECT / "02_content_calendar_30d.csv"))),
            tracker_csv=Path(g("AWN_TRACKER_CSV", str(PROJECT / "03_performance_tracker.csv"))),
            handle=g("AWN_HANDLE", "@awhynote"),
            lead_days=int(g("AWN_LEAD_DAYS", "5")),
            reminder_hours=float(g("AWN_REMINDER_HOURS", "4")),
            max_reminders=int(g("AWN_MAX_REMINDERS", "3")),
            max_active=int(g("AWN_MAX_ACTIVE", "3")),
            llm_provider=g("LLM_PROVIDER", "mock").lower(),
            llm_api_key=g("LLM_API_KEY"),
            llm_model=g("LLM_MODEL"),
            llm_check_model=g("LLM_CHECK_MODEL"),
            llm_base_url=g("LLM_BASE_URL", "https://api.openai.com/v1"),
            tts_provider=g("TTS_PROVIDER", "edge").lower(),
            edge_voice=g("EDGE_VOICE", "en-IN-PrabhatNeural"),
            edge_rate=g("EDGE_RATE", "+0%"),
            eleven_key=g("ELEVENLABS_API_KEY"),
            eleven_voice=g("ELEVENLABS_VOICE_ID"),
            telegram_token=g("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=g("TELEGRAM_CHAT_ID"),
            music_file=g("AWN_MUSIC_FILE"),
            music_db=float(g("AWN_MUSIC_DB", "-22")),
            raw=dict(env),
        )
