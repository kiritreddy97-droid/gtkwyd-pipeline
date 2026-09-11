"""Caption generation with faster-whisper (offline, CPU).

Each scene's narration wav is transcribed separately and its word timestamps are
shifted by that scene's start offset in the final video, so captions stay aligned
even though scenes carry trailing silence.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .util import PipelineError

_MODEL_CACHE: dict = {}


@dataclass
class Chunk:
    start: float
    end: float
    text: str


def _get_model(size: str):
    if size not in _MODEL_CACHE:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:  # pragma: no cover
            raise PipelineError("faster-whisper not installed. Re-run ./setup.ps1") from e
        print(f"  loading whisper model '{size}' (first run downloads it) ...")
        _MODEL_CACHE[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODEL_CACHE[size]


def _words_for(wav: Path, size: str) -> list:
    model = _get_model(size)
    segments, _ = model.transcribe(str(wav), word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            token = w.word.strip()
            if token:
                words.append((w.start, w.end, token))
    return words


def _chunk_words(words: list, offset: float, max_words: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    i = 0
    while i < len(words):
        group = words[i:i + max_words]
        text = " ".join(t for _, _, t in group).strip()
        # tidy spacing around punctuation
        for p in (",", ".", "!", "?", ";", ":"):
            text = text.replace(f" {p}", p)
        chunks.append(Chunk(group[0][0] + offset, group[-1][1] + offset, text))
        i += max_words
    # close gaps so a chunk stays on screen until the next begins
    for a, b in zip(chunks, chunks[1:]):
        a.end = max(a.end, min(b.start, a.end + 1.2))
    return chunks


def _fmt_srt(t: float) -> str:
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def _fmt_ass(t: float) -> str:
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):d}:{int(m):02d}:{s:05.2f}"


def _write_srt(chunks: list[Chunk], path: Path) -> None:
    lines = []
    for n, c in enumerate(chunks, 1):
        lines += [str(n), f"{_fmt_srt(c.start)} --> {_fmt_srt(c.end)}", c.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_ass(chunks: list[Chunk], path: Path, w: int, h: int, font: str) -> None:
    fontsize = round(h * (0.041 if w < h else 0.052))
    margin_v = round(h * (0.24 if w < h else 0.09))  # portrait: clear the Shorts UI
    outline = max(2, round(fontsize * 0.12))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{fontsize},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0.4,0,1,{outline},1,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = []
    for c in chunks:
        txt = c.text.replace("\n", " ").upper()
        body.append(
            f"Dialogue: 0,{_fmt_ass(c.start)},{_fmt_ass(c.end)},Caption,,0,0,0,,"
            f"{{\\fad(70,70)}}{txt}"
        )
    path.write_text(header + "\n".join(body) + "\n", encoding="utf-8")


def generate(scene_wavs: list[tuple[Path, float]], out_dir: Path, stem: str,
             w: int, h: int, cfg: dict) -> dict:
    """scene_wavs: list of (wav_path, start_offset_seconds). Returns paths dict."""
    cap_cfg = cfg.get("captions", {})
    size = cap_cfg.get("whisper_model", "base")
    max_words = int(cap_cfg.get("max_words_per_line", 4))
    if w < h:  # portrait / Shorts - keep lines short so they never touch the edge
        max_words = min(max_words, 3)
    font = cap_cfg.get("font", "Arial")

    all_chunks: list[Chunk] = []
    for wav, offset in scene_wavs:
        words = _words_for(wav, size)
        all_chunks.extend(_chunk_words(words, offset, max_words))

    srt = out_dir / f"{stem}.srt"
    ass = out_dir / f"{stem}.ass"
    _write_srt(all_chunks, srt)
    _write_ass(all_chunks, ass, w, h, font)
    return {"srt": srt, "ass": ass, "chunks": all_chunks}
