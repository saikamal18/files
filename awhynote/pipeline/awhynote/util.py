"""Small shared helpers: atomic JSON, ffmpeg runner, duration probe, fonts."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from .config import ROOT

FONT_DIR = ROOT / "assets" / "fonts"
FONT_URLS = {
    "ArchivoBlack-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/archivoblack/ArchivoBlack-Regular.ttf",
    "InstrumentSerif-Italic.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/instrumentserif/InstrumentSerif-Italic.ttf",
    "SpaceMono-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/spacemono/SpaceMono-Bold.ttf",
    "Poppins-ExtraBold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-ExtraBold.ttf",
    "Montserrat-Var.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf",
    "Anton-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
    "BebasNeue-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/bebasneue/BebasNeue-Regular.ttf",
}
SYSTEM_FALLBACKS = {
    "ArchivoBlack-Regular.ttf": "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "InstrumentSerif-Italic.ttf": "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
    "SpaceMono-Bold.ttf": "/System/Library/Fonts/Menlo.ttc",
    "Poppins-ExtraBold.ttf": "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "Montserrat-Var.ttf": "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "Anton-Regular.ttf": "/System/Library/Fonts/Supplemental/Impact.ttf",
    "BebasNeue-Regular.ttf": "/System/Library/Fonts/Supplemental/Impact.ttf",
}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_fonts(download: bool = True) -> dict[str, str]:
    """Return font paths. Downloads brand fonts once; falls back to macOS system fonts."""
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, url in FONT_URLS.items():
        target = FONT_DIR / name
        if not target.exists() and download:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "awhynote/0.1"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    target.write_bytes(r.read())
            except Exception:
                pass
        if target.exists() and target.stat().st_size > 10_000:
            out[name] = str(target)
        else:
            out[name] = SYSTEM_FALLBACKS[name]
    return out


def run_ffmpeg(ffmpeg: str, args: list[str], log_path: Path | None = None, input_bytes: bytes | None = None) -> None:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
    res = subprocess.run(cmd, input=input_bytes, capture_output=True)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(res.stderr or b"")
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({res.returncode}): {(res.stderr or b'').decode(errors='replace')[-1500:]}")


def media_info(ffmpeg: str, path: Path) -> dict:
    """Duration/size/rotation via `ffmpeg -i` (no ffprobe needed)."""
    res = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    txt = res.stderr
    info: dict = {"duration": None, "width": None, "height": None, "has_video": False, "has_audio": False}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    if m:
        info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", txt)
    if m:
        info["has_video"] = True
        info["width"], info["height"] = int(m[1]), int(m[2])
    rot = re.search(r"rotate\s*:\s*(-?\d+)", txt) or re.search(r"displaymatrix:\s*rotation of (-?[\d.]+)", txt)
    if rot and abs(int(float(rot[1]))) in (90, 270) and info["width"]:
        info["width"], info["height"] = info["height"], info["width"]
    info["has_audio"] = "Audio:" in txt
    return info


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXT


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def set_env(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for i, l in enumerate(lines):
        if l.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
