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


XFADE = 0.4  # seconds - overlap at each cut between hook/highlight/outro


def _crossfade_concat(parts: list[Path], out_path: Path) -> None:
    """Join clips with a short audio+video crossfade at each cut instead of
    a hard splice. The hook and highlight come from two different moments
    in the original video, each carrying whatever background music was
    already mixed in AT that point - a hard cut between them can jump
    between different musical moments and read as a jarring, out-of-sync
    edit. A crossfade doesn't change which music plays, it just blends the
    seam instead of cutting it."""
    durations = [ffprobe_duration(p) for p in parts]
    if len(parts) == 1:
        run([FFMPEG, "-y", "-i", parts[0], "-c", "copy", out_path])
        return

    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]

    filter_chain = []
    cur_v, cur_a = "0:v", "0:a"
    cum_dur = durations[0]
    n = len(parts)
    for i in range(1, n):
        offset = max(0.0, cum_dur - XFADE)
        out_v = f"v{i}" if i < n - 1 else "vout"
        out_a = f"a{i}" if i < n - 1 else "aout"
        filter_chain.append(
            f"[{cur_v}][{i}:v]xfade=transition=fade:duration={XFADE}:"
            f"offset={offset:.3f}[{out_v}]")
        filter_chain.append(f"[{cur_a}][{i}:a]acrossfade=d={XFADE}[{out_a}]")
        cur_v, cur_a = out_v, out_a
        cum_dur = offset + durations[i]

    run([FFMPEG, "-y", *inputs, "-filter_complex", ";".join(filter_chain),
         "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000", out_path])


def build_glimpse_clip(video_path: Path, out_path: Path, cfg: dict) -> Path:
    """hook (~8s) + one later highlight (~8s) + spoken CTA outro (~5s),
    crossfaded together rather than hard-cut."""
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
    if hi_dur >= 2.0 + XFADE:
        _extract_segment(video_path, hi_start, hi_dur, highlight)
        parts.append(highlight)

    _build_cta_clip(cfg, cta)
    parts.append(cta)

    _crossfade_concat(parts, out_path)

    for p in parts:
        p.unlink(missing_ok=True)
    return out_path


# --------------------------------------------------------------------------- #
# cover image (Reels don't get a good auto-picked cover otherwise)
# --------------------------------------------------------------------------- #


