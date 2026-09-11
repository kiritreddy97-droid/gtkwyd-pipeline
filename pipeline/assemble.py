"""ffmpeg assembly: per-scene video build, then final mux with captions + music."""
from __future__ import annotations

import shutil
from pathlib import Path

from .util import ASSETS_DIR, FFMPEG, PipelineError, dims, ffprobe_duration, run

_TAIL = 0.35  # seconds of breathing room after each scene's narration


def _vfilter(w: int, h: int, fps: int) -> str:
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1,fps={fps},format=yuv420p")


def render_slide(text: str, w: int, h: int, dest: Path) -> Path:
    """Plain dark slide with centred text — used by --no-stock."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (w, h), (14, 17, 22))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arialbd.ttf", round(h * 0.06))
    except OSError:
        font = ImageFont.load_default()
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) > w * 0.8 and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    line_h = round(h * 0.08)
    y = h // 2 - line_h * len(lines) // 2
    for line in lines:
        tw = draw.textlength(line, font=font)
        draw.text((w // 2 - tw // 2, y), line, font=font, fill=(238, 238, 238))
        y += line_h
    img.save(dest)
    return dest


def _segment(asset_path: Path, kind: str, seconds: float, out: Path,
             w: int, h: int, fps: int, ken_in: bool) -> Path:
    seconds = max(0.6, seconds)
    frames = max(2, round(seconds * fps))
    if kind == "video":
        cmd = [FFMPEG, "-y", "-stream_loop", "-1", "-t", f"{seconds:.3f}",
               "-i", asset_path, "-an", "-vf", _vfilter(w, h, fps),
               "-frames:v", str(frames),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
               "-pix_fmt", "yuv420p", "-r", str(fps), out]
    else:
        # Ken Burns on a still. Feed ONE image frame (no -loop): zoompan then
        # emits exactly d frames. A gentle zoom that alternates in/out; the
        # up-scale before zoompan keeps the pan from stair-stepping.
        up_w, up_h = int(w * 1.5), int(h * 1.5)
        start, end = (1.0, 1.10) if ken_in else (1.10, 1.0)
        step = (end - start) / frames
        z = f"{start}+{step}*on" if ken_in else f"{start}{step:+f}*on"
        vf = (f"scale={up_w}:{up_h}:force_original_aspect_ratio=increase,"
              f"crop={up_w}:{up_h},"
              f"zoompan=z='{z}':d={frames}:fps={fps}:s={w}x{h}"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
              f"setsar=1,format=yuv420p")
        cmd = [FFMPEG, "-y", "-i", asset_path, "-vf", vf,
               "-frames:v", str(frames), "-t", f"{seconds:.3f}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
               "-pix_fmt", "yuv420p", "-r", str(fps), out]
    run(cmd)
    return out


def _concat_copy(parts: list[Path], out: Path, workdir: Path) -> Path:
    listing = workdir / f"{out.stem}_list.txt"
    listing.write_text(
        "".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8"
    )
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listing.name,
         "-c", "copy", out.name], cwd=workdir)
    return out


def build_scene(idx: int, narration_wav: Path, assets: list, cfg: dict,
                workdir: Path, slide_text: str | None = None) -> tuple[Path, float]:
    w, h = dims(cfg)
    fps = int(cfg.get("project", {}).get("fps", 30))
    dur = ffprobe_duration(narration_wav) + _TAIL

    if slide_text is not None:  # --no-stock
        png = render_slide(slide_text, w, h, workdir / f"slide_{idx:02d}.png")
        segs = [_segment(png, "image", dur, workdir / f"seg_{idx:02d}_00.mp4",
                         w, h, fps, ken_in=(idx % 2 == 0))]
    else:
        n = max(1, len(assets))
        share = dur / n
        segs = []
        for j, asset in enumerate(assets):
            segs.append(_segment(
                asset.path, asset.kind, share,
                workdir / f"seg_{idx:02d}_{j:02d}.mp4",
                w, h, fps, ken_in=((idx + j) % 2 == 0),
            ))

    scene_vid = (segs[0] if len(segs) == 1
                 else _concat_copy(segs, workdir / f"scene_{idx:02d}_v.mp4", workdir))

    scene_final = workdir / f"scene_{idx:02d}.mp4"
    run([FFMPEG, "-y", "-i", scene_vid, "-i", narration_wav,
         "-map", "0:v:0", "-map", "1:a:0",
         "-af", "apad,aformat=sample_rates=48000:channel_layouts=stereo",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps),
         "-c:a", "aac", "-b:a", "192k", "-shortest", scene_final])
    return scene_final, ffprobe_duration(scene_final)


def finalize(scene_finals: list[Path], music: Path | None, ass: Path | None,
             cfg: dict, workdir: Path, out_mp4: Path) -> float:
    w, h = dims(cfg)
    fps = int(cfg.get("project", {}).get("fps", 30))
    mv = float(cfg.get("music", {}).get("volume", 0.12))

    body = _concat_copy(scene_finals, workdir / "body.mp4", workdir)
    total = ffprobe_duration(body)

    # bundle any project fonts next to the render so libass can find them
    fonts_dir = ASSETS_DIR / "fonts"
    have_fonts = fonts_dir.exists() and any(fonts_dir.glob("*.ttf"))
    if have_fonts:
        for ttf in list(fonts_dir.glob("*.ttf")) + list(fonts_dir.glob("*.otf")):
            shutil.copy(ttf, workdir / ttf.name)

    cmd = [FFMPEG, "-y", "-i", body.name]
    filt: list[str] = []
    vlabel = "0:v"
    if ass is not None:
        ass_arg = ass.name + (":fontsdir=." if have_fonts else "")
        filt.append(f"[0:v]ass={ass_arg}[v]")
        vlabel = "[v]"

    # Audio: even out the narration level, keep music as a quiet high-passed bed
    # (so it never fights the voice), then loudness-normalise the mix to
    # YouTube's -14 LUFS target so every upload sounds consistent and clear.
    if music is not None and Path(music).exists():
        cmd += ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
        fade_st = max(0.1, total - 2.5)
        filt.append("[0:a]aresample=48000,dynaudnorm=p=0.55:m=10:s=8[voc]")
        filt.append(
            f"[1:a]aresample=48000,highpass=f=110,volume={mv:.3f},"
            f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_st:.2f}:d=2.0[bed]")
        filt.append("[voc][bed]amix=inputs=2:duration=first:weights=1.0 0.85:"
                    "dropout_transition=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    else:
        filt.append("[0:a]aresample=48000,dynaudnorm=p=0.55:m=10:s=8,"
                    "loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    alabel = "[a]"

    cmd += ["-filter_complex", ";".join(filt),
            "-map", vlabel, "-map", alabel,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-maxrate", "16M", "-bufsize", "32M",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", "-t", f"{total:.3f}",
            str(Path(out_mp4).resolve())]
    run(cmd, cwd=workdir)
    if not Path(out_mp4).exists():
        raise PipelineError("final render produced no file")
    return total
