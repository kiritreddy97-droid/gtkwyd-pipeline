"""Narration synthesis: Microsoft Edge neural voices (default, natural-sounding,
free, with word timings) or the offline Piper binary (fallback)."""
from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

from .util import FFMPEG, PIPER_EXE, ROOT, PipelineError, run

WordTiming = tuple[float, float, str]  # (start_s, end_s, word)


def synthesize_edge(text: str, out_wav: Path, voice: str, rate_pct: int = 0,
                    pitch_hz: int = 0, retries: int = 4) -> list[WordTiming]:
    """Speak `text` with an Edge neural voice into out_wav (24 kHz mono) and
    return per-word timings measured against that file. Raises PipelineError
    after repeated failures so callers can fall back to Piper."""
    try:
        import edge_tts
    except ImportError as e:
        raise PipelineError("edge-tts not installed (pip install edge-tts)") from e

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    mp3 = out_wav.with_suffix(".mp3")
    clean = " ".join(text.split())
    last: Exception | None = None

    async def _go() -> list[WordTiming]:
        comm = edge_tts.Communicate(clean, voice, rate=f"{rate_pct:+d}%",
                                    pitch=f"{pitch_hz:+d}Hz", boundary="WordBoundary")
        words: list[WordTiming] = []
        with mp3.open("wb") as fh:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append((start, start + chunk["duration"] / 1e7, chunk["text"]))
        return words

    for attempt in range(1, retries + 1):
        try:
            words = asyncio.run(_go())
            if not mp3.exists() or mp3.stat().st_size < 1000:
                raise PipelineError("edge-tts returned no audio")
            run([FFMPEG, "-y", "-i", mp3, "-ar", "24000", "-ac", "1", out_wav])
            mp3.unlink(missing_ok=True)
            return words
        except Exception as e:  # noqa: BLE001 - network/service hiccup
            last = e
            time.sleep(2 * attempt)
    raise PipelineError(f"edge-tts failed after {retries} tries: {last}")

def approx_word_timings(wav: Path, text: str) -> list[WordTiming]:
    """Estimate per-word timings for audio that came without them (Gemini TTS).
    Sentence boundaries are anchored on the longest silences in the audio; inside
    a sentence, words share the time in proportion to their length."""
    import re

    from .util import ffprobe_duration

    dur = ffprobe_duration(wav)
    words = text.split()
    if not words:
        return []
    proc = run([FFMPEG, "-i", wav, "-af", "silencedetect=noise=-38dB:d=0.18",
                "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    sil = list(zip(starts, ends + [dur] * (len(starts) - len(ends))))

    lo, hi = 0.0, dur
    if sil and sil[0][0] <= 0.05:
        lo = sil[0][1]
        sil = sil[1:]
    if sil and sil[-1][1] >= dur - 0.1:
        hi = sil[-1][0]
        sil = sil[:-1]
    hi = max(hi, lo + 0.5)

    sentences = [s for s in re.split(r"(?<=[.?!।])\s+", text.strip()) if s]
    n = len(sentences)
    inner = [p for p in sil if lo + 0.1 < p[0] and p[1] < hi - 0.1]
    if n > 1 and len(inner) >= n - 1:
        cuts = sorted(sorted(inner, key=lambda p: p[1] - p[0], reverse=True)[:n - 1])
    else:
        cuts = []
    # speech segments between the chosen pauses
    bounds = [lo] + [x for p in cuts for x in p] + [hi]
    segs = list(zip(bounds[0::2], bounds[1::2])) if cuts else [(lo, hi)]
    if cuts and len(segs) != n:
        segs, sentences = [(lo, hi)], [text.strip()]

    out: list[WordTiming] = []
    for (a, b), sent in zip(segs, sentences):
        ws = sent.split()
        weights = [max(1, len(re.sub(r"\W", "", w))) for w in ws]
        total = float(sum(weights))
        t = a
        for w, wt in zip(ws, weights):
            d = (b - a) * wt / total
            out.append((t, t + d, w))
            t += d
    return out


def synthesize_gemini(text: str, out_wav: Path, voice: str, style: str,
                      retries: int = 4) -> list[WordTiming]:
    """Narrate with Gemini TTS (expressive, directable). Returns estimated word
    timings. Raises PipelineError after repeated failures."""
    from . import genmedia

    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            genmedia.gemini_tts(text, out_wav, voice, style)
            time.sleep(1.5)  # stay polite with the free tier's request rate
            return approx_word_timings(out_wav, text)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(10 * attempt)
    raise PipelineError(f"gemini tts failed after {retries} tries: {last}")


_ESPEAK_DATA = ROOT / "piper" / "espeak-ng-data"


def synthesize(text: str, out_wav: Path, onnx_path: Path,
               length_scale: float = 1.0, sentence_silence: float = 0.35,
               noise_scale: float | None = None, noise_w: float | None = None) -> Path:
    if not PIPER_EXE.exists():
        raise PipelineError(
            f"Piper binary missing at {PIPER_EXE}. Re-run ./setup.ps1"
        )
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(PIPER_EXE),
        "-m", str(onnx_path),
        "-f", str(out_wav),
        "--length_scale", str(length_scale),
        "--sentence_silence", str(sentence_silence),
        "-q",
    ]
    # noise_scale/noise_w control the model's own prosody variation (pitch,
    # rhythm). Small per-video jitter here is what keeps one voice from
    # sounding like the exact same recording every time.
    if noise_scale is not None:
        cmd += ["--noise_scale", str(round(noise_scale, 4))]
    if noise_w is not None:
        cmd += ["--noise_w", str(round(noise_w, 4))]
    if _ESPEAK_DATA.exists():
        cmd += ["--espeak_data", str(_ESPEAK_DATA)]

    # Piper reads one utterance per stdin line; keep the narration on a single line.
    payload = " ".join(text.split()) + "\n"
    proc = subprocess.run(
        cmd, input=payload, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not out_wav.exists():
        tail = "\n".join((proc.stderr or "").splitlines()[-15:])
        raise PipelineError(f"piper failed:\n{tail}")
    return out_wav
