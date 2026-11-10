"""Light, classical enhancement of the creator's own media. No generative AI; it never invents pixels
unless the optional Real-ESRGAN binary is installed and the source is small."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .util import media_info, run_ffmpeg

TARGET_LONG_EDGE = 2400  # enough for 1080x1920 cover-crop plus a 12% Ken Burns zoom
MAX_UPSCALE = 3.0


def open_image(path: Path) -> Image.Image:
    if path.suffix.lower() in (".heic", ".heif"):
        sips = shutil.which("sips")  # macOS built-in
        if not sips:
            raise RuntimeError(f"{path.name}: HEIC needs macOS 'sips' or a JPG/PNG export")
        tmp = Path(tempfile.mkdtemp()) / (path.stem + ".jpg")
        subprocess.run([sips, "-s", "format", "jpeg", str(path), "--out", str(tmp)], check=True, capture_output=True)
        path = tmp
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def _realesrgan(img: Image.Image) -> Image.Image | None:
    exe = shutil.which("realesrgan-ncnn-vulkan")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.png", Path(td) / "out.png"
        img.save(src)
        try:
            subprocess.run([exe, "-i", str(src), "-o", str(dst), "-s", "2"], check=True, capture_output=True, timeout=300)
            return Image.open(dst).convert("RGB")
        except (subprocess.SubprocessError, OSError):
            return None


def enhance_image(src: Path, dst: Path, visual_type: str = "") -> dict:
    img = open_image(src)
    w0, h0 = img.size
    note = []
    long_edge = max(img.size)
    if long_edge < 1400:
        up = _realesrgan(img)
        if up is not None:
            img = up
            note.append("Real-ESRGAN x2")
    long_edge = max(img.size)
    if long_edge < TARGET_LONG_EDGE:
        scale = min(MAX_UPSCALE, TARGET_LONG_EDGE / long_edge)
        if scale > 1.05:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
            note.append(f"upscaled x{scale:.2f}")
    elif long_edge > 4000:
        s = 4000 / long_edge
        img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=0.5)
    if visual_type == "archival":
        gray = ImageOps.grayscale(img)
        sepia = ImageOps.colorize(gray, black="#1a1209", white="#f1e4c8", mid="#8a6a43").convert("RGB")
        img = Image.blend(img, sepia, 0.65)
        note.append("archival tone")
    else:
        img = ImageEnhance.Color(img).enhance(1.06)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.6, percent=60, threshold=3))
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "PNG")
    return {"src_size": [w0, h0], "out_size": list(img.size), "ops": ["autocontrast", "unsharp"] + note}


def enhance_video(ffmpeg: str, src: Path, dst: Path, max_seconds: float = 20.0) -> dict:
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = ("scale='if(gt(iw,ih),min(iw,1920),-2)':'if(gt(iw,ih),-2,min(ih,1920))',"
          "hqdn3d=1.5:1.5:4:4,unsharp=5:5:0.5:5:5:0.0,eq=contrast=1.05:saturation=1.05,fps=30,format=yuv420p")
    run_ffmpeg(ffmpeg, ["-i", str(src), "-t", str(max_seconds), "-an", "-vf", vf, "-c:v", "libx264", "-crf", "18", "-preset", "medium", str(dst)])
    info = media_info(ffmpeg, dst)
    return {"duration": info["duration"], "out_size": [info["width"], info["height"]], "ops": ["denoise", "sharpen", "contrast"]}
