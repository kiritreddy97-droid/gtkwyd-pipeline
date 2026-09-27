"""Fully-automated satisfying/soothing/fitness-wellness Reel: write a short
narrated script for one category, render it vertically, run it past the QA
gatekeeper (pipeline.qa_gate), and post it straight to Instagram
(Instagram-only content, no YouTube upload).

    .venv\\Scripts\\python reels.py --category satisfying   one Reel, posted
    .venv\\Scripts\\python reels.py --category soothing --dry-run
    .venv\\Scripts\\python reels.py --status                 queues + history

Each category (satisfying / soothing / fitness) posts once a day, on its own
schedule slot in .github/workflows/reels.yml - not a rotation. --category is
required for a real (non-status) run; without it, --status still works and
a manual run falls back to whichever category has posted least so far.

QA gate: a rendered Reel is checked (visuals actually relate to the
narration, thumbnail looks clean) before it's posted. A failure regenerates
a fresh script/render, up to 3 attempts total; if it still hasn't passed,
this slot is skipped for today (logged clearly) and the last attempt is
held in pipeline.qa_queue so the NEXT scheduled run for this category tries
it again first, instead of losing it.
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

from pipeline import ideas, qa_gate, qa_queue
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
MAX_ATTEMPTS = 3

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


def _generate_and_render(category: str, topics_path: Path, model: str | None,
                         acfg: dict, slot: str) -> dict | None:
    """One attempt: pick a theme, write a script, render it. Returns a dict
    describing the outcome, or None if there was no theme left to try."""
    theme = ideas.next_topic(topics_path)
    if not theme:
        return None

    from pipeline import writer
    try:
        md = writer.generate_reel_script(
            theme, category, model=model or writer.DEFAULT_MODEL,
            self_review=bool(acfg.get("self_review", True)))
    except Exception as e:  # noqa: BLE001
        # Benign, expected variance (the local model doesn't always land a
        # guardrail-passing script in 3 tries) - matches auto.py's philosophy
        # that a writer miss is not a CI failure, just "nothing this round."
        log(f"[reels] writer failed for {category} / {theme!r}: {e}")
        ideas.mark_used(theme, topics_path)
        return {"ok": False}
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

    if rc != 0:
        log(f"[reels] render failed (rc {rc}) for {slug}")
        return {"ok": False, "render_failed": True, "slug": slug,
                "title": script.title, "render_secs": render_secs}

    outdir = BUILD_DIR / slug
    video = outdir / f"{slug}.mp4"
    thumb = outdir / f"{slug}_thumbnail.png"
    narration = " ".join(sc.narration for sc in script.scenes)
    return {"ok": True, "slug": slug, "title": script.title, "video": video,
           "thumb": thumb if thumb.exists() else None, "narration": narration,
           "render_secs": render_secs}


def _load_held(category: str) -> dict | None:
    held = qa_queue.peek(category)
    if not held:
        return None
    video = Path(held["video"])
    if not video.exists():
        log(f"[reels] held {category} item's render is gone (build/ was cleaned) - "
            f"discarding, generating fresh instead")
        qa_queue.pop(category)
        return None
    log(f"[reels] retrying a held {category} item from a previous failed QA pass first")
    thumb = Path(held["thumb"]) if held.get("thumb") else None
    return {"ok": True, "slug": held["slug"], "title": held["title"], "video": video,
           "thumb": thumb if thumb and thumb.exists() else None,
           "narration": held.get("narration", ""), "render_secs": 0}


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

    model = acfg.get("ollama_model")
    topics_path = TOPICS[category]

    result = _load_held(category)
    used_held = result is not None
    qa_issues: list[str] = []
    attempts = 0
    passed = False

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        if result is None:
            result = _generate_and_render(category, topics_path, model, acfg, slot)
        if result is None:
            log(f"[reels] {topics_path.name} exhausted - nothing to do for {category!r}")
            return 0
        if result.get("render_failed"):
            entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                     "slug": result["slug"], "title": result["title"], "format": "reel",
                     "category": category, "origin": "ai", "slot": slot,
                     "render_seconds": result["render_secs"], "status": "render_failed"}
            _record(entry)
            return 1
        if not result.get("ok"):
            result = None  # writer miss - try another theme this same attempt budget
            continue

        if dry_run:
            log(f"[reels] rendered only ({result['render_secs']}s): {result['video']}")
            entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                     "slug": result["slug"], "title": result["title"], "format": "reel",
                     "category": category, "origin": "ai", "slot": slot,
                     "render_seconds": result["render_secs"], "status": "rendered"}
            _record(entry)
            return 0

        passed, qa_issues = qa_gate.check_reel(result["video"], result["thumb"],
                                                result["narration"])
        if passed:
            break
        log(f"[reels] QA FAILED (attempt {attempts}/{MAX_ATTEMPTS}) for "
            f"{result['slug']}: {' | '.join(qa_issues)}")
        last_failed = result
        used_held = False
        result = None

    if not passed:
        # exhausted every attempt without a pass - skip this slot rather than
        # post something flagged, but hold the last attempt so the NEXT
        # scheduled run for this category tries it again first.
        log(f"[reels] {category} failed QA after {MAX_ATTEMPTS} attempts - "
            f"skipping this slot, held {last_failed['slug']} for the next {category} run")
        qa_queue.hold(category, {
            "slug": last_failed["slug"], "title": last_failed["title"],
            "video": str(last_failed["video"]),
            "thumb": str(last_failed["thumb"]) if last_failed["thumb"] else None,
            "narration": last_failed["narration"],
        })
        entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                 "slug": last_failed["slug"], "title": last_failed["title"], "format": "reel",
                 "category": category, "origin": "ai", "slot": slot, "status": "qa_held",
                 "qa_issues": qa_issues}
        _record(entry)
        return 0

    if used_held:
        qa_queue.pop(category)

    from pipeline import instagram
    posted = instagram.publish_reel(
        result["video"], result["slug"], result["title"], category, result["thumb"], cfg)

    entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
             "slug": result["slug"], "title": result["title"], "format": "reel",
             "category": category, "origin": "ai", "slot": slot,
             "render_seconds": result["render_secs"], "qa_issues": qa_issues}
    if posted:
        entry.update(posted)
        entry["status"] = "uploaded" if posted.get("instagram_posted") else "post_failed"
    else:
        entry["status"] = "post_failed"

    _record(entry)
    if entry["status"] == "uploaded":
        log(f"[reels] POSTED {result['slug']} ({category})  "
            f"media {posted.get('instagram_media_id')}  ({result['render_secs']}s)")
    else:
        log(f"[reels] rendered but did not post: {result['slug']}")
    return 0


def status() -> None:
    hist = [e for e in _history() if e.get("format") == "reel"]
    up = [e for e in hist if e.get("status") == "uploaded"]
    print(f"reels posted total : {len(up)}")
    for cat, path in TOPICS.items():
        held = qa_queue.peek(cat)
        held_note = f"  (held: {held['slug']})" if held else ""
        print(f"  {cat:10} today: {_posted_today(cat)}   themes left: {ideas.remaining(path)}{held_note}")
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
