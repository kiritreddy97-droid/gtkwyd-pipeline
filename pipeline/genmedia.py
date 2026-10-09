"""Free AI media generation: expressive speech and illustrations.

- Gemini TTS (free tier, same GEMINI_API_KEY): far more human, directable
  narration than the Edge voices, including Telugu.
- Illustrations: Gemini image models if the key has quota for them, else
  Pollinations (free, no key). Used to draw each scene instead of stock footage.

Every function raises PipelineError on failure so callers can fall back.
"""
from __future__ import annotations

import base64
import re
import time
import urllib.parse
from pathlib import Path

import requests

from .util import FFMPEG, PipelineError, run
from .writer_te import GEMINI_BASE, api_key

TTS_VOICES = ["Kore", "Charon", "Orus", "Fenrir", "Puck", "Aoede", "Leda", "Zephyr"]


def _models(kind: str) -> list[str]:
    """Models this key can call that look like `kind` ('tts' or 'image'), newest first."""
    r = requests.get(f"{GEMINI_BASE}/models", headers={"x-goog-api-key": api_key()},
                     params={"pageSize": 200}, timeout=30)
    r.raise_for_status()
    names = [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    hits = [n for n in names if kind in n and "embed" not in n]

    def ver(n: str):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        v = tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
        return tuple(-x for x in v + (0,) * (3 - len(v)))

    return sorted(hits, key=lambda n: (("preview" in n), ver(n), n))


def _post(model: str, body: dict, timeout: int = 180) -> dict:
    last = ""
    for attempt in range(1, 4):
        r = requests.post(f"{GEMINI_BASE}/models/{model}:generateContent",
                          headers={"x-goog-api-key": api_key(),
                                   "Content-Type": "application/json"},
                          json=body, timeout=timeout)
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"HTTP {r.status_code} {r.text[:160]}"
            time.sleep(6 * attempt)
            continue
        if not r.ok:
            raise PipelineError(f"{model}: HTTP {r.status_code} {r.text[:240]}")
        return r.json()
    raise PipelineError(f"{model}: {last}")


def _inline(data: dict, want: str) -> tuple[str, bytes]:
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data") and want in blob.get("mimeType", blob.get("mime_type", "")):
                return blob.get("mimeType", blob.get("mime_type", "")), base64.b64decode(blob["data"])
    raise PipelineError(f"no {want} in the response")


def gemini_tts(text: str, out_wav: Path, voice: str = "Kore", style: str = "",
               model: str | None = None) -> Path:
    """Narrate `text` with Gemini TTS. `style` is a plain-language direction
    (e.g. 'a warm grandmother telling a gripping true story, varied pace')."""
    model = model or (_models("tts") or [""])[0]
    if not model:
        raise PipelineError("this key lists no Gemini TTS model")
    prompt = (f"{style}\n\n{text}" if style else text)
    data = _post(model, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    })
    mime, pcm = _inline(data, "audio")
    rate = re.search(r"rate=(\d+)", mime)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    raw = out_wav.with_suffix(".pcm")
    raw.write_bytes(pcm)
    run([FFMPEG, "-y", "-f", "s16le", "-ar", rate.group(1) if rate else "24000", "-ac", "1",
         "-i", raw, out_wav])
    raw.unlink(missing_ok=True)
    return out_wav


def gemini_image(prompt: str, out_png: Path, model: str | None = None) -> Path:
    models = [model] if model else _models("image")
    if not models:
        raise PipelineError("this key lists no Gemini image model")
    last = ""
    for m in models[:3]:
        try:
            data = _post(m, {
                "contents": [{"parts": [{"text": prompt + " Wide 16:9 landscape composition."}]}],
                "generationConfig": {"responseModalities": ["IMAGE"]},
            })
            _, img = _inline(data, "image")
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(img)
            return out_png
        except PipelineError as e:
            last = str(e)
    raise PipelineError(last)


def pollinations_image(prompt: str, out_jpg: Path, w: int = 1920, h: int = 1080,
                       seed: int = 1) -> Path:
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
           + f"?width={w}&height={h}&model=flux&nologo=true&seed={seed}")
    last = ""
    for attempt in range(1, 4):
        try:
            r = requests.get(url, timeout=120)
        except requests.RequestException as e:
            last = str(e)
            time.sleep(5 * attempt)
            continue
        if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) > 20_000:
            out_jpg.parent.mkdir(parents=True, exist_ok=True)
            out_jpg.write_bytes(r.content)
            return out_jpg
        last = f"HTTP {r.status_code} {r.headers.get('content-type', '')} {len(r.content)}B"
        time.sleep(5 * attempt)
    raise PipelineError(f"pollinations: {last}")


def check(out_dir: Path) -> list[str]:
    """Diagnostic for the workflow: try each free generator once, save samples."""
    out_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    te = ("విజయనగర సామ్రాజ్యం ఒకప్పుడు ప్రపంచంలోనే అత్యంత సంపన్నమైనది. కానీ ఒక్క రోజులో "
          "అంతా మారిపోయింది. ఆ రోజు ఏం జరిగిందో తెలుసా?")
    style = ("Narrate in natural, warm, conversational Telugu like a gifted storyteller "
             "speaking to family: vary the pace, slow down and lower the voice at the dramatic "
             "moment, rise on the question, take small human pauses. Not a flat reading.")
    try:
        tts_models = _models("tts")
        lines.append(f"TTS models listed: {tts_models[:4]}")
        for v in ("Kore", "Charon"):
            try:
                p = gemini_tts(te, out_dir / f"gemini_tts_{v}.wav", v, style)
                lines.append(f"Gemini TTS {v}: OK ({p.stat().st_size // 1024} KB)")
            except PipelineError as e:
                lines.append(f"Gemini TTS {v}: FAILED {e}")
                break
    except Exception as e:  # noqa: BLE001
        lines.append(f"Gemini TTS: FAILED {e}")

    ip = ("Cinematic painterly historical illustration, 16th-century South India, a great "
          "stone city with temples and bustling bazaar at golden hour, warm light, rich detail, "
          "no text, no modern objects")
    try:
        lines.append(f"image models listed: {_models('image')[:4]}")
        p = gemini_image(ip, out_dir / "gemini_image.png")
        lines.append(f"Gemini image: OK ({p.stat().st_size // 1024} KB)")
    except Exception as e:  # noqa: BLE001
        lines.append(f"Gemini image: FAILED {e}")
    try:
        p = pollinations_image(ip, out_dir / "pollinations.jpg")
        lines.append(f"Pollinations: OK ({p.stat().st_size // 1024} KB)")
    except Exception as e:  # noqa: BLE001
        lines.append(f"Pollinations: FAILED {e}")
    return lines
