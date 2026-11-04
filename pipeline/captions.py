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


def _write_ass(chunks: list[Chunk], path: Path, w: int, h: int, font: str,
               upper: bool = True, spacing: float = 0.4, size_scale: float = 1.0) -> None:
    # spacing must be 0 for Indic scripts: any letter-spacing makes libass drop
    # text shaping, which detaches Telugu vowel signs from their letters.
    fontsize = round(h * (0.041 if w < h else 0.052) * size_scale)
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
Style: Caption,{font},{fontsize},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,{spacing},0,1,{outline},1,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = []
    for c in chunks:
        txt = c.text.replace("\n", " ")
        if upper:
            txt = txt.upper()
        body.append(
            f"Dialogue: 0,{_fmt_ass(c.start)},{_fmt_ass(c.end)},Caption,,0,0,0,,"
            f"{{\\fad(70,70)}}{txt}"
        )
    path.write_text(header + "\n".join(body) + "\n", encoding="utf-8")


def _tokens_with_punctuation(timings: list, text: str) -> list:
    """Edge returns words without punctuation. When the script's own
    whitespace-split words line up 1:1 with the timings, use those (so the
    captions keep their commas and question marks)."""
    import re

    def norm(s: str) -> str:
        return re.sub(r"\W", "", s.lower())

    src = text.split()
    out, i = [], 0
    for s, e, tok in timings:
        n = max(1, len(tok.split()))  # Edge sometimes merges e.g. "2 years old"
        chunk = src[i:i + n]
        if len(chunk) < n or norm("".join(chunk)) != norm(tok):
            return list(timings)
        out.append((s, e, " ".join(chunk)))
        i += n
    return out if i == len(src) else list(timings)


def _write_ass_highlight(groups: list[list], path: Path, w: int, h: int, font: str,
                         size_scale: float, card: tuple[str, str] | None,
                         card_secs: float = 2.8) -> None:
    """Explainer-style captions: a short line of 2-3 words with the word being spoken in
    orange, plus an optional opening title card (small orange kicker + big white title)."""
    fontsize = round(h * 0.05 * size_scale)
    margin_v = round(h * 0.27)
    outline = max(3, round(fontsize * 0.1))
    orange, white = "&H0080FF&", "&HFFFFFF&"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{fontsize},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,80,80,{margin_v},1
Style: Card,{font},{round(h * 0.07)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline + 2},2,8,70,70,{round(h * 0.12)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = []
    if card:
        kicker, title = card
        big = 0.075 if len(title) <= 12 else 0.055 if len(title) <= 18 else 0.042
        body.append(
            f"Dialogue: 1,{_fmt_ass(0.05)},{_fmt_ass(card_secs)},Card,,0,0,0,,"
            f"{{\\fad(120,250)\\fs{round(h * 0.036)}\\c{orange}}}{kicker}\\N"
            f"{{\\fs{round(h * big)}\\c{white}}}{title}")
    for g in groups:  # g = [(start, end, token), ...] for one on-screen line
        for k, (s, e, _tok) in enumerate(g):
            end = g[k + 1][0] if k + 1 < len(g) else e + 0.15
            parts = [(f"{{\\c{orange}}}{t}{{\\c{white}}}" if j == k else t)
                     for j, (_, _, t) in enumerate(g)]
            body.append(f"Dialogue: 0,{_fmt_ass(s)},{_fmt_ass(end)},Caption,,0,0,0,,"
                        f"{' '.join(parts)}")
    path.write_text(header + "\n".join(body) + "\n", encoding="utf-8")


def generate_from_timings(scenes: list[tuple[list, float, str]], out_dir: Path,
                          stem: str, w: int, h: int, cfg: dict,
                          lang: str = "en", highlight: bool = False,
                          card: tuple[str, str] | None = None) -> dict:
    """Captions straight from TTS word timings (no speech recognition).
    scenes: (word_timings, start_offset_seconds, narration_text) per scene."""
    cap_cfg = cfg.get("captions", {})
    max_words = int(cap_cfg.get("max_words_per_line", 4))
    if lang != "en":
        max_words = min(max_words, 3)  # Telugu words are long
    if w < h:
        max_words = min(max_words, 3)
    font = cap_cfg.get("font", "Arial") if lang == "en" else cap_cfg.get("te_font", "Noto Sans Telugu")

    all_chunks: list[Chunk] = []
    groups: list[list] = []
    for timings, offset, text in scenes:
        words = _tokens_with_punctuation(timings, text)
        if words:
            all_chunks.extend(_chunk_words(words, offset, max_words))
            shifted = [(s + offset, e + offset, t) for s, e, t in words]
            groups.extend(shifted[i:i + max_words] for i in range(0, len(shifted), max_words))

    srt = out_dir / f"{stem}.srt"
    ass = out_dir / f"{stem}.ass"
    _write_srt(all_chunks, srt)
    if highlight:
        _write_ass_highlight(groups, ass, w, h, font, 1.3 if lang != "en" else 1.0, card)
    elif lang == "en":
        _write_ass(all_chunks, ass, w, h, font)
    else:
        _write_ass(all_chunks, ass, w, h, font, upper=False, spacing=0.0, size_scale=1.3)
    return {"srt": srt, "ass": ass, "chunks": all_chunks}


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
