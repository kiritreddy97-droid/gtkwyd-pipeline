"""Turn a markdown script into a finished YouTube video.

    ./run.ps1 scripts/my-topic.md
    ./run.ps1 --list-voices
    ./run.ps1 scripts/my-topic.md --no-stock      # plain slides, no API keys needed
    ./run.ps1 scripts/my-topic.md --no-captions
"""
from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from pathlib import Path

import random

from pipeline import assemble, captions, metadata, music_match, tts
from pipeline.script_parser import parse_script
from pipeline.util import (ASSETS_DIR, BUILD_DIR, PipelineError, check_ffmpeg,
                           dims, load_config, slugify)
from pipeline.visuals import Asset, VisualFetcher
from pipeline import illustrate, writer_te
from pipeline.voices import (DEFAULT_VOICE, EDGE_PREFIX, ensure_voice, list_voices,
                             pick_voice)


OUTRO_SPOKEN = ("For more information and to stay updated, please like, "
               "share, and subscribe.")
OUTRO_DISPLAY = "LIKE, SHARE & SUBSCRIBE for more like this"
OUTRO_SPOKEN_TE = ("ఇలాంటి మరిన్ని చరిత్ర కథల కోసం, లైక్ చేయండి, షేర్ చేయండి, "
                   "మరియు సబ్‌స్క్రైబ్ చేయండి.")
GEMINI_EN_VOICES = ["Charon", "Orus", "Fenrir", "Puck", "Kore", "Aoede", "Leda", "Zephyr"]
_EN_COMMON = (" Sound like a real person talking to a friend, never like reading a script: "
              "vary pace and pitch with the meaning, lean on key words, take small natural "
              "pauses, rise on questions. Keep one consistent voice throughout.")
EN_STYLE_VIDEO = ("Narrate like a warm, curious documentary storyteller who is genuinely "
                  "fascinated by the subject." + _EN_COMMON)
EN_STYLE_SHORT = ("Narrate like an energetic, engaging creator hooking viewers in the first "
                  "second: punchy, quick, with real excitement and a clear emphasis on the "
                  "surprising part." + _EN_COMMON)
EN_STYLE_REEL = ("Narrate like a friendly, expressive storyteller speaking to camera for a "
                 "short vertical video." + _EN_COMMON)
DEFAULT_TE_STYLE = (
    "Narrate in natural, warm, conversational Telugu, like a gifted storyteller telling a "
    "true story to family around a fire: expressive and human, never like reading a passage. "
    "Vary the pace and pitch with the meaning: slow down and lower your voice at dramatic "
    "or sad moments, speed up with excitement, rise on questions, add small natural pauses "
    "and gentle emphasis on key words. Keep one consistent voice throughout.")
OUTRO_DISPLAY_TE = "ఇలాంటి మరిన్ని కథల కోసం\nసబ్‌స్క్రైబ్ చేయండి"


