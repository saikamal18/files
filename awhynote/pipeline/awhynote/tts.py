"""Stage 3: voice-over. Per-beat synthesis, word timings, 44.1 kHz mono wav assembled from sample counts."""
from __future__ import annotations

import array
import asyncio
import base64
import math
import re
import tempfile
import wave
from pathlib import Path

import requests

from .config import Config
from .script import clean_word, words
from .store import Episode
from .util import read_json, run_ffmpeg

SR = 44100
GAP_S = 0.10
LEAD_KEEP_S = 0.03
TAIL_KEEP_S = 0.07
SILENCE_THRESH = 400  # int16 amplitude


def normalise_speech(text: str, extra: dict[str, str] | None = None) -> str:
    text = re.sub(r"₹\s?([\d][\d,\.]*)", r"\1 rupees", text)
    text = re.sub(r"\bRs\.?\s?([\d][\d,\.]*)", r"\1 rupees", text)
    text = text.replace("₹", "rupees ")
    for k, v in (extra or {}).items():
        text = re.sub(rf"\b{re.escape(k)}\b", v, text)
    return re.sub(r"\s+", " ", text).strip()


# -- wav helpers -------------------------------------------------------------
def read_wav(path: Path) -> array.array:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2, "unexpected wav format"
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    return a


def write_wav(path: Path, samples: array.array) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())


def trim_silence(samples: array.array) -> tuple[array.array, float]:
    """Trim leading/trailing near-silence; returns (samples, seconds trimmed from the start)."""
    n = len(samples)
    i = 0
    while i < n and abs(samples[i]) < SILENCE_THRESH:
        i += 1
    j = n - 1
    while j > i and abs(samples[j]) < SILENCE_THRESH:
        j -= 1
    if i >= j:
        return samples, 0.0
    start = max(0, i - int(LEAD_KEEP_S * SR))
    end = min(n, j + int(TAIL_KEEP_S * SR))
    return samples[start:end], start / SR


def silence(seconds: float) -> array.array:
    return array.array("h", [0] * int(seconds * SR))


# -- providers ---------------------------------------------------------------
def _edge_beat(text: str, voice: str, rate: str, out_mp3: Path) -> list[dict]:
    import edge_tts

    async def go() -> list[dict]:
        comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
        evs: list[dict] = []
        audio = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                evs.append({"w": chunk["text"], "start": chunk["offset"] / 1e7, "end": (chunk["offset"] + chunk["duration"]) / 1e7})
        if not audio:
            raise RuntimeError("edge-tts returned no audio")
        out_mp3.write_bytes(bytes(audio))
        return evs

    last: Exception | None = None
    for _ in range(3):
        try:
            return asyncio.run(go())
        except Exception as e:  # network hiccups are common; retry
            last = e
    raise RuntimeError(f"edge-tts failed: {last}")


def _eleven_beat(text: str, cfg: Config, out_mp3: Path) -> list[dict]:
    if not cfg.eleven_key or not cfg.eleven_voice:
        raise RuntimeError("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID are required")
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{cfg.eleven_voice}/with-timestamps",
        headers={"xi-api-key": cfg.eleven_key},
        params={"output_format": "mp3_44100_128"},
        json={"text": text, "model_id": "eleven_multilingual_v2"},
        timeout=120,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"ElevenLabs HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    out_mp3.write_bytes(base64.b64decode(data["audio_base64"]))
    al = data["alignment"]
    evs, cur, st, en = [], "", None, 0.0
    for ch, s, e in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
        if ch.isspace():
            if cur:
                evs.append({"w": cur, "start": st, "end": en})
            cur, st = "", None
        else:
            if st is None:
                st = s
            cur += ch
            en = e
    if cur:
        evs.append({"w": cur, "start": st, "end": en})
    return evs


