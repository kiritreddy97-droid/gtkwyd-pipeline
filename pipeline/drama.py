"""AI mini-drama episodes: recurring characters with their own voices, dialogue shots
drawn as AI illustrations with camera movement, Telugu captions, and a cliffhanger.

Free pipeline: Gemini writes the episode, Gemini TTS voices each line (one distinct
voice per character, a short emotion cue per line), an open-source diffusion model
draws each shot on the CPU runner, and the normal scene builder animates and mixes it.
This is a motion-comic look, not lip-synced AI video (that needs paid GPU services).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import assemble, captions, genmedia, illustrate, tts, writer_te
from .util import FFMPEG, PipelineError, ffprobe_duration, run
from .visuals import Asset

MALE = ["Charon", "Orus", "Fenrir", "Puck"]
FEMALE = ["Kore", "Aoede", "Leda", "Zephyr"]
STYLE_3D = "Pixar-style 3D animated film still, glossy, warm cinematic lighting, Indian town"

_SHOT = {
    "type": "OBJECT",
    "properties": {
        "speaker": {"type": "STRING"},
        "emotion": {"type": "STRING"},
        "line": {"type": "STRING"},
        "visual": {"type": "STRING"},
    },
    "required": ["speaker", "emotion", "line", "visual"],
}
_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "title_en": {"type": "STRING"},
        "characters": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING"}, "gender": {"type": "STRING"}, "look": {"type": "STRING"}},
            "required": ["name", "gender", "look"]}},
        "shots": {"type": "ARRAY", "items": _SHOT},
        "to_be_continued": {"type": "STRING"},
    },
    "required": ["title", "title_en", "characters", "shots", "to_be_continued"],
}
_SYSTEM = """\
You write a short episode of an original, family-friendly Telugu animated mini-series
for Instagram Reels and YouTube Shorts, in the style of viral AI drama reels.

STORY: invent original characters and an original story (never reuse existing films,
shows, books or real people). A simple, emotional village or town story with a clear
hook in the first shot, rising tension, and a cliffhanger last shot. Warm, a little
funny, with a small human truth. No violence, politics, religion or romance.

CAST: 2 to 3 characters. Give each a "look": a vivid ENGLISH visual description (age,
build, face, hair, clothing, colours) that will be pasted word for word into every
image prompt, so make it concrete and distinctive. Indian setting and people.

