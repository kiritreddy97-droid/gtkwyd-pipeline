"""Fully-automated satisfying/soothing/fitness-wellness Reel: write a short
narrated script for one category, render it vertically, and post it straight
to Instagram (Instagram-only content, no YouTube upload).

    .venv\\Scripts\\python reels.py --category satisfying   one Reel, posted
    .venv\\Scripts\\python reels.py --category soothing --dry-run
    .venv\\Scripts\\python reels.py --status                 queues + history

Each category (satisfying / soothing / fitness) posts once a day, on its own
schedule slot in .github/workflows/reels.yml - not a rotation. --category is
required for a real (non-status) run; without it, --status still works and
a manual run falls back to whichever category has posted least so far.
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
from pipeline.writer import REEL_CATEGORIES

TOPICS = {
    "satisfying": ROOT / "topics-satisfying.txt",
    "soothing": ROOT / "topics-soothing.txt",
    "fitness": ROOT / "topics-fitness.txt",
}
GEN_DIR = ROOT / "scripts" / "_generated-reels"
HISTORY = ROOT / "history.jsonl"
LOG = ROOT / "reels.log"
LOCK = ROOT / "reels.lock"
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


def _least_posted_category(hist: list[dict]) -> str:
    """Fallback for a manual run with no --category: whichever category has
    the fewest uploaded entries so far, ties broken by REEL_CATEGORIES order."""
    counts = {c: 0 for c in REEL_CATEGORIES}
    for e in hist:
        if e.get("format") == "reel" and e.get("status") == "uploaded":
            c = e.get("category")
            if c in counts:
                counts[c] += 1
    return min(REEL_CATEGORIES, key=lambda c: counts[c])


def _posted_today(category: str) -> int:
    today = dt.date.today().isoformat()
    return sum(1 for e in _history()
               if e.get("ts", "").startswith(today) and e.get("format") == "reel"
               and e.get("category") == category and e.get("status") == "uploaded")


def run(slot: str, category: str | None, dry_run: bool) -> int:
    cfg = load_config()
    acfg = cfg.get("auto", {})
    if not acfg.get("enabled", True):
        log("[reels] disabled in config (shares [auto].enabled with auto.py)")
        return 0

    hist = _history()
    if not category:
        category = _least_posted_category(hist)
        log(f"[reels] no --category given - defaulting to {category!r} (least posted)")
    if category not in REEL_CATEGORIES:
        log(f"[reels] unknown category {category!r} (need one of {REEL_CATEGORIES})")
        return 1

    if _posted_today(category) >= 1:
        log(f"[reels] {category} already posted today - skipping")
        return 0

    topics_path = TOPICS[category]
    theme = ideas.next_topic(topics_path)
    if not theme:
        log(f"[reels] {topics_path.name} exhausted - nothing to do for {category!r}")
        return 0

    model = acfg.get("ollama_model")
    from pipeline import writer
    try:
        md = writer.generate_reel_script(
            theme, category, model=model or writer.DEFAULT_MODEL,
            self_review=bool(acfg.get("self_review", True)))
    except Exception as e:  # noqa: BLE001
        log(f"[reels] writer failed for {category} / {theme!r}: {e}")
        ideas.mark_used(theme, topics_path)
        return 1
    ideas.mark_used(theme, topics_path)

    from pipeline.script_parser import parse_script_text
    script = parse_script_text(md, fallback_title=theme)
    slug = slugify(f"{category}-{script.title}")[:60]
    script_path = GEN_DIR / f"{slug}.md"
    script_path.write_text(md, encoding="utf-8")

    log(f"[{slot}] rendering {category} reel {slug}  \"{script.title}\"")
    t0 = time.time()
    cmd = [str(PY), "make_video.py", str(script_path), "--portrait"]
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    render_secs = round(time.time() - t0)

    entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
             "slug": slug, "title": script.title, "format": "reel",
             "category": category, "origin": "ai", "slot": slot,
             "render_seconds": render_secs}

    if rc != 0:
        entry["status"] = "render_failed"
        _record(entry)
        log(f"[reels] render failed (rc {rc}) for {slug}")
        return 1

    outdir = BUILD_DIR / slug
    video = outdir / f"{slug}.mp4"
    thumb = outdir / f"{slug}_thumbnail.png"

    if dry_run:
        entry["status"] = "rendered"
        _record(entry)
        log(f"[reels] rendered only ({render_secs}s): {video}")
        return 0

    from pipeline import instagram
    posted = instagram.publish_reel(
        video, slug, script.title, category, thumb if thumb.exists() else None, cfg)
    if posted:
        entry.update(posted)
        entry["status"] = "uploaded" if posted.get("instagram_posted") else "post_failed"
    else:
        entry["status"] = "post_failed"

    _record(entry)
    if entry["status"] == "uploaded":
        log(f"[reels] POSTED {slug} ({category})  media {posted.get('instagram_media_id')}  "
            f"({render_secs}s)")
    else:
        log(f"[reels] rendered but did not post: {slug}")
    return 0


def status() -> None:
    hist = [e for e in _history() if e.get("format") == "reel"]
    up = [e for e in hist if e.get("status") == "uploaded"]
    print(f"reels posted total : {len(up)}")
    for cat, path in TOPICS.items():
        print(f"  {cat:10} today: {_posted_today(cat)}   themes left: {ideas.remaining(path)}")
    for e in hist[-6:]:
        print(f"  {e['ts']}  {e.get('category','?'):10} {e.get('status','?'):14} {e.get('title','')}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="reels")
    ap.add_argument("--slot", default="manual")
    ap.add_argument("--category", choices=REEL_CATEGORIES, default=None)
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
        return run(args.slot, args.category, args.dry_run)
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
