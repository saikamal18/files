"""Stage 5/6: render the 1080x1920 video in one pass (frames composed in Pillow, encoded by one ffmpeg) and the cover."""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

from . import music, sfx
from . import transitions as TR
from . import visuals as V
from .captions import CaptionTrack
from .config import Config
from .enhance import open_image
from .store import Episode
from .styles import get_style
from .util import ensure_fonts, run_ffmpeg

MAX_STILL_S = 3.4
TAIL_S = 0.6
SOURCE_SHOW_S = 3.2
SERIES_TINT = {"Money": (107, 74, 43), "History": (140, 105, 60), "Tech": (60, 90, 120), "Myths": (120, 50, 40)}


class _Seg:
    def __init__(self, start: float, end: float, overlays: list, build, meta: dict | None = None):
        self.start, self.end, self.overlays = start, end, overlays
        self.meta = meta or {}
        self._build = build
        self._obj = None

    def frame(self, t: float) -> Image.Image:
        if self._obj is None:
            self._obj = self._build(self.end - self.start)
        return self._obj.frame(t - self.start)

    def close(self) -> None:
        if self._obj is not None and hasattr(self._obj, "close"):
            self._obj.close()
        self._obj = None


class _Still:
    def __init__(self, kb: V.KenBurns):
        self.kb = kb

    def frame(self, t: float) -> Image.Image:
        return self.kb.frame(t)


class _Clip:
    """Streams a video clip as raw frames, holding the last frame if the clip is shorter than the slot."""

    def __init__(self, ffmpeg: str, path: Path, size: tuple[int, int], duration: float, fps: int):
        w, h = size
        if w / max(1, h) > 1.25:
            vf = (f"split[a][b];[a]scale={V.W}:{V.H}:force_original_aspect_ratio=increase,crop={V.W}:{V.H},boxblur=30:5,eq=brightness=-0.12[bg];"
                  f"[b]scale={V.W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={fps},format=rgb24")
        else:
            vf = f"scale={V.W}:{V.H}:force_original_aspect_ratio=increase,crop={V.W}:{V.H},fps={fps},format=rgb24"
        self.proc = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-t", f"{duration:.3f}", "-vf", vf,
                                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.n = V.W * V.H * 3
        self.last: Image.Image | None = None
        self.eof = False

    def frame(self, t: float) -> Image.Image:
        if not self.eof:
            buf = b""
            while len(buf) < self.n:
                chunk = self.proc.stdout.read(self.n - len(buf))
                if not chunk:
                    break
                buf += chunk
            if len(buf) == self.n:
                self.last = Image.frombytes("RGB", (V.W, V.H), buf)
            else:
                self.eof = True
        if self.last is None:
            self.last = Image.new("RGB", (V.W, V.H), V.INK)
        return self.last

    def close(self) -> None:
        try:
            self.proc.stdout.close()
            self.proc.kill()
        except Exception:
            pass


def _split(duration: float) -> int:
    return max(1, math.ceil(duration / MAX_STILL_S - 1e-6))


def build_segments(cfg: Config, ep: Episode, manifest: dict, total: float, script: dict) -> list[_Seg]:
    segs: list[_Seg] = []
    preset_i = 0
    canvases: dict[str, tuple] = {}
    shots = manifest["shots"]
    for si, shot in enumerate(shots):
        start = 0.0 if si == 0 else shot["start"]
        end = total if si == len(shots) - 1 else shots[si + 1]["start"]
        overlays = []
        if shot.get("location_tag"):
            overlays.append(V.location_chip(shot["location_tag"]))
        if shot.get("year_stamp"):
            overlays.append(V.year_stamp(shot["year_stamp"]))
        items = shot["items"]
        if not items:
            parts = [("text", None)] * _split(end - start)
        else:
            parts = []
            per = (end - start) / len(items)
            for it in items:
                n = 1 if it["type"] == "video" else _split(per)
                parts += [(it["type"], it)] * n
        dur = (end - start) / len(parts)
        for pi, (kind, it) in enumerate(parts):
            s0, s1 = start + pi * dur, start + (pi + 1) * dur
            if kind == "video":
                def build(d, it=it):
                    size = tuple(it["info"].get("out_size") or (V.W, V.H))
                    return _Clip(cfg.ffmpeg, ep.dir / it["path"], size, d + 0.2, cfg.fps)
            else:
                if shot.get("callout") and pi == 0:
                    preset = "focus"
                else:
                    cycle = V.KenBurns.GENTLE if (shot["style"] in ("card", "text") or kind == "text") else [p for p in V.KenBurns.PRESETS if p != "focus"]
                    preset = cycle[preset_i % len(cycle)]
                preset_i += 1

                def build(d, shot=shot, it=it, kind=kind, preset=preset):
                    key = f"{shot['id']}:{it['path'] if it else 'text'}"
                    if key not in canvases:
                        canvases.clear()
                        if kind == "text":
                            canvases[key] = V.prepare_text(shot["card_text"])
                        else:
                            img = open_image(ep.dir / it["path"])
                            fn = V.prepare_card if shot["style"] == "card" else V.prepare_full
                            canvases[key] = fn(img, shot.get("callout"))
                    canvas, focus = canvases[key]
                    return _Still(V.KenBurns(canvas, preset, d, focus))
            segs.append(_Seg(s0, s1, overlays, build, {"shot": shot["id"], "style": shot["style"], "vtype": shot.get("visual_type", "")}))
    return segs


