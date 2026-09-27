"""Fully-automated 'mind-blowing fact' image post: pulls the hook line from
the most recently uploaded YouTube video/Short, builds a bold static fact
card (pipeline/fact_card.py - not a video, a single feed image), runs it
past the QA gatekeeper (pipeline.qa_gate), and posts it to Instagram to
drive engagement/shares back to the full video.

    .venv\\Scripts\\python fact_post.py                one post
    .venv\\Scripts\\python fact_post.py --dry-run      build only, do not post

QA gate: the built card is checked (looks intentionally designed, text
legible and on-topic) before it's posted. A failure rebuilds with a
different palette, up to 3 attempts total; if it still hasn't passed,
today's slot is skipped (logged clearly) and the last attempt is held in
pipeline.qa_queue so the NEXT scheduled run tries it again first.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import requests

from pipeline import qa_gate, qa_queue
from pipeline.util import ROOT, load_config

HISTORY = ROOT / "history.jsonl"
LOG = ROOT / "fact_post.log"
MAX_ATTEMPTS = 3
QA_CATEGORY = "fact-image"


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _download_youtube_thumb(video_id: str, dest: Path) -> bool:
    """The card's background should look like it belongs to this specific
    video, not a blank branded gradient - YouTube always hosts a thumbnail at
    a predictable URL, no API key needed, so reuse that directly. Tries the
    high-res version first, falls back to the guaranteed-to-exist default."""
    for name in ("maxresdefault.jpg", "hqdefault.jpg"):
        url = f"https://img.youtube.com/vi/{video_id}/{name}"
        try:
            r = requests.get(url, timeout=15)
            if r.ok and len(r.content) > 2000:  # maxres 404s as a tiny placeholder image
                dest.write_bytes(r.content)
                return True
        except requests.RequestException:
            continue
    return False


def _pick_candidate() -> dict | None:
    """Most recent uploaded video/Short that has a hook and hasn't already
    had a fact-image post made from it."""
    if not HISTORY.exists():
        return None
    lines = HISTORY.read_text(encoding="utf-8").splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if not lines[i].strip():
            continue
        try:
            e = json.loads(lines[i])
        except json.JSONDecodeError:
            continue
        if (e.get("format") in ("video", "short") and e.get("status") == "uploaded"
                and e.get("hook") and not e.get("fact_image_posted")):
            return e
    return None


def _mark_posted(slug: str, media_id: str) -> None:
    if not HISTORY.exists():
        return
    lines = HISTORY.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if e.get("slug") == slug:
            e["fact_image_posted"] = True
            e["fact_image_media_id"] = media_id
            lines[i] = json.dumps(e, ensure_ascii=False)
            break
    HISTORY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_card(entry: dict, cfg: dict, seed_offset: int) -> dict:
    from pipeline import fact_card

    slug = entry["slug"]
    hook = entry["hook"]
    title = entry["title"]
    channel = cfg.get("project", {}).get("channel_name", "Get To Know What You Don't")

    tmp_dir = ROOT / "build" / "_fact_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    img_path = tmp_dir / f"{slug}-fact.jpg"

    bg_path = None
    if entry.get("video_id"):
        candidate = tmp_dir / f"{slug}-bg.jpg"
        if _download_youtube_thumb(entry["video_id"], candidate):
            bg_path = candidate
            log(f"[fact] using YouTube thumbnail as background for {slug}")
        else:
            log(f"[fact] could not fetch YouTube thumbnail for {slug} - falling back to gradient")

    seed = abs(hash(slug)) + seed_offset
    fact_card.generate(hook, title, channel, img_path, seed=seed, bg_image_path=bg_path)
    if bg_path:
        bg_path.unlink(missing_ok=True)
    log(f"[fact] built card for {slug}: {img_path}")
    return {"slug": slug, "hook": hook, "title": title, "img_path": img_path,
           "video_id": entry.get("video_id", "")}


def _load_held() -> dict | None:
    held = qa_queue.peek(QA_CATEGORY)
    if not held:
        return None
    img_path = Path(held["img_path"])
    if not img_path.exists():
        log("[fact] held item's image is gone (build/ was cleaned) - "
            "discarding, generating fresh instead")
        qa_queue.pop(QA_CATEGORY)
        return None
    log("[fact] retrying a held item from a previous failed QA pass first")
    return {"slug": held["slug"], "hook": held["hook"], "title": held["title"],
           "img_path": img_path, "video_id": held.get("video_id", "")}


def run(dry_run: bool) -> int:
    cfg = load_config()
    if not cfg.get("auto", {}).get("enabled", True):
        log("[fact] disabled in config (shares [auto].enabled with auto.py)")
        return 0

    result = _load_held()
    used_held = result is not None
    entry = None
    if not used_held:
        entry = _pick_candidate()
        if entry is None:
            log("[fact] no eligible uploaded video/Short with a hook found - nothing to do")
            return 0

    qa_issues: list[str] = []
    attempts = 0
    passed = False
    last_failed = None

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        if result is None:
            result = _build_card(entry, cfg, seed_offset=attempts - 1)

        if dry_run:
            log("[fact] dry-run - not posting")
            return 0

        passed, qa_issues = qa_gate.check_image_post(result["img_path"], result["hook"])
        if passed:
            break
        log(f"[fact] QA FAILED (attempt {attempts}/{MAX_ATTEMPTS}) for "
            f"{result['slug']}: {' | '.join(qa_issues)}")
        last_failed = result
        used_held = False
        result = None

    if not passed:
        log(f"[fact] failed QA after {MAX_ATTEMPTS} attempts - "
            f"skipping today's slot, held {last_failed['slug']} for the next run")
        qa_queue.hold(QA_CATEGORY, {
            "slug": last_failed["slug"], "hook": last_failed["hook"],
            "title": last_failed["title"], "img_path": str(last_failed["img_path"]),
            "video_id": last_failed["video_id"],
        })
        return 0

    if used_held:
        qa_queue.pop(QA_CATEGORY)

    from pipeline import instagram

    video_url = f"https://youtu.be/{result['video_id']}" if result["video_id"] else ""
    caption_lines = [f"\U0001F92F {result['hook']}", "", result["title"]]
    if video_url:
        caption_lines += ["", f"Full video: {video_url}"]
    caption_lines += ["", "Link in bio too. Follow for more \U0001F447"]
    caption = "\n".join(caption_lines)

    posted = instagram.publish_fact_image(result["img_path"], result["slug"], caption, cfg)
    result["img_path"].unlink(missing_ok=True)

    if not (posted and posted.get("instagram_posted")):
        log(f"[fact] failed to post fact image for {result['slug']} - will retry next run")
        return 1

    _mark_posted(result["slug"], posted.get("instagram_media_id"))
    log(f"[fact] POSTED fact image for {result['slug']}  "
        f"media {posted.get('instagram_media_id')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="fact_post")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