def build_cover_image(thumb_png: Path, out_jpg: Path) -> Path:
    """Reuse the already-built YouTube thumbnail (category badge + title
    already composited on a clean stock frame) rather than grabbing a new
    frame from the final rendered video - that video already has captions
    burned in by this point, and a frame grabbed from it collided with this
    function's own title text, producing garbled overlapping text. The
    thumbnail is landscape (1280x720); this letterbox-fits it onto the 9:16
    canvas with a blurred, darkened copy of itself filling the rest, instead
    of a hard crop that would cut off the badge/title."""
    from PIL import Image

    src = Image.open(thumb_png).convert("RGB")
    sw, sh = src.size

    # blurred cover-fill background: shrinking to a tiny size and blowing it
    # back up is a cheap, reliable blur with none of the blocky/grid-like
    # artifacts a real Gaussian blur left behind on a starfield background
    # (fine bright points against near-black amplify JPEG block artifacts
    # under a large-radius blur). A tiny source has no fine detail left to
    # produce that pattern once upscaled.
    scale = max(IG_W / sw, IG_H / sh)
    tiny = src.resize((max(1, round(sw * scale / 40)), max(1, round(sh * scale / 40))))
    bg = tiny.resize((round(sw * scale), round(sh * scale)), Image.BILINEAR)
    bx, by = (bg.width - IG_W) // 2, (bg.height - IG_H) // 2
    bg = bg.crop((bx, by, bx + IG_W, by + IG_H))
    bg = Image.eval(bg, lambda p: int(p * 0.5))

    # sharp foreground: fit-within (no cropping, so the badge/title survive
    # intact), centered vertically.
    fw = IG_W
    fh = round(sh * (IG_W / sw))
    fg = src.resize((fw, fh))
    bg.paste(fg, (0, (IG_H - fh) // 2))

    bg.save(out_jpg, quality=92)
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


def stage_glimpse(video_path: Path, slug: str, thumb_png: Path | None,
                  cfg: dict) -> dict | None:
    """Build + host the glimpse clip and its cover image. Never raises - a
    broken Instagram step must never take down a YouTube publish. Returns
    {"video_url": ..., "cover_url": ...}, or None."""
    try:
        tmp_dir = ROOT / "build" / "_ig_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        clip_path = tmp_dir / f"{slug}-glimpse.mp4"
        build_glimpse_clip(video_path, clip_path, cfg)
        files = [clip_path]

        cover_path = None
        if thumb_png and thumb_png.exists():
            cover_path = tmp_dir / f"{slug}-cover.jpg"
            build_cover_image(thumb_png, cover_path)
            files.append(cover_path)

        urls = _upload_to_release(files, slug)
        result = {"video_url": urls[clip_path.name]}
        clip_path.unlink(missing_ok=True)
        if cover_path:
            result["cover_url"] = urls[cover_path.name]
            cover_path.unlink(missing_ok=True)
        return result
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


def _create_container(uid: str, token: str, media_type: str, video_url: str,
                      caption: str, cover_url: str | None) -> str:
    data = {"media_type": media_type, "video_url": video_url,
           "caption": caption, "access_token": token}
    if cover_url:
        data["cover_url"] = cover_url
    r = requests.post(f"{GRAPH}/{uid}/media", data=data, timeout=30)
    if not r.ok:
        raise PipelineError(f"Instagram container create failed ({r.status_code}): {r.text}")
    return r.json()["id"]


def _wait_and_publish(uid: str, token: str, creation_id: str) -> str:
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


def post_reel(video_url: str, caption: str, cover_url: str | None = None) -> str:
    uid, token = _ig_env()
    if not uid or not token:
        raise PipelineError("IG_USER_ID / IG_ACCESS_TOKEN not set - see INSTAGRAM_SETUP.md")
    creation_id = _create_container(uid, token, "REELS", video_url, caption, cover_url)
    return _wait_and_publish(uid, token, creation_id)


# Standard Reels are capped at ~90s by the Content Publishing API. The
# storytelling posts run 3-4 minutes, so REELS gets tried first (keeps the
# Reels-tab placement/reach for anything short enough) and only falls back to
# plain feed VIDEO when Instagram rejects it specifically for being too long -
# any other error still raises normally rather than masking a real failure.
_LENGTH_REJECTION = re.compile(r"\b(too long|duration|maximum.*(?:length|seconds)|90 ?s)\b", re.I)


def post_video_or_reel(video_url: str, caption: str, cover_url: str | None = None) -> str:
    uid, token = _ig_env()
    if not uid or not token:
        raise PipelineError("IG_USER_ID / IG_ACCESS_TOKEN not set - see INSTAGRAM_SETUP.md")
    try:
        creation_id = _create_container(uid, token, "REELS", video_url, caption, cover_url)
    except PipelineError as e:
        if not _LENGTH_REJECTION.search(str(e)):
            raise
        creation_id = _create_container(uid, token, "VIDEO", video_url, caption, cover_url)
    return _wait_and_publish(uid, token, creation_id)


def post_comment(media_id: str, message: str) -> str:
    _, token = _ig_env()
    r = requests.post(f"{GRAPH}/{media_id}/comments",
                      data={"message": message, "access_token": token}, timeout=30)
    if not r.ok:
        raise PipelineError(f"Instagram comment failed ({r.status_code}): {r.text}")
    return r.json()["id"]


def _publish_full_video(video_path: Path, slug: str, caption: str,
                        thumb_png: Path | None, cfg: dict, tag_prefix: str) -> dict | None:
    """Host + post a full pre-rendered vertical video in one shot. Unlike
    stage_glimpse()/run_post_due(), there's no preceding YouTube upload to
    protect here - this IS the whole post (storytelling reels and the
    satisfying/soothing/fitness reels both use this) - so it stages, posts
    and comments synchronously. Never raises; returns fields to merge into
    the history.jsonl entry on success (including partial staging info on a
    posting failure, for debugging), or None if staging itself failed."""
    if not is_configured():
        print("[instagram] not configured - skipping post")
        return None
    try:
        files = [video_path]
        cover_path = None
        if thumb_png and thumb_png.exists():
            tmp_dir = ROOT / "build" / "_ig_tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            cover_path = tmp_dir / f"{slug}-cover.jpg"
            build_cover_image(thumb_png, cover_path)
            files.append(cover_path)
        urls = _upload_to_release(files, f"{tag_prefix}-{slug}")
        video_url = urls[video_path.name]
        cover_url = urls.get(cover_path.name) if cover_path else None
        if cover_path:
            cover_path.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        print(f"[instagram] staging failed for {slug}: {e}")
        return None

    result: dict = {"instagram_video_url": video_url}
    if cover_url:
        result["instagram_cover_url"] = cover_url
    try:
        media_id = post_video_or_reel(video_url, caption, cover_url=cover_url)
    except Exception as e:  # noqa: BLE001
        print(f"[instagram] FAILED to post {slug}: {e}")
        result["instagram_last_error"] = str(e)
        return result

    result["instagram_posted"] = True
    result["instagram_media_id"] = media_id

    youtube_url = cfg.get("social", {}).get("youtube_url", "").strip()
    if youtube_url:
        try:
            post_comment(media_id, f"More on the channel: {youtube_url}")
            result["instagram_commented"] = True
        except Exception as e:  # noqa: BLE001
            print(f"[instagram] posted {slug} but comment failed: {e}")
    return result


def publish_story(video_path: Path, slug: str, title: str, thumb_png: Path | None,
                  cfg: dict) -> dict | None:
    handle = cfg.get("social", {}).get("youtube_handle", "").strip()
    lines = [title, "", "An original short story, told in full - right here."]
    if handle:
        lines.append(f"More like this: {handle}")
    lines.append("Follow for a new one every day \U0001F447")
    return _publish_full_video(video_path, slug, "\n".join(lines), thumb_png, cfg, "story")


_REEL_TAGLINES = {
    "satisfying": "Oddly satisfying, for your feed ✨",
    "soothing": "A little calm for your day \U0001F343",
    "fitness": "Quick tip, done right \U0001F4AA",
}


def publish_reel(video_path: Path, slug: str, title: str, category: str,
                 thumb_png: Path | None, cfg: dict) -> dict | None:
    handle = cfg.get("social", {}).get("youtube_handle", "").strip()
    lines = [title, "", _REEL_TAGLINES.get(category, "")]
    if handle:
        lines.append(f"More like this: {handle}")
    lines.append("Follow for more \U0001F447")
    return _publish_full_video(video_path, slug, "\n".join(lines), thumb_png, cfg,
                               f"reel-{category}")


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