def plan_transitions(cfg: Config, segs: list[_Seg]) -> list[tuple[str, float] | None]:
    """Per cut (between segs[i] and segs[i+1]): (kind, half-length in s) or None for a hard cut."""
    out: list[tuple[str, float] | None] = []
    for i in range(len(segs) - 1):
        a, b = segs[i], segs[i + 1]
        same = a.meta.get("shot") == b.meta.get("shot")
        kind = TR.choose(cfg.transition, b.meta, same, i, last=i == len(segs) - 2)
        if not kind:
            out.append(None)
            continue
        half = min(TR.length(kind, same) / 2, 0.4 * (a.end - a.start), 0.4 * (b.end - b.start))
        out.append((kind, half) if half >= 0.05 else None)
    return out


def _audio_filter(cfg: Config, total: float, sfx_path: Path | None, music_path: Path | None) -> tuple[list[str], str]:
    """Voice (+ soft sound effects) (+ music that ducks under the voice), then loudness-normalised."""
    fmt = "aformat=sample_rates=44100:channel_layouts=stereo"
    extra: list[str] = []
    parts = [f"[1:a]aresample=44100,apad=whole_dur={total:.3f},{fmt}" + ("[vx];[vx]asplit=2[v][vsc]" if music_path else "[v]")]
    labels, idx = ["[v]"], 2
    if sfx_path:
        extra += ["-i", str(sfx_path)]
        parts.append(f"[{idx}:a]aresample=44100,{fmt},volume={cfg.sfx_db}dB,apad=whole_dur={total:.3f}[s]")
        labels.append("[s]")
        idx += 1
    if music_path:
        extra += ["-stream_loop", "-1", "-i", str(music_path)]
        parts.append(f"[{idx}:a]aresample=44100,{fmt},volume={cfg.music_db}dB,afade=t=in:d=0.8,afade=t=out:st={max(0, total - 1.4):.2f}:d=1.4[m0]")
        parts.append("[m0][vsc]sidechaincompress=threshold=0.03:ratio=9:attack=25:release=450:makeup=1[m]")
        labels.append("[m]")
    n = len(labels)
    if n == 1:
        parts.append("[v]loudnorm=I=-14:TP=-1:LRA=11[a]")
    else:
        parts.append(f"{''.join(labels)}amix=inputs={n}:duration=first:dropout_transition=0,volume={n},loudnorm=I=-14:TP=-1:LRA=11[a]")
    return extra, ";".join(parts)


TELEGRAM_TARGET_MB = 46


def _fit_for_telegram(cfg: Config, ep: Episode, out: Path, total: float) -> None:
    """Telegram bots can send at most 50 MB. If the render is bigger, keep it as final_master.mp4 and re-encode a lighter final.mp4."""
    if out.stat().st_size <= TELEGRAM_TARGET_MB * 1e6:
        return
    master = ep.path("final_master.mp4")
    out.replace(master)
    kbps = max(1500, int(TELEGRAM_TARGET_MB * 8000 / total) - 170)
    run_ffmpeg(cfg.ffmpeg, ["-i", str(master), "-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.25)}k",
                            "-bufsize", f"{kbps * 2}k", "-profile:v", "high", "-c:a", "copy", "-movflags", "+faststart", str(out)])


