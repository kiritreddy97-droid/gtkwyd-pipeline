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


def declick(wav: Path, fade_in: float = 0.02, fade_out: float = 0.04) -> None:
    """Tiny fades so clips that start/end abruptly don't click when joined."""
    from .util import ffprobe_duration

    dur = ffprobe_duration(wav)
    tmp = wav.with_name(wav.stem + "_dc.wav")
    run([FFMPEG, "-y", "-i", wav, "-af",
         f"afade=t=in:d={fade_in},afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out}", tmp])
    tmp.replace(wav)


def trim_tail_burst(wav: Path, quiet_gap: float = 0.2, max_burst: float = 0.45) -> bool:
    """Cut a short loud burst that follows the real speech after a quiet gap.

    Gemini clips sometimes end with ~0.15 s of loud noise (heard as a scratch at every
    scene cut). Only a trailing burst shorter than `max_burst`, separated from the speech
    by at least `quiet_gap` of near-silence, is removed. Returns True when trimmed."""
    import array
    import wave

    with wave.open(str(wav), "rb") as r:
        params = r.getparams()
        if params.sampwidth != 2 or params.nchannels != 1:
            return False
        data = array.array("h")
        data.frombytes(r.readframes(params.nframes))
    sr = params.framerate
    step = max(1, int(sr * 0.01))
    rms = [(sum(x * x for x in data[i:i + step]) / max(1, len(data[i:i + step]))) ** 0.5
           for i in range(0, len(data), step)]
    if not rms:
        return False
    loud = max(rms) * 0.12
    tail_end = len(rms)
    while tail_end > 0 and rms[tail_end - 1] < loud:
        tail_end -= 1
    b_end = tail_end
    b_start = b_end
    while b_start > 0 and rms[b_start - 1] >= loud:
        b_start -= 1
    gap_end = b_start
    g_start = gap_end
    while g_start > 0 and rms[g_start - 1] < loud:
        g_start -= 1
    if (b_end - b_start) * 0.01 > max_burst or (gap_end - g_start) * 0.01 < quiet_gap or g_start == 0:
        return False
    keep = min(len(data), (g_start + 12) * step)  # keep the word's fading tail
    with wave.open(str(wav), "wb") as w:
        w.setparams(params)
        w.writeframes(data[:keep].tobytes())
    return True


def level_match(wav: Path, target_lufs: float = -20.0) -> None:
    """Give a narration segment a consistent overall loudness with ONE gain change
    (no compression), so separate TTS calls match each other while each keeps its
    own natural loud/soft expression."""
    import re

    proc = run([FFMPEG, "-hide_banner", "-nostats", "-i", wav, "-af", "ebur128=peak=true",
                "-f", "null", "-"])
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)
    if not m:
        return
    gain = max(-12.0, min(12.0, target_lufs - float(m[-1])))
    tmp = wav.with_name(wav.stem + "_lm.wav")
    run([FFMPEG, "-y", "-i", wav, "-af", f"volume={gain:.2f}dB,alimiter=limit=0.95", tmp])
    tmp.replace(wav)


def synthesize_gemini(text: str, out_wav: Path, voice: str, style: str,
                      retries: int = 4, timings: bool = True,
                      mode: str = "say") -> list[WordTiming] | None:
    """Narrate with Gemini TTS (expressive, directable). Returns estimated word
    timings (or None when timings=False, e.g. English, where speech recognition
    gives exact ones). Raises PipelineError after repeated failures."""
    from . import genmedia

    global _gemini_blocked_until
    if time.time() < _gemini_blocked_until:
        raise PipelineError("gemini tts skipped (failed recently in this run)")
    last: Exception | None = None
    import re

    telugu = bool(re.search(r"[ఀ-౿]", text))
    # Delivery directions of ANY length can be read aloud ("say it with a smile"), so the
    # style is deliberately NOT sent; the voice infers its delivery from the text itself.
    cur_style = ""
    for attempt in range(1, retries + 1):
        try:
            genmedia.gemini_tts(text, out_wav, voice, cur_style, mode=mode)
            from .util import ffprobe_duration
            limit = 2.5 + len(text.split()) * (1.5 if telugu else 0.9)
            if ffprobe_duration(out_wav) > limit:
                # far longer than the text needs: the model read the directions aloud.
                # Plain text (no style) is always read cleanly, so retry that way.
                cur_style = ""
                raise PipelineError("clip is much longer than its text (style read aloud?)")
            level_match(out_wav)
            time.sleep(1.5)  # stay polite with the free tier's request rate
            return approx_word_timings(out_wav, text) if timings else None
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(10 * attempt)
    _gemini_blocked_until = time.time() + 1800  # don't burn minutes retrying every segment
    raise PipelineError(f"gemini tts failed after {retries} tries: {last}")


_gemini_blocked_until = 0.0


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