def _pick_music(is_short: bool, title: str = "", tags: list[str] | None = None) -> Path | None:
    """Mood-matched track from your library (assets/music/) or, if that's
    empty (e.g. a fresh clone / cloud run), the small always-committed
    assets/cloud-music/. Falls back to a random pick when nothing matches."""
    kind = "shorts" if is_short else "videos"
    for base in (ASSETS_DIR / "music", ASSETS_DIR / "cloud-music"):
        for candidate in (base / kind, base):
            if candidate.is_dir():
                pool = sorted(candidate.glob("*.mp3")) + sorted(candidate.glob("*.m4a"))
                if pool:
                    return music_match.pick(pool, title, tags)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(prog="make_video")
    ap.add_argument("script", nargs="?", help="path to a markdown script")
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--no-stock", action="store_true",
                    help="render plain title slides instead of stock footage")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--short", action="store_true",
                    help="render a vertical YouTube Short (1080x1920, adds #Shorts)")
    ap.add_argument("--portrait", action="store_true",
                    help="render vertical 1080x1920 without Shorts-specific "
                         "behavior (for Instagram-only content, e.g. stories)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the working directory even on success")
    args = ap.parse_args()

    if args.list_voices:
        list_voices()
        return 0
    if not args.script:
        ap.print_help()
        return 1

    check_ffmpeg()
    cfg = load_config()
    script = parse_script(args.script)
    if args.short or args.portrait:
        cfg.setdefault("project", {})["orientation"] = "portrait"
    if args.short:
        cfg.setdefault("music", {})
        cfg["music"]["volume"] = float(cfg["music"].get("short_volume", 0.16))
    w, h = dims(cfg)
    fps = int(cfg.get("project", {}).get("fps", 30))
    channel = cfg.get("project", {}).get("channel_name", "My Channel")

    slug = slugify(Path(args.script).stem)
    workdir = BUILD_DIR / slug
    if workdir.exists():
        shutil.rmtree(workdir)
    (workdir / "narration").mkdir(parents=True)

    lang = script.lang
    vcfg = cfg.get("voice", {})
    # Telugu always uses the Edge neural voices as the fallback (Piper has no
    # usable Telugu voice). engine = "gemini" means: expressive Gemini voice first,
    # Edge as the automatic fallback.
    engine_cfg = vcfg.get("engine", "piper")
    engine = ("edge" if engine_cfg == "gemini" else engine_cfg) if lang == "en" else "edge"
    voice_name = pick_voice(script.voice, rotate=bool(vcfg.get("rotate", True)),
                            fallback=vcfg.get("model", DEFAULT_VOICE),
                            engine=engine, lang=lang)
    use_edge = voice_name.startswith(EDGE_PREFIX)
    if lang != "en":
        cfg.setdefault("visuals", {})["prefer_animation"] = False  # history needs real imagery
        channel = cfg.get("telugu", {}).get("channel_name", channel)
    # Small per-render jitter around the configured base values, so two
    # videos using the same voice still don't come out sounding identical.
    length_scale = max(0.85, float(vcfg.get("length_scale", 1.0)) + random.uniform(-0.04, 0.05))
    sentence_silence = float(vcfg.get("sentence_silence", 0.35))
    noise_scale = max(0.3, float(vcfg.get("noise_scale", 0.667)) + random.uniform(-0.06, 0.06))
    noise_w = max(0.3, float(vcfg.get("noise_w", 0.8)) + random.uniform(-0.08, 0.08))
    base_rate = int(vcfg.get("edge_rate", -3)) if lang == "en" else int(vcfg.get("te_rate", -6))
    rate_pct = base_rate + random.randint(-2, 2)
    pitch_hz = random.randint(-2, 2)
    if use_edge:
        print(f"[voice]  {voice_name}  (rate={rate_pct:+d}% pitch={pitch_hz:+d}Hz)")
    else:
        print(f"[voice]  {voice_name}  (length={length_scale:.2f} noise={noise_scale:.2f}/{noise_w:.2f})")

    piper_onnx = None

    def speak(text: str, wav: Path):
        """Narrate text into wav. Returns word timings (Edge) or None (Piper)."""
        nonlocal piper_onnx
        if use_edge:
            try:
                return tts.synthesize_edge(text, wav, voice_name[len(EDGE_PREFIX):],
                                           rate_pct, pitch_hz)
            except PipelineError as e:
                if lang != "en":
                    raise
                print(f"[voice]  Edge voice failed ({e}); falling back to Piper")
        if piper_onnx is None:
            piper_onnx, _ = ensure_voice(DEFAULT_VOICE if use_edge else voice_name)
        tts.synthesize(text, wav, piper_onnx, length_scale, sentence_silence,
                       noise_scale=noise_scale, noise_w=noise_w)
        return None

    # Narrate everything up front so a voice problem is found before any video
    # work and one video never mixes two voices. Telugu prefers Gemini's
    # expressive voice (no word timings - estimated from the audio's pauses);
    # if it fails for any segment the WHOLE video is redone with the Edge voice.
    outro_spoken = OUTRO_SPOKEN if lang == "en" else OUTRO_SPOKEN_TE
    outro_display = OUTRO_DISPLAY if lang == "en" else OUTRO_DISPLAY_TE
    seg_texts = [sc.narration for sc in script.scenes] + [outro_spoken]
    seg_wavs = [workdir / "narration" / f"scene_{i:02d}.wav" for i in range(len(seg_texts))]
    seg_timings = None
    used_gemini = False
    tcfg = cfg.get("telugu", {})
    want_gemini = (tcfg.get("voice_engine", "edge") == "gemini") if lang != "en" \
        else (engine_cfg == "gemini")
    if want_gemini and writer_te.available():
        if lang != "en":
            gem_voice = tcfg.get("gemini_voice", "Charon")
            gem_style = script.style or tcfg.get("gemini_style", DEFAULT_TE_STYLE)
        else:
            gem_voice = script.voice if script.voice in GEMINI_EN_VOICES \
                else random.choice(GEMINI_EN_VOICES)
            gem_style = script.style or (EN_STYLE_SHORT if args.short
                                         else EN_STYLE_REEL if args.portrait else EN_STYLE_VIDEO)
        try:
            print(f"[voice]  Gemini expressive voice '{gem_voice}' for {len(seg_texts)} segments ...")
            # Telugu: estimate word timings from the audio's pauses. English: leave
            # them out so the (accurate) speech-recognition caption path is used.
            seg_timings = [tts.synthesize_gemini(t, w, gem_voice, gem_style,
                                                 timings=(lang != "en"))
                           for t, w in zip(seg_texts, seg_wavs)]
            used_gemini = True
        except PipelineError as e:
            print(f"[voice]  Gemini voice failed ({e}); using the Edge voice for the whole video")
    if seg_timings is None:
        seg_timings = [speak(t, w) for t, w in zip(seg_texts, seg_wavs)]

    fetcher = None
    if not args.no_stock:
        fetcher = VisualFetcher(cfg)

    art_ok = illustrate.available(cfg)
    art_model = tcfg.get("image_model", illustrate.DEFAULT_MODEL)
    art_steps = int(tcfg.get("image_steps", 2))
    if art_ok and any(sc.art for sc in script.scenes):
        print(f"[art]    AI illustrations with {art_model} ({art_steps} steps)")

    print(f"[scenes] {len(script.scenes)}")
    scene_finals: list[Path] = []
    scene_durations: list[float] = []
    scene_wavs: list[tuple[Path, float]] = []
    scene_timings: list[tuple[list | None, float, str]] = []
    first_asset: Asset | None = None
    sources: set[str] = set()
    running = 0.0

    for i, scene in enumerate(script.scenes):
        print(f"  scene {i + 1}/{len(script.scenes)}: {scene.heading}")
        wav = seg_wavs[i]
        timings = seg_timings[i]

        slide_text = None
        assets: list[Asset] = []
        if args.no_stock:
            slide_text = scene.heading
        else:
            # AI illustrations first (they match the story); stock footage is the
            # fallback for any scene where drawing isn't possible or fails.
            if scene.art and art_ok:
                made: list[Asset] = []
                for j, prompt in enumerate(scene.art):
                    print(f"      art: {prompt[:78]}")
                    try:
                        p = illustrate.generate(
                            prompt, workdir / f"art_{i:02d}_{j}.jpg", portrait=(w < h),
                            model=art_model, steps=art_steps, seed=1000 * i + j)
                        made.append(Asset(p, "image", prompt, "ai"))
                    except PipelineError as e:
                        print(f"      art failed ({e}); using stock footage for this scene")
                        made = []
                        break
                if made:
                    assets = made
                    sources.add("ai")
            if not assets:
                for q in scene.queries:
                    print(f"      stock: {q}")
                    a = fetcher.fetch(q)
                    assets.append(a)
                    sources.add(a.source)
            if assets and first_asset is None:
                first_asset = assets[0]

        scene_mp4, dur = assemble.build_scene(
            i, wav, assets, cfg, workdir, slide_text=slide_text, lang=lang)
        scene_finals.append(scene_mp4)
        scene_durations.append(dur)
        scene_wavs.append((wav, running))
        scene_timings.append((timings, running, scene.narration))
        running += dur

    # Every video and Short ends on the same like/share/subscribe card -
    # built the same way as --no-stock's plain slides, so it gets a real
    # narrated scene (captioned like the rest) rather than a bolted-on clip.
    cta_idx = len(script.scenes)
    print(f"  scene {cta_idx + 1}/{len(script.scenes) + 1}: outro (like/share/subscribe)")
    cta_wav = seg_wavs[cta_idx]
    cta_timings = seg_timings[cta_idx]
    cta_mp4, cta_dur = assemble.build_scene(
        cta_idx, cta_wav, [], cfg, workdir, slide_text=outro_display, lang=lang)
    scene_finals.append(cta_mp4)
    scene_durations.append(cta_dur)
    scene_wavs.append((cta_wav, running))
    scene_timings.append((cta_timings, running, outro_spoken))
    running += cta_dur

    ass_path = None
    srt_out = None
    if cfg.get("captions", {}).get("enabled", True) and not args.no_captions:
        if all(t is not None for t, _, _ in scene_timings):
            print("[captions] from the voice's own word timings ...")
            cap = captions.generate_from_timings(scene_timings, workdir, slug, w, h,
                                                 cfg, lang)
        else:
            print("[captions] transcribing narration ...")
            cap = captions.generate(scene_wavs, workdir, slug, w, h, cfg)
        ass_path = cap["ass"]
        srt_out = cap["srt"]

    music_path = None
    if script.music:
        mp = (Path(script.music) if Path(script.music).is_absolute()
              else Path.cwd() / script.music)
        if mp.exists():
            music_path = mp
        else:
            print(f"[music]  {script.music} not found - trying music library")
    if music_path is None:
        music_path = _pick_music(args.short, script.title, script.tags)
    if music_path:
        from pipeline import music_match as _mm
        mood = _mm.target_mood(script.title, script.tags)
        print(f"[music]  {music_path.name}  (mood: {mood})")

    print("[render] final mux ...")
    out_mp4 = BUILD_DIR / slug / f"{slug}.mp4"
    total = assemble.finalize(scene_finals, music_path, ass_path, cfg, workdir, out_mp4)

    # collateral
    scene_starts = [sum(scene_durations[:i]) for i in range(len(scene_durations))]
    thumb = BUILD_DIR / slug / f"{slug}_thumbnail.png"
    try:
        from pipeline import thumbnail
        thumbnail.generate(script.title, channel, first_asset, workdir, thumb,
                           tags=script.tags, show_badge=not args.portrait, lang=lang,
                           thumb_text=script.thumb_text)
    except Exception as e:
        print(f"[thumb]  skipped ({e})")
        thumb = None

    meta_txt = BUILD_DIR / slug / f"{slug}_metadata.txt"
    metadata.generate(script, scene_starts, total, sources, channel, meta_txt,
                      is_short=args.short, lang=lang,
                      voice_credit=("Google Gemini text-to-speech" if used_gemini
                                    else "Microsoft neural text-to-speech (Edge voices)"
                                    if use_edge else "Piper (open-source, offline)"))

    # captions.generate already writes {slug}.srt / {slug}.ass into workdir,
    # which is the build/<slug> output dir, so nothing to copy.

    # clean intermediates
    if not args.keep:
        for pat in ("art_*.jpg", "seg_*", "scene_*.mp4", "scene_*_v.mp4", "*_list.txt",
                    "body.mp4", "slide_*.png", "_thumb_frame.jpg",
                    f"{slug}.ass", "*.ttf", "*.otf"):
            for f in workdir.glob(pat):
                f.unlink(missing_ok=True)
        narr = workdir / "narration"
        if narr.exists():
            shutil.rmtree(narr, ignore_errors=True)

    print("\nDONE")
    print(f"  video     {out_mp4}")
    if thumb:
        print(f"  thumbnail {thumb}")
    print(f"  metadata  {meta_txt}")
    if srt_out:
        print(f"  captions  {BUILD_DIR / slug / f'{slug}.srt'}")
    print(f"  runtime   {int(total // 60)}m {int(total % 60)}s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PipelineError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
