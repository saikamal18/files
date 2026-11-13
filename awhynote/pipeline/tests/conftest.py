import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from awhynote.config import PROJECT, Config  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    tracker = tmp_path / "tracker.csv"
    shutil.copy(PROJECT / "03_performance_tracker.csv", tracker)
    c = Config.load(env_path=tmp_path / "none.env", overrides={
        "LLM_PROVIDER": "mock", "TTS_PROVIDER": "mock", "AWN_DATA_DIR": str(tmp_path / "data"), "AWN_TRACKER_CSV": str(tracker),
        "LLM_API_KEY": "", "LLM_MODEL": "", "AWN_MUSIC": "off",
    })
    c.fps = 8  # keep end-to-end renders quick
    return c


def synth_image(w, h, label="X", seed=1):
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), tuple(rnd.randint(60, 200) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(30):
        x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(w // 30, w // 8)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(rnd.randint(30, 230) for _ in range(3)))
    return img


def image_bytes(w, h, label="X", fmt="JPEG", seed=1):
    import io

    buf = io.BytesIO()
    synth_image(w, h, label, seed).save(buf, fmt)
    return buf.getvalue()


def make_clip(ffmpeg, path: Path, seconds=1.0, size="640x360"):
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30", "-t", str(seconds), "-pix_fmt", "yuv420p", str(path)], check=True)
    return path.read_bytes()