def render_video(cfg: Config, ep: Episode, progress=None, series_tag: str = "") -> dict:
    ensure_fonts(download=True)
    V.use_style(get_style(cfg.caption_style))
    voice, script, manifest = ep.read("voice.json"), ep.read("script.json"), ep.read("media.json")
    total = voice["duration"] + TAIL_S
    fps = cfg.fps
    nframes = math.ceil(total * fps)
    segs = build_segments(cfg, ep, manifest, total, script)
    track = CaptionTrack(voice, cfg.caption_style)
    tags = V.static_tags(series_tag or script.get("series_tag", ""), cfg.handle)
    source = V.source_pill(script["sources_line"]) if script.get("sources_line") else None
    vig, grains = V.vignette(), V.grain_frames()
    out = ep.path("final.mp4")
    part = ep.path("final.partial.mp4")
    bounds = plan_transitions(cfg, segs)
    sfx_path = sfx.build_track([(segs[i].end, TR.SOUND_KINDS[b[0]]) for i, b in enumerate(bounds) if b and b[0] in TR.SOUND_KINDS], total, ep.path("sfx.wav"))
    music_path = music.pick(cfg, ep.id)
    extra_in, afilter = _audio_filter(cfg, total, sfx_path, music_path)
    cmd = [cfg.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{V.W}x{V.H}", "-r", str(fps), "-i", "-",
           "-i", str(ep.path("voice.wav")), *extra_in,
           "-filter_complex", f"[0:v]scale=out_color_matrix=bt709:out_range=tv,format=yuv420p[vout];{afilter}",
           "-map", "[vout]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", "-f", "mp4", str(part)]
    log = open(ep.path("ffmpeg_render.log"), "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
    ptr, closed, last_pct = 0, 0, -1

    def compose(t: float) -> tuple[Image.Image, list]:
        cur = segs[ptr]
        after = bounds[ptr] if ptr < len(bounds) else None
        before = bounds[ptr - 1] if ptr > 0 else None
        if after and t >= cur.end - after[1]:
            kind, h = after
            a, b, p = cur, segs[ptr + 1], (t - (cur.end - h)) / (2 * h)
        elif before and t < cur.start + before[1]:
            kind, h = before
            a, b, p = segs[ptr - 1], cur, (t - (cur.start - h)) / (2 * h)
        else:
            return cur.frame(t), cur.overlays
        return TR.blend(kind, a.frame(t), b.frame(t), p), (a.overlays if p < 0.5 else b.overlays)

    try:
        for i in range(nframes):
            t = i / fps
            while ptr < len(segs) - 1 and t >= segs[ptr].end:
                ptr += 1
            while closed < ptr and t >= segs[closed].end + (bounds[closed][1] if bounds[closed] else 0):
                segs[closed].close()
                closed += 1
            raw, overlays = compose(t)
            frame = V.apply_grade(raw, vig, grains[(i // 2) % len(grains)])
            for img, pos in overlays:
                frame.paste(img, pos, img)
            for img, pos in tags:
                frame.paste(img, pos, img)
            cap = track.overlay(t)
            if cap:
                frame.paste(cap[0], cap[1], cap[0])
            if source and t >= total - TAIL_S - SOURCE_SHOW_S:
                frame.paste(source[0], source[1], source[0])
            proc.stdin.write(frame.tobytes())
            pct = int(i * 100 / nframes)
            if progress and pct // 25 != last_pct // 25 and pct > 0:
                progress(pct)
            last_pct = pct
        proc.stdin.close()
        rc = proc.wait()
    except BrokenPipeError:
        rc = proc.wait()
    finally:
        for s_ in segs:
            s_.close()
        log.close()
    if rc != 0:
        raise RuntimeError("ffmpeg render failed: " + ep.path("ffmpeg_render.log").read_text(errors="replace")[-800:])
    part.replace(out)
    _fit_for_telegram(cfg, ep, out, total)
    run_ffmpeg(cfg.ffmpeg, ["-i", str(out), "-vf", "scale=540:960", "-c:v", "libx264", "-preset", "veryfast", "-crf", "29",
                            "-c:a", "aac", "-b:a", "80k", "-movflags", "+faststart", str(ep.path("preview.mp4"))])
    info = {"duration": round(total, 2), "frames": nframes, "size_mb": round(out.stat().st_size / 1e6, 1)}
    ep.write("render.json", info)
    return info


def style_samples(cfg: Config, out_dir: Path) -> list[Path]:
    """One sample frame per caption style plus a side-by-side sheet, so the look can be chosen by eye."""
    from .captions import CaptionTrack
    from .styles import STYLES

    ensure_fonts(download=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    words = "The ₹10 note hides a temple".split()
    voice = {"duration": 3.0, "beats": [{"id": 1, "shot": 1, "text": " ".join(words), "start": 0.0, "end": 3.0, "words": [
        {"w": w, "start": i * 0.4, "end": i * 0.4 + 0.35, "hl": w == "temple"} for i, w in enumerate(words)]}]}
    vig, grain = V.vignette(), V.grain_frames(1)[0]
    paths, thumbs = [], []
    for name, style in STYLES.items():
        V.use_style(style)
        canvas, focus = V.prepare_text("₹10 · 1938")
        frame = V.apply_grade(V.KenBurns(canvas, "push", 1.0, focus).frame(0.0), vig, grain)
        for img, pos in V.static_tags("NOTE 001", cfg.handle):
            frame.paste(img, pos, img)
        track = CaptionTrack(voice, style)
        for ci, dy in zip(range(min(2, len(track.chunks))), (-110, 110)):
            im, (x, y) = track._render(ci, len(track.chunks[ci].words))
            frame.paste(im, (x, y + dy), im)
        path = out_dir / f"style_{name}.jpg"
        frame.save(path, quality=90)
        paths.append(path)
        thumbs.append((name, frame.resize((405, 720), Image.LANCZOS)))
    V.use_style(get_style(cfg.caption_style))
    sheet = Image.new("RGB", (len(thumbs) * 415 + 10, 770), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    for i, (name, th) in enumerate(thumbs):
        sheet.paste(th, (10 + i * 415, 40))
        d.text((10 + i * 415, 10), name, font=V.font("mono", 24), fill=V.YELLOW)
    sheet_path = out_dir / "styles_sheet.jpg"
    sheet.save(sheet_path, quality=88)
    return [sheet_path, *paths]


def shot_preview(cfg: Config, ep: Episode, shot: dict) -> Image.Image:
    """First frame of a shot exactly as the renderer frames it (no captions/overlays)."""
    items = shot["items"]
    if not items:
        canvas, focus = V.prepare_text(shot["card_text"])
    elif items[0]["type"] == "video":
        it = items[0]
        clip = _Clip(cfg.ffmpeg, ep.dir / it["path"], tuple(it["info"].get("out_size") or (V.W, V.H)), 0.5, cfg.fps)
        try:
            return clip.frame(0.0).copy()
        finally:
            clip.close()
    else:
        img = open_image(ep.dir / items[0]["path"])
        canvas, focus = (V.prepare_card if shot["style"] == "card" else V.prepare_full)(img, shot.get("callout"))
    return V.KenBurns(canvas, "push", 1.0, focus).frame(0.0)


# -- cover -------------------------------------------------------------------
def make_cover(cfg: Config, ep: Episode, title: str, highlight: str, series_tag: str) -> Path:
    ensure_fonts(download=True)
    V.use_style(get_style(cfg.caption_style))
    manifest = ep.read("media.json")
    base = shot_preview(cfg, ep, manifest["shots"][0])
    base = ImageEnhance.Brightness(base).enhance(0.45)
    tint = SERIES_TINT.get(ep.topic.get("pillar", ""), (60, 60, 60))
    base = Image.blend(base, Image.new("RGB", base.size, tint), 0.25)
    d = ImageDraw.Draw(base)
    words = title.upper().split()
    hl = highlight.upper().strip(".,?!:") if highlight else ""
    for size in (150, 134, 120, 106, 92):
        fnt = V.font("head", size)
        lines = V.wrap(" ".join(words), fnt, 880)
        if len(lines) <= 4:
            break
    lh = int(size * 1.3)
    y = 960 - lh * len(lines) / 2
    asc, desc = fnt.getmetrics()
    for line in lines:
        x = (V.W - fnt.getlength(line)) / 2
        for tok in line.split(" "):
            tw = fnt.getlength(tok)
            if hl and tok.strip(".,?!:") == hl:
                d.rounded_rectangle([x - 16, y - 4, x + tw + 16, y + asc + desc + 4], radius=18, fill=V.YELLOW)
                d.text((x, y), tok, font=fnt, fill=V.INK)
            else:
                d.text((x, y), tok, font=fnt, fill=V.PAPER, stroke_width=6, stroke_fill=(0, 0, 0))
            x += fnt.getlength(tok + " ")
        y += lh
    mono = V.font("mono", 40)
    tag = f"{series_tag}".upper()
    d.text(((V.W - mono.getlength(tag)) / 2, 700 - lh), tag, font=mono, fill=V.YELLOW)
    wm = "AWHYNOTE"
    d.text(((V.W - mono.getlength(wm)) / 2, 1560), wm, font=mono, fill=V.PAPER)
    out = ep.path("cover.png")
    base.save(out)
    base.convert("RGB").save(ep.path("cover.jpg"), quality=92)
    return out
