"""Fully-automated 'mind-blowing fact' image post: pulls the hook line from
the most recently uploaded YouTube video/Short, builds a bold static fact
card (pipeline/fact_card.py - not a video, a single feed image), and posts it
to Instagram to drive engagement/shares back to the full video.

    .venv\\Scripts\\python fact_post.py                one post
    .venv\\Scripts\\python fact_post.py --dry-run      build only, do not post
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from pipeline.util import ROOT, load_config

HISTORY = ROOT / "history.jsonl"
LOG = ROOT / "fact_post.log"


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _pick_candidate() -> tuple[int, dict] | tuple[None, None]:
    """Most recent uploaded video/Short that has a hook and hasn't already
    had a fact-image post made from it. Returns (line_index, entry)."""
    if not HISTORY.exists():
        return None, None
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
            return i, e
    return None, None


def run(dry_run: bool) -> int:
    cfg = load_config()
    if not cfg.get("auto", {}).get("enabled", True):
        log("[fact] disabled in config (shares [auto].enabled with auto.py)")
        return 0

    idx, entry = _pick_candidate()
    if entry is None:
        log("[fact] no eligible uploaded video/Short with a hook found - nothing to do")
        return 0

    from pipeline import fact_card, instagram

    slug = entry["slug"]
    hook = entry["hook"]
    title = entry["title"]
    channel = cfg.get("project", {}).get("channel_name", "Get To Know What You Don't")

    tmp_dir = ROOT / "build" / "_fact_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    img_path = tmp_dir / f"{slug}-fact.jpg"
    fact_card.generate(hook, title, channel, img_path, seed=abs(hash(slug)))
    log(f"[fact] built card for {slug}: {img_path}")

    if dry_run:
        log("[fact] dry-run - not posting")
        return 0

    video_url = f"https://youtu.be/{entry['video_id']}" if entry.get("video_id") else ""
    caption_lines = [f"\U0001F92F {hook}", "", title]
    if video_url:
        caption_lines += ["", f"Full video: {video_url}"]
    caption_lines += ["", "Link in bio too. Follow for more \U0001F447"]
    caption = "\n".join(caption_lines)

    posted = instagram.publish_fact_image(img_path, slug, caption, cfg)
    img_path.unlink(missing_ok=True)

    if not (posted and posted.get("instagram_posted")):
        log(f"[fact] failed to post fact image for {slug} - will retry next run")
        return 1

    lines = HISTORY.read_text(encoding="utf-8").splitlines()
    entry["fact_image_posted"] = True
    entry["fact_image_media_id"] = posted.get("instagram_media_id")
    lines[idx] = json.dumps(entry, ensure_ascii=False)
    HISTORY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"[fact] POSTED fact image for {slug}  media {entry['fact_image_media_id']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="fact_post")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
