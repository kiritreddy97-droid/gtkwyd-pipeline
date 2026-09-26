"""Instagram Reels cross-posting: glimpse-clip builder + Graph API poster.

Two-stage pipeline, decoupled so a slow/broken Instagram step never blocks a
YouTube publish:

1. stage_glimpse() runs right after a successful YouTube upload (from
   auto.py), while the freshly-rendered mp4 is still on disk. It cuts an
   ~20s "glimpse" (hook + one highlight clip + a 5s spoken CTA outro) and
   hosts it as a GitHub Release asset (same trick already used for the music
   library) so it has a stable public URL. The URL + a "post after" timestamp
   (upload time + 1h) get written into that history.jsonl entry.

2. run_post_due(), run on a schedule by .github/workflows/instagram.yml,
   scans history.jsonl for glimpses whose post-after time has passed and
   haven't been posted yet, and publishes them via the Instagram Graph API.

Auth model: mirrors youtube.py - you do a one-time interactive setup on
Meta's own site (see INSTAGRAM_SETUP.md) and hand this code a long-lived
access token. It's never asked for a password.

Env vars (GitHub Secrets in CI):
  IG_USER_ID       - Instagram Business Account ID (numeric)
  IG_ACCESS_TOKEN  - long-lived Instagram Graph API access token

Usage:
    python -m pipeline.instagram post-due
    python -m pipeline.instagram build <video.mp4> <out.mp4>   (local testing)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

from .util import (ASSETS_DIR, FFMPEG, ROOT, PipelineError, check_ffmpeg,
                   ffprobe_duration, load_config, run)

# Tokens from the "Instagram API with Instagram Login" flow (the IGAA...
# prefix) are only valid against graph.instagram.com, not graph.facebook.com -
# sending one to the Facebook host gets "Cannot parse access token" (code
# 190), which looks like a corrupted/malformed token but is actually just the
# wrong API host for this token type.
GRAPH = "https://graph.instagram.com/v21.0"

IG_W, IG_H = 1080, 1920
HOOK_SECONDS = 8.0
HIGHLIGHT_SECONDS = 8.0
CTA_SECONDS = 5.0
CTA_LINE = ("For more content like this, watch my YouTube channel. "
            "Link is in bio, and you can check the comments too.")


# --------------------------------------------------------------------------- #
# clip building
# --------------------------------------------------------------------------- #
def _vertical_filter() -> str:
    """Center-crop a landscape source to a 9:16 vertical frame."""
    return (f"scale=-2:{IG_H}:force_original_aspect_ratio=increase,"
            f"crop={IG_W}:{IG_H}:exact=1,setsar=1")


def _extract_segment(src: Path, start: float, seconds: float, out: Path) -> None:
    run([FFMPEG, "-y", "-ss", f"{start:.2f}", "-i", src, "-t", f"{seconds:.2f}",
         "-vf", _vertical_filter(), "-r", "30",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000", out])


def _pick_highlight_start(duration: float) -> float:
    """Somewhere in the back half of the video, away from the very end."""
    lo = duration * 0.45
    hi = max(lo + 1.0, duration * 0.75)
    return round(random.uniform(lo, hi), 2)


def _build_cta_clip(cfg: dict, out: Path) -> None:
    """5s outro card: dark background, spoken CTA, matching text overlay."""
    from . import tts as tts_mod
    from .voices import DEFAULT_VOICE, ensure_voice

    handle = cfg.get("social", {}).get("youtube_handle", "").strip()

    onnx, _ = ensure_voice(DEFAULT_VOICE)
    wav = out.with_suffix(".wav")
    tts_mod.synthesize(CTA_LINE, wav, onnx, length_scale=1.0, sentence_silence=0.15)
    duration = max(CTA_SECONDS, ffprobe_duration(wav) + 0.3)

    lines = ["MORE LIKE THIS", "on YouTube"]
    if handle:
        lines.append(handle)
    lines.append("link in bio")
    text_file = out.with_suffix(".txt")
    text_file.write_text("\n".join(lines), encoding="utf-8")

    # fontfile= explicit, not relying on system fontconfig to resolve a
    # generic family name - fontconfig can be missing/misconfigured (seen
    # locally on Windows: "Cannot load default config file"), and this is
    # cheap insurance against the same fragility ever surfacing in CI too.
    # ffmpeg's filter syntax uses ':' as an option separator, so a Windows
    # drive-letter colon (C:/...) must be escaped or it's parsed as the end
    # of the fontfile value.
    font_path = (ASSETS_DIR / "fonts" / "Anton-Regular.ttf").as_posix().replace(":", r"\:")
    vf = (f"drawtext=textfile='{text_file.as_posix()}':fontfile='{font_path}':"
          f"fontcolor=white:fontsize=64:"
          f"line_spacing=18:x=(w-text_w)/2:y=(h-text_h)/2:"
          f"box=1:boxcolor=black@0.45:boxborderw=30")
    run([FFMPEG, "-y",
         "-f", "lavfi", "-i", f"color=c=0x0E1116:s={IG_W}x{IG_H}:d={duration:.2f}",
         "-i", wav,
         "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-shortest", out])

    wav.unlink(missing_ok=True)
    text_file.unlink(missing_ok=True)


def build_glimpse_clip(video_path: Path, out_path: Path, cfg: dict) -> Path:
    """hook (~8s) + one later highlight (~8s) + spoken CTA outro (~5s)."""
    check_ffmpeg()
    tmp = out_path.parent
    tmp.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(video_path)

    stem = out_path.stem
    hook = tmp / f"{stem}_hook.mp4"
    highlight = tmp / f"{stem}_highlight.mp4"
    cta = tmp / f"{stem}_cta.mp4"

    _extract_segment(video_path, 0.0, min(HOOK_SECONDS, duration), hook)
    parts = [hook]

    hi_start = _pick_highlight_start(duration)
    hi_dur = min(HIGHLIGHT_SECONDS, max(0.0, duration - hi_start))
    if hi_dur >= 2.0:
        _extract_segment(video_path, hi_start, hi_dur, highlight)
        parts.append(highlight)

    _build_cta_clip(cfg, cta)
    parts.append(cta)

    concat_list = tmp / f"{stem}_concat.txt"
    concat_list.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    # Re-encode here rather than -c copy: the CTA segment's audio comes from a
    # completely separate pipeline (fresh Piper TTS -> AAC) than the hook/
    # highlight segments (extracted from the fully mixed/mastered original
    # video), and stream-copying segments whose AAC frame boundaries don't
    # line up produces exactly the stuttering/repeated-syllable glitch
    # reported at the join into the outro ("for fo for fo... more co
    # cococococ"). Re-encoding rebuilds a clean, consistent audio stream
    # across the join instead of splicing incompatible frame boundaries.
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000", out_path])

    for p in [*parts, concat_list]:
        p.unlink(missing_ok=True)
    return out_path


# --------------------------------------------------------------------------- #
# cover image (Reels don't get a good auto-picked cover otherwise)
# --------------------------------------------------------------------------- #
def _extract_cover_frame(video_path: Path, dest: Path) -> Path:
    run([FFMPEG, "-y", "-ss", "2.5", "-i", video_path, "-vframes", "1",
         "-vf", _vertical_filter(), dest])
    return dest


def build_cover_image(video_path: Path, title: str, channel: str,
                      tags: list[str] | None, workdir: Path, out_jpg: Path) -> Path:
    """Same visual grammar as the YouTube thumbnail (category badge + big
    bold title over a dark-graded frame), laid out for a 9:16 Reels cover."""
    from PIL import Image, ImageDraw

    from .thumbnail import _category, _font, _wrap

    badge_text, accent = _category(title, tags or [])
    clean_title = re.sub(r"\s*#\w+\s*$", "", title).strip()

    frame = workdir / "_ig_cover_frame.jpg"
    _extract_cover_frame(video_path, frame)
    bg = Image.open(frame).convert("RGB")
    if bg.size != (IG_W, IG_H):
        bg = bg.resize((IG_W, IG_H))

    # dark grade from the lower third up, so white title text stays legible
    ov = Image.new("RGBA", (IG_W, IG_H), (0, 0, 0, 40))
    od = ImageDraw.Draw(ov)
    for y in range(IG_H):
        a = int(235 * (max(0, y - IG_H * 0.5) / (IG_H * 0.5)) ** 1.4)
        od.line([(0, y), (IG_W, y)], fill=(0, 0, 0, min(a, 235)))
    bg = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")
    draw = ImageDraw.Draw(bg)

    draw.rectangle([0, 0, IG_W, 14], fill=accent)

    bf = _font(40)
    bw = draw.textlength(badge_text, font=bf)
    draw.rectangle([50, 70, 50 + bw + 44, 70 + 62], fill=accent)
    draw.text((72, 82), badge_text, font=bf, fill=(15, 18, 26))

    size = 96
    font = _font(size)
    max_w = IG_W - 120
    lines = _wrap(draw, clean_title, font, max_w)
    while len(lines) > 4 and size > 50:
        size -= 8
        font = _font(size)
        lines = _wrap(draw, clean_title, font, max_w)

    lh = int(size * 1.15)
    y = IG_H - 220 - lh * len(lines)
    for line in lines:
        for dx in range(-4, 5, 2):
            for dy in range(-4, 5, 2):
                draw.text((60 + dx, y + dy), line, font=font, fill=(0, 0, 0))
        draw.text((60, y), line, font=font, fill=(255, 255, 255))
        y += lh

    if channel:
        cf = _font(30)
        draw.text((60, IG_H - 130), channel.upper(), font=cf, fill=(255, 255, 255))

    bg.save(out_jpg, quality=92)
    frame.unlink(missing_ok=True)
    return out_jpg


# --------------------------------------------------------------------------- #
# hosting the clip publicly (GitHub Release asset, same trick as the music lib)
# --------------------------------------------------------------------------- #
def _repo_slug() -> str:
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise PipelineError(f"gh repo view failed: {proc.stderr}")
    return proc.stdout.strip()


def _upload_to_release(files: list[Path], slug: str) -> dict[str, str]:
    tag = f"ig-{slug}"
    proc = subprocess.run(
        ["gh", "release", "create", tag, *[str(f) for f in files],
         "--title", f"Instagram glimpse: {slug}",
         "--notes", "Auto-generated Instagram Reels clip + cover, hosted here only so "
                     "Instagram's API can fetch them by URL. Not meant for direct viewing."],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise PipelineError(f"gh release create failed: {proc.stderr}")
    repo = _repo_slug()
    return {f.name: f"https://github.com/{repo}/releases/download/{tag}/{f.name}" for f in files}


def stage_glimpse(video_path: Path, slug: str, title: str, channel: str,
                  tags: list[str] | None, cfg: dict) -> dict | None:
    """Build + host the glimpse clip and its cover image. Never raises - a
    broken Instagram step must never take down a YouTube publish. Returns
    {"video_url": ..., "cover_url": ...}, or None."""
    try:
        tmp_dir = ROOT / "build" / "_ig_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        clip_path = tmp_dir / f"{slug}-glimpse.mp4"
        cover_path = tmp_dir / f"{slug}-cover.jpg"
        build_glimpse_clip(video_path, clip_path, cfg)
        build_cover_image(video_path, title, channel, tags, tmp_dir, cover_path)
        urls = _upload_to_release([clip_path, cover_path], slug)
        clip_path.unlink(missing_ok=True)
        cover_path.unlink(missing_ok=True)
        return {"video_url": urls[clip_path.name], "cover_url": urls[cover_path.name]}
    except Exception as e:  # noqa: BLE001
        print(f"[instagram] glimpse staging failed for {slug}: {e}")
        return None


# --------------------------------------------------------------------------- #
# Graph API posting
# --------------------------------------------------------------------------- #
def _ig_env() -> tuple[str, str]:
    return (os.environ.get("IG_USER_ID", "").strip(),
            os.environ.get("IG_ACCESS_TOKEN", "").strip())


def is_configured() -> bool:
    uid, token = _ig_env()
    return bool(uid and token)


def post_reel(video_url: str, caption: str, cover_url: str | None = None) -> str:
    uid, token = _ig_env()
    if not uid or not token:
        raise PipelineError("IG_USER_ID / IG_ACCESS_TOKEN not set - see INSTAGRAM_SETUP.md")

    data = {"media_type": "REELS", "video_url": video_url,
           "caption": caption, "access_token": token}
    if cover_url:
        data["cover_url"] = cover_url
    r = requests.post(f"{GRAPH}/{uid}/media", data=data, timeout=30)
    if not r.ok:
        raise PipelineError(f"Instagram container create failed ({r.status_code}): {r.text}")
    creation_id = r.json()["id"]

    for _ in range(30):
        time.sleep(10)
        s = requests.get(f"{GRAPH}/{creation_id}",
                         params={"fields": "status_code", "access_token": token}, timeout=15)
        if not s.ok:
            raise PipelineError(f"Instagram status check failed ({s.status_code}): {s.text}")
        status = s.json().get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise PipelineError(f"Instagram failed to process the video: {s.json()}")
    else:
        raise PipelineError("Instagram video processing timed out after 5 minutes")

    p = requests.post(f"{GRAPH}/{uid}/media_publish",
                      data={"creation_id": creation_id, "access_token": token}, timeout=30)
    if not p.ok:
        raise PipelineError(f"Instagram publish failed ({p.status_code}): {p.text}")
    return p.json()["id"]


def post_comment(media_id: str, message: str) -> str:
    _, token = _ig_env()
    r = requests.post(f"{GRAPH}/{media_id}/comments",
                      data={"message": message, "access_token": token}, timeout=30)
    if not r.ok:
        raise PipelineError(f"Instagram comment failed ({r.status_code}): {r.text}")
    return r.json()["id"]


def _build_caption(entry: dict, cfg: dict) -> str:
    social = cfg.get("social", {})
    handle = social.get("youtube_handle", "").strip()
    title = entry.get("title", "")
    lines = [title, "", "Full video on YouTube now — link in bio."]
    if handle:
        lines.append(f"Search {handle} on YouTube.")
    lines.append("More in the comments \U0001F447")
    return "\n".join(lines)


def run_post_due() -> int:
    if not is_configured():
        print("Instagram not configured (IG_USER_ID / IG_ACCESS_TOKEN missing) - skipping")
        return 0

    cfg = load_config()
    history = ROOT / "history.jsonl"
    if not history.exists():
        print("no history.jsonl")
        return 0

    raw_lines = history.read_text(encoding="utf-8").splitlines()
    entries: list[dict | None] = []
    for line in raw_lines:
        if not line.strip():
            entries.append(None)
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append(None)

    now = dt.datetime.now()
    changed = False
    for e in entries:
        if not e or e.get("instagram_posted") or not e.get("instagram_glimpse_url"):
            continue
        due_at = e.get("instagram_post_after")
        if not due_at:
            continue
        try:
            due = dt.datetime.fromisoformat(due_at)
        except ValueError:
            continue
        if now < due:
            continue
        changed = True
        try:
            media_id = post_reel(e["instagram_glimpse_url"], _build_caption(e, cfg),
                                 cover_url=e.get("instagram_cover_url"))
            e["instagram_posted"] = True
            e["instagram_media_id"] = media_id
            print(f"[instagram] posted {e.get('slug')} -> media {media_id}")
        except Exception as ex:  # noqa: BLE001
            e["instagram_last_error"] = str(ex)
            print(f"[instagram] FAILED to post {e.get('slug')}: {ex}")
            continue

        # Best-effort: a failed comment must never undo a successful post.
        youtube_url = cfg.get("social", {}).get("youtube_url", "").strip()
        if youtube_url:
            try:
                post_comment(media_id, f"Full video here: {youtube_url}")
                e["instagram_commented"] = True
            except Exception as ex:  # noqa: BLE001
                print(f"[instagram] posted {e.get('slug')} but comment failed: {ex}")

    if changed:
        out = "\n".join(json.dumps(e, ensure_ascii=False) if e else "" for e in entries)
        history.write_text(out + "\n", encoding="utf-8")
    else:
        print("nothing due")
    return 0


# --------------------------------------------------------------------------- #
def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    mode = sys.argv[1]
    if mode == "post-due":
        return run_post_due()
    if mode == "build" and len(sys.argv) >= 4:
        cfg = load_config()
        build_glimpse_clip(Path(sys.argv[2]), Path(sys.argv[3]), cfg)
        print(f"built {sys.argv[3]}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
