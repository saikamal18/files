"""Stage 4: collect the creator's real media per shot, enhance it, build a contact sheet."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageOps

from .config import Config
from .enhance import enhance_image, enhance_video
from .store import Episode
from .util import IMAGE_EXT, VIDEO_EXT, is_image, is_video, read_json, write_json

MAX_DOWNLOAD = 300 * 1024 * 1024
NAME_RE = re.compile(r"^(?:shot|s)?[\s_-]*0*(\d{1,2})(?:[^\d]|$)", re.I)


def parse_shot_number(name: str) -> int | None:
    m = NAME_RE.match(Path(name).stem)
    return int(m.group(1)) if m else None


def inbox(ep: Episode) -> Path:
    p = ep.path("media/in")
    p.mkdir(parents=True, exist_ok=True)
    return p


def shot_windows(voice: dict, shots: list[dict]) -> dict[int, tuple[float, float]]:
    first: dict[int, float] = {}
    for b in voice["beats"]:
        first.setdefault(b["shot"], b["start"])
    order = [s["id"] for s in shots if s["id"] in first]
    out = {}
    for i, sid in enumerate(order):
        end = first[order[i + 1]] if i + 1 < len(order) else voice["duration"]
        out[sid] = (first[sid], end)
    return out


def _unique(dirpath: Path, name: str) -> Path:
    p = dirpath / name
    i = 2
    while p.exists():
        p = dirpath / f"{p.stem.rsplit('~', 1)[0]}~{i}{p.suffix}"
        i += 1
    return p


def add_file(ep: Episode, shot: int, data: bytes, filename: str) -> Path:
    ext = Path(filename).suffix.lower() or ".jpg"
    if ext not in IMAGE_EXT | VIDEO_EXT:
        raise ValueError(f"unsupported file type {ext}")
    dest = _unique(inbox(ep), f"s{shot}_{Path(filename).stem[:30]}{ext}")
    dest.write_bytes(data)
    return dest


def add_url(ep: Episode, shot: int, url: str) -> Path:
    if not url.startswith(("http://", "https://")):
        raise ValueError("not a URL")
    r = requests.get(url, stream=True, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    ctype = r.headers.get("content-type", "").split(";")[0]
    ext = Path(url.split("?")[0]).suffix.lower()
    if ext not in IMAGE_EXT | VIDEO_EXT:
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "video/mp4": ".mp4"}.get(ctype, "")
    if not ext:
        raise ValueError(f"URL is not a direct image/video link (content-type {ctype or 'unknown'})")
    buf = bytearray()
    for chunk in r.iter_content(1 << 16):
        buf.extend(chunk)
        if len(buf) > MAX_DOWNLOAD:
            raise ValueError("file is larger than 300 MB")
    return add_file(ep, shot, bytes(buf), Path(url.split("?")[0]).name or f"download{ext}")


def meta(ep: Episode) -> dict:
    m = read_json(ep.path("media_meta.json"), {}) or {}
    m.setdefault("skip", [])
    m.setdefault("callouts", {})
    return m


def set_skip(ep: Episode, shot: int, on: bool = True) -> None:
    m = meta(ep)
    s = set(m["skip"])
    (s.add if on else s.discard)(shot)
    m["skip"] = sorted(s)
    write_json(ep.path("media_meta.json"), m)


def set_callout(ep: Episode, shot: int, x: float, y: float, r: float) -> None:
    m = meta(ep)
    m["callouts"][str(shot)] = {"x": min(1, max(0, x)), "y": min(1, max(0, y)), "r": min(0.5, max(0.02, r))}
    write_json(ep.path("media_meta.json"), m)


def scan(ep: Episode, script: dict) -> dict:
    """Which shots have media, which are missing, which files could not be matched to a shot."""
    m = meta(ep)
    files: dict[int, list[Path]] = {s["id"]: [] for s in script["shots"]}
    unassigned: list[str] = []
    for p in sorted(inbox(ep).iterdir()):
        if p.name.startswith(".") or not (is_image(p) or is_video(p)):
            continue
        n = parse_shot_number(p.name)
        if n in files:
            files[n].append(p)
        else:
            unassigned.append(p.name)
    missing, ready_shots = [], {}
    for s in script["shots"]:
        sid = s["id"]
        have = files[sid]
        needs = s["style"] != "text"
        if have:
            state = "media"
        elif sid in m["skip"]:
            state = "skipped"
        elif needs:
            state = "missing"
            missing.append(sid)
        else:
            state = "auto"
        ready_shots[sid] = {"state": state, "files": [str(f) for f in have]}
    return {"shots": ready_shots, "missing": missing, "unassigned": unassigned, "ready": not missing and not unassigned}


def checklist_text(ep: Episode, script: dict, voice: dict, scanned: dict) -> str:
    win = shot_windows(voice, script["shots"])
    icon = {"media": "✅", "skipped": "⏭", "missing": "❌", "auto": "🔤"}
    lines = [f"🎬 Shot list for {ep.topic['hook_title']}", ""]
    for s in script["shots"]:
        st = scanned["shots"][s["id"]]
        a, b = win.get(s["id"], (0, 0))
        what = s["brief"] or s["stat_text"]
        extra = f"  [{s['year_stamp']}]" if s["year_stamp"] else ""
        lines.append(f"{icon[st['state']]} {s['id']}. ({b - a:.1f}s) {what}{extra}")
        if st["files"]:
            lines.append(f"     {len(st['files'])} file(s)")
    if scanned["unassigned"]:
        lines += ["", "❓ Files with no shot number (rename to e.g. s3_name.jpg): " + ", ".join(scanned["unassigned"][:6])]
    lines += ["",
              "Send photos/videos with the shot number as caption (e.g. `3`), or drop files named `s3_name.jpg` into:",
              str(inbox(ep)),
              "Big files (over 20 MB) must use the folder. A direct image URL + number also works: `3 https://…`",
              "Commands: /status · /skip 2 (use a text card) · /callout 4 0.55 0.30 0.10 (red circle at x,y radius, 0-1) · /done"]
    return "\n".join(lines)


def prepare(cfg: Config, ep: Episode, script: dict, voice: dict) -> dict:
    """Enhance everything and write the render manifest + contact sheet. Returns the manifest."""
    scanned = scan(ep, script)
    m = meta(ep)
    win = shot_windows(voice, script["shots"])
    enh = ep.path("media/enh")
    if enh.exists():
        shutil.rmtree(enh)
    enh.mkdir(parents=True)
    by_shot: dict[int, list[str]] = {}
    for b in script["beats"]:
        by_shot.setdefault(b["shot"], []).append(b["narration"])
    shots = []
    for s in script["shots"]:
        sid = s["id"]
        st = scanned["shots"][sid]
        items = []
        for f in st["files"]:
            f = Path(f)
            out = enh / (f.stem + (".png" if is_image(f) else ".mp4"))
            if is_image(f):
                info = enhance_image(f, out, s["visual_type"])
                items.append({"type": "image", "path": str(out.relative_to(ep.dir)), "info": info})
            else:
                info = enhance_video(cfg.ffmpeg, f, out)
                items.append({"type": "video", "path": str(out.relative_to(ep.dir)), "info": info})
        style = s["style"] if (items or s["style"] == "text") else "text"
        if items and style == "text":
            style = "full"
        card_text = s["stat_text"] or (by_shot.get(sid, [""])[0]).upper()
        a, b = win.get(sid, (0.0, 3.0))
        shots.append({
            "id": sid, "style": style, "visual_type": s["visual_type"], "items": items, "start": a, "end": b,
            "stat_text": s["stat_text"], "card_text": card_text, "year_stamp": s["year_stamp"], "location_tag": s["location_tag"],
            "callout": m["callouts"].get(str(sid)), "fallback": st["state"] == "skipped",
        })
    manifest = {"shots": shots}
    ep.write("media.json", manifest)
    build_contact_sheet(cfg, ep, manifest)
    return manifest


def build_contact_sheet(cfg: Config, ep: Episode, manifest: dict) -> Path:
    from .render import shot_preview
    from .visuals import font

    shots = manifest["shots"]
    cols = 5 if len(shots) > 8 else 4
    rows = -(-len(shots) // cols)
    tw, th, pad, lab = 216, 384, 10, 34
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + lab + pad) + pad), "#0E0E0F")
    d = ImageDraw.Draw(sheet)
    f = font("mono", 15)
    for i, s in enumerate(shots):
        x, y = pad + (i % cols) * (tw + pad), pad + (i // cols) * (th + lab + pad)
        thumb = ImageOps.fit(shot_preview(cfg, ep, s), (tw, th), Image.LANCZOS)
        sheet.paste(thumb, (x, y))
        tag = f"{s['id']} · {s['end'] - s['start']:.1f}s" + (" · TEXT" if not s["items"] else (f" · x{len(s['items'])}" if len(s["items"]) > 1 else ""))
        d.text((x, y + th + 6), tag, fill="#FFD60A", font=f)
    out = ep.path("media/contact.jpg")
    sheet.save(out, quality=88)
    return out
