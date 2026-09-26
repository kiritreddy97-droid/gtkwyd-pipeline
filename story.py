"""Fully-automated storytelling Reel: pick a premise, write an original short
story, render it as a vertical video, and post it straight to Instagram.

    .venv\\Scripts\\python story.py                one story, posted
    .venv\\Scripts\\python story.py --dry-run       render only, do not post
    .venv\\Scripts\\python story.py --status        topic queue summary

Unlike auto.py's YouTube path, a story is Instagram-only content: there is no
antecedent YouTube upload to protect, so this renders and posts synchronously
in one run instead of staging + waiting for a separate post-due pass.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from pipeline import ideas
from pipeline.util import BUILD_DIR, ROOT, load_config, slugify

TOPICS = ROOT / "topics-stories.txt"
GEN_DIR = ROOT / "scripts" / "_generated-stories"
HISTORY = ROOT / "history.jsonl"
LOG = ROOT / "story.log"
LOCK = ROOT / "story.lock"
PY = Path(sys.executable)

GEN_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _history() -> list[dict]:
    if not HISTORY.exists():
        return []
    out = []
    for ln in HISTORY.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    return out


def _record(entry: dict) -> None:
    with HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _posted_today() -> int:
    today = dt.date.today().isoformat()
    return sum(1 for e in _history()
               if e.get("ts", "").startswith(today) and e.get("format") == "story"
               and e.get("status") == "uploaded")


def run(slot: str, dry_run: bool) -> int:
    cfg = load_config()
    acfg = cfg.get("auto", {})
    if not acfg.get("enabled", True):
        log("[story] disabled in config (shares [auto].enabled with auto.py)")
        return 0

    if _posted_today() >= 1:
        log("[story] already posted today - skipping")
        return 0

    theme = ideas.next_topic(TOPICS)
    if not theme:
        log(f"[story] {TOPICS.name} exhausted - nothing to do")
        return 0

    model = acfg.get("ollama_model")
    from pipeline import writer
    try:
        md = writer.generate_story_script(
            theme, model=model or writer.DEFAULT_MODEL,
            self_review=bool(acfg.get("self_review", True)))
    except Exception as e:  # noqa: BLE001
        # Benign, expected variance (the local model doesn't always land a
        # guardrail-passing script in 3 tries) - matches auto.py's philosophy
        # that a writer miss is not a CI failure, just "nothing this round."
        log(f"[story] writer failed for {theme!r}: {e}")
        ideas.mark_used(theme, TOPICS)
        return 0
    ideas.mark_used(theme, TOPICS)

    from pipeline.script_parser import parse_script_text
    script = parse_script_text(md, fallback_title=theme)
    slug = slugify(script.title)[:60]
    script_path = GEN_DIR / f"{slug}.md"
    script_path.write_text(md, encoding="utf-8")

    log(f"[{slot}] rendering story {slug}  \"{script.title}\"")
    t0 = time.time()
    cmd = [str(PY), "make_video.py", str(script_path), "--portrait"]
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    render_secs = round(time.time() - t0)

    entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
             "slug": slug, "title": script.title, "format": "story",
             "origin": "ai", "slot": slot, "render_seconds": render_secs}

    if rc != 0:
        entry["status"] = "render_failed"
        _record(entry)
        log(f"[story] render failed (rc {rc}) for {slug}")
        return 1

    outdir = BUILD_DIR / slug
    video = outdir / f"{slug}.mp4"
    thumb = outdir / f"{slug}_thumbnail.png"

    if dry_run:
        entry["status"] = "rendered"
        _record(entry)
        log(f"[story] rendered only ({render_secs}s): {video}")
        return 0

    from pipeline import instagram
    posted = instagram.publish_story(
        video, slug, script.title, thumb if thumb.exists() else None, cfg)
    if posted:
        entry.update(posted)
        entry["status"] = "uploaded" if posted.get("instagram_posted") else "post_failed"
    else:
        entry["status"] = "post_failed"

    _record(entry)
    if entry["status"] == "uploaded":
        log(f"[story] POSTED {slug}  media {posted.get('instagram_media_id')}  "
            f"({render_secs}s)")
    else:
        log(f"[story] rendered but did not post: {slug}")
    return 0


def status() -> None:
    hist = [e for e in _history() if e.get("format") == "story"]
    up = [e for e in hist if e.get("status") == "uploaded"]
    print(f"stories posted total : {len(up)}   (today: {_posted_today()})")
    print(f"story premises left  : {ideas.remaining(TOPICS)}")
    for e in hist[-5:]:
        print(f"  {e['ts']}  {e.get('status','?'):14} {e.get('title','')}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="story")
    ap.add_argument("--slot", default="manual")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        status()
        return 0

    if LOCK.exists():
        age = time.time() - LOCK.stat().st_mtime
        if age < 3 * 3600:
            log(f"[{args.slot}] another run holds the lock ({age/60:.0f} min old) - exiting")
            return 0
        log(f"[{args.slot}] stale lock ({age/60:.0f} min old) - taking over")
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    try:
        return run(args.slot, args.dry_run)
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