SHOTS: 9 to 11 shots, about 35 to 45 seconds in total. Each shot: "speaker" (a
character's exact name or "NARRATOR"), "emotion" (a short English delivery cue such
as "excitedly", "in a worried voice", "angrily", "with a smile", "in a whisper"),
"line" (what is said, in natural spoken Telugu script, 5 to 18 words, no English
words, no stage directions), and "visual" (an ENGLISH description of the picture:
the action, pose, expression, camera angle and setting; name which characters
appear using their exact names). Vary the camera between close-ups and medium shots with faces LARGE and clearly
visible (the image model draws small, distant faces badly, so avoid wide shots where
people are tiny; use a wide shot only for places, with no people or only silhouettes).
Dialogue should feel like real people talking, with short back-and-forth lines.
"to_be_continued": the Telugu words for "to be continued..." as a teaser line.
"""


def _telugu_ratio(t: str) -> float:
    return writer_te._telugu_ratio(t)


def validate(d: dict) -> list[str]:
    probs = []
    names = {c.get("name", "").strip() for c in d.get("characters", [])}
    if not 2 <= len(names) <= 3:
        probs.append(f"{len(names)} characters (need 2-3)")
    shots = d.get("shots", [])
    if not 7 <= len(shots) <= 12:
        probs.append(f"{len(shots)} shots (need 7-12)")
    words = sum(len(s.get("line", "").split()) for s in shots)
    if not 55 <= words <= 170:
        probs.append(f"{words} words (need 55-170)")
    for i, s in enumerate(shots, 1):
        if s.get("speaker") not in names | {"NARRATOR"}:
            probs.append(f"shot {i}: unknown speaker {s.get('speaker')!r}")
        if _telugu_ratio(s.get("line", "")) < 0.85:
            probs.append(f"shot {i}: line is not Telugu")
        if not (s.get("visual") or "").strip():
            probs.append(f"shot {i}: no visual")
    return probs[:6]


def write_episode(idea: str, model: str = writer_te.DEFAULT_MODEL, attempts: int = 3) -> dict:
    last = ""
    for _ in range(attempts):
        data = writer_te._call(_SYSTEM, f"Episode idea: {idea}", _SCHEMA, model, 12000, 0.9)
        probs = validate(data)
        if not probs:
            return data
        last = "; ".join(probs)
        print(f"[drama] script problems: {last}")
    raise PipelineError(f"no acceptable episode after {attempts} tries: {last}")


def assign_voices(chars: list[dict]) -> dict[str, str]:
    voices: dict[str, str] = {"NARRATOR": "Charon"}
    m = [v for v in MALE if v != "Charon"]
    f = list(FEMALE)
    for c in chars:
        pool = f if str(c.get("gender", "")).lower().startswith(("f", "w", "g")) else m
        voices[c["name"]] = pool.pop(0) if pool else MALE[0]
    return voices


def _words(s: str, n: int) -> str:
    return " ".join(s.split()[:n])


def shot_prompt(shot: dict, chars: list[dict]) -> str:
    """Short on purpose: the image model reads only the first ~60 words. Order:
    style, then who is in the shot (their fixed look), then the action."""
    present = [c for c in chars if c["name"] in shot["visual"] or c["name"] == shot["speaker"]][:2]
    looks = "; ".join(_words(c["look"], 16) for c in present)
    return f"{STYLE_3D}, {looks}, {_words(shot['visual'], 24)}"


def edge_prosody(emotion: str) -> tuple[int, int]:
    """(rate %, pitch Hz) for the Edge fallback voices, from a delivery cue."""
    e = emotion.lower()
    if "whisper" in e:
        return -12, -3
    if any(k in e for k in ("excit", "happy", "joy", "laugh", "smil", "cheer", "surpris")):
        return 8, 3
    if any(k in e for k in ("sad", "cry", "worr", "afraid", "fear", "soft", "gentle", "tear")):
        return -8, -2
    if any(k in e for k in ("angr", "shout", "furious", "firm", "stern", "urgent")):
        return 6, 1
    return 0, 0


def _edge_voices(chars: list[dict]) -> dict[str, tuple[str, int]]:
    """character -> (Edge voice, pitch offset); two male characters get different pitch."""
    out = {"NARRATOR": ("te-IN-MohanNeural", -2)}
    male_n = 0
    for c in chars:
        if str(c.get("gender", "")).lower().startswith(("f", "w", "g")):
            out[c["name"]] = ("te-IN-ShrutiNeural", 0)
        else:
            out[c["name"]] = ("te-IN-MohanNeural", (3, -4, 6)[male_n % 3])
            male_n += 1
    return out


def _narrate(ep: dict, workdir: Path) -> tuple[list[Path], list[list], str]:
    """Voice every line. Gemini (distinct voice per character) if the free quota
    allows; otherwise the whole episode in Edge voices. Returns (wavs, word timings, engine)."""
    shots, chars = ep["shots"], ep["characters"]
    wavs = [workdir / "narration" / f"s{i:02d}.wav" for i in range(len(shots))]
    if writer_te.available():
        try:
            voices = assign_voices(chars)
            out = [tts.synthesize_gemini(s["line"], w, voices[s["speaker"]], s["emotion"],
                                         mode="emotion", retries=2)
                   for s, w in zip(shots, wavs)]
            return wavs, out, "gemini"
        except PipelineError as e:
            print(f"[drama] Gemini voices unavailable ({str(e)[:90]}); using Edge voices")
    ev = _edge_voices(chars)
    out = []
    for s, w in zip(shots, wavs):
        voice, base_pitch = ev[s["speaker"]]
        rate, pitch = edge_prosody(s["emotion"])
        out.append(tts.synthesize_edge(s["line"], w, voice, rate_pct=rate - 3,
                                       pitch_hz=pitch + base_pitch))
    return wavs, out, "edge"


def _declick_all(wavs: list[Path]) -> None:
    for w in wavs:
        tts.declick(w)


def render(ep: dict, workdir: Path, cfg: dict, music: Path | None = None) -> Path:
    """Render an episode dict to a vertical 1080x1920 mp4. Raises PipelineError."""
    cfg = json.loads(json.dumps(cfg))
    cfg.setdefault("project", {})["orientation"] = "portrait"
    w, h = 1080, 1920
    workdir = Path(workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    (workdir / "narration").mkdir(parents=True)
    art_model = cfg.get("telugu", {}).get("image_model", illustrate.DEFAULT_MODEL)
    chars = ep["characters"]

    wavs, word_timings, engine = _narrate(ep, workdir)
    _declick_all(wavs)
    print(f"[drama] voices: {engine}")

    finals, timings = [], []
    running = 0.0
    for i, shot in enumerate(ep["shots"]):
        print(f"[drama] shot {i + 1}/{len(ep['shots'])} drawing: {shot['visual'][:60]}")
        img = illustrate.generate(shot_prompt(shot, chars), workdir / f"art_{i:02d}.jpg",
                                  portrait=True, model=art_model, seed=77 + i, style="")
        mp4, dur = assemble.build_scene(i, wavs[i], [Asset(img, "image", shot["visual"], "ai")],
                                        cfg, workdir, lang="te")
        finals.append(mp4)
        timings.append((word_timings[i], running, shot["line"]))
        running += dur
    # "to be continued" end card on a silent beat
    n = len(ep["shots"])
    sil = workdir / "narration" / "end.wav"
    run([FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "2.4", sil])
    end_mp4, end_dur = assemble.build_scene(n, sil, [], cfg, workdir,
                                            slide_text=ep.get("to_be_continued") or "...",
                                            lang="te")
    finals.append(end_mp4)

    cap = captions.generate_from_timings(timings, workdir, "ep", w, h, cfg, lang="te")
    out = workdir / "episode.mp4"
    assemble.finalize(finals, music, cap["ass"], cfg, workdir, out, gentle=True)
    return out