def _mock_beat(text: str, out_wav: Path) -> list[dict]:
    toks = text.split()
    evs, t = [], 0.05
    samples = array.array("h")
    for w in toks:
        d = 0.12 + 0.045 * len(w)
        evs.append({"w": w, "start": t, "end": t + d})
        n = int(d * SR)
        samples.extend(array.array("h", (int(3000 * math.sin(2 * math.pi * 220 * i / SR)) for i in range(n))))
        t += d + 0.03
        samples.extend(silence(0.03))
    write_wav(out_wav, array.array("h", silence(0.05)) + samples)
    return evs


# -- timing map --------------------------------------------------------------
def map_words(display: list[str], spoken: list[dict], offset: float) -> list[dict]:
    """Attach times to display tokens. 1:1 when counts match, else proportional to character count."""
    if not spoken:
        return []
    if len(display) == len(spoken):
        return [{"w": d, "start": s["start"] - offset, "end": s["end"] - offset} for d, s in zip(display, spoken)]
    t0, t1 = spoken[0]["start"] - offset, spoken[-1]["end"] - offset
    total = sum(max(1, len(d)) for d in display)
    out, t = [], t0
    for d in display:
        dur = (t1 - t0) * max(1, len(d)) / total
        out.append({"w": d, "start": t, "end": t + dur})
        t += dur
    return out


def synthesize(cfg: Config, ep: Episode, script: dict) -> dict:
    ov = ep.state.get("voice_overrides", {})
    voice = ov.get("voice") or cfg.edge_voice
    rate = ov.get("rate") or cfg.edge_rate
    extra = read_json(cfg.data_dir / "pronounce.json", {}) or {}
    provider = cfg.tts_provider
    ff = cfg.ffmpeg
    all_samples = array.array("h")
    beats_out: list[dict] = []
    t = 0.0
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        for b in script["beats"]:
            spoken_text = normalise_speech(b["narration"], extra)
            wav = tdp / f"b{b['id']}.wav"
            if provider == "mock":
                evs = _mock_beat(spoken_text, wav)
            else:
                mp3 = tdp / f"b{b['id']}.mp3"
                evs = _edge_beat(spoken_text, voice, rate, mp3) if provider == "edge" else _eleven_beat(spoken_text, cfg, mp3)
                run_ffmpeg(ff, ["-i", str(mp3), "-ar", str(SR), "-ac", "1", "-sample_fmt", "s16", str(wav)])
            samples, trimmed = trim_silence(read_wav(wav))
            disp = b["narration"].split()
            wmap = map_words(disp, evs, trimmed)
            hl = {clean_word(h) for h in b.get("highlight", [])}
            for w in wmap:
                w["start"] = round(max(0.0, w["start"]) + t, 3)
                w["end"] = round(max(w["start"] + 0.05, w["end"] + t), 3)
                w["hl"] = clean_word(w["w"]) in hl
            dur = len(samples) / SR
            beats_out.append({"id": b["id"], "shot": b["shot"], "text": b["narration"], "start": round(t, 3), "end": round(t + dur, 3), "words": wmap})
            all_samples.extend(samples)
            gap = GAP_S + float(b.get("pause_after_s", 0) or 0)
            all_samples.extend(silence(gap))
            t += dur + gap
    total = len(all_samples) / SR
    write_wav(ep.path("voice.wav"), all_samples)
    run_ffmpeg(ff, ["-i", str(ep.path("voice.wav")), "-c:a", "aac", "-b:a", "96k", str(ep.path("voice.m4a"))])
    meta = {"provider": provider, "voice": voice if provider == "edge" else cfg.eleven_voice, "rate": rate, "duration": round(total, 2), "beats": beats_out}
    ep.write("voice.json", meta)
    return meta


def summary_text(meta: dict) -> str:
    d = meta["duration"]
    flag = "✅" if d <= 55 else ("⚠️ a bit long — consider tightening" if d <= 60 else "⛔ over a minute — use Edit to shorten")
    return (f"🎙 Voice-over ready · {d:.1f}s {flag}\nVoice: {meta['voice']}  ·  rate {meta['rate']}\n\n"
            "Listen to the audio, then:\n• ✅ Approve\n• ✏️ Edit: `voice en-IN-NeerjaNeural`, `rate +8%`, or script changes\n• 🔄 Redo")
