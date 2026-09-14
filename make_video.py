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
from pipeline.voices import DEFAULT_VOICE, ensure_voice, list_voices, pick_voice


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
    if args.short:
        cfg.setdefault("project", {})["orientation"] = "portrait"
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

    vcfg = cfg.get("voice", {})
    voice_name = pick_voice(script.voice, rotate=bool(vcfg.get("rotate", True)),
                            fallback=vcfg.get("model", DEFAULT_VOICE))
    onnx, _ = ensure_voice(voice_name)
    # Small per-render jitter around the configured base values, so two
    # videos using the same voice still don't come out sounding identical.
    length_scale = max(0.85, float(vcfg.get("length_scale", 1.0)) + random.uniform(-0.04, 0.05))
    sentence_silence = float(vcfg.get("sentence_silence", 0.35))
    noise_scale = max(0.3, float(vcfg.get("noise_scale", 0.667)) + random.uniform(-0.06, 0.06))
    noise_w = max(0.3, float(vcfg.get("noise_w", 0.8)) + random.uniform(-0.08, 0.08))
    print(f"[voice]  {voice_name}  (length={length_scale:.2f} noise={noise_scale:.2f}/{noise_w:.2f})")

    fetcher = None
    if not args.no_stock:
        fetcher = VisualFetcher(cfg)

    print(f"[scenes] {len(script.scenes)}")
    scene_finals: list[Path] = []
    scene_durations: list[float] = []
    scene_wavs: list[tuple[Path, float]] = []
    first_asset: Asset | None = None
    sources: set[str] = set()
    running = 0.0

    for i, scene in enumerate(script.scenes):
        print(f"  scene {i + 1}/{len(script.scenes)}: {scene.heading}")
        wav = workdir / "narration" / f"scene_{i:02d}.wav"
        tts.synthesize(scene.narration, wav, onnx, length_scale, sentence_silence,
                      noise_scale=noise_scale, noise_w=noise_w)

        slide_text = None
        assets: list[Asset] = []
        if args.no_stock:
            slide_text = scene.heading
        else:
            for q in scene.queries:
                print(f"      stock: {q}")
                a = fetcher.fetch(q)
                assets.append(a)
                sources.add(a.source)
            if assets and first_asset is None:
                first_asset = assets[0]

        scene_mp4, dur = assemble.build_scene(
            i, wav, assets, cfg, workdir, slide_text=slide_text)
        scene_finals.append(scene_mp4)
        scene_durations.append(dur)
        scene_wavs.append((wav, running))
        running += dur

    ass_path = None
    srt_out = None
    if cfg.get("captions", {}).get("enabled", True) and not args.no_captions:
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
                           tags=script.tags)
    except Exception as e:
        print(f"[thumb]  skipped ({e})")
        thumb = None

    meta_txt = BUILD_DIR / slug / f"{slug}_metadata.txt"
    metadata.generate(script, scene_starts, total, sources, channel, meta_txt,
                      is_short=args.short)

    # captions.generate already writes {slug}.srt / {slug}.ass into workdir,
    # which is the build/<slug> output dir, so nothing to copy.

    # clean intermediates
    if not args.keep:
        for pat in ("seg_*", "scene_*.mp4", "scene_*_v.mp4", "*_list.txt",
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
