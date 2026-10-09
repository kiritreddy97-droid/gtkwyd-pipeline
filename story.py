"""Fully-automated storytelling Reel: pick a premise, write an original short
story, render it as a vertical video, run it past the QA gatekeeper
(pipeline.qa_gate), and post it straight to Instagram.

    .venv\\Scripts\\python story.py                one story, posted
    .venv\\Scripts\\python story.py --dry-run       render only, do not post
    .venv\\Scripts\\python story.py --status        topic queue summary

Unlike auto.py's YouTube path, a story is Instagram-only content: there is no
antecedent YouTube upload to protect, so this renders and posts synchronously
in one run instead of staging + waiting for a separate post-due pass.

QA gate: a rendered story is checked (visuals actually relate to the
narration, thumbnail looks clean) before it's posted. A failure regenerates
a fresh script/render, up to 3 attempts total; if it still hasn't passed,
today's slot is skipped (logged clearly) and the last attempt is held in
pipeline.qa_queue so the NEXT scheduled run tries it again first.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from pipeline import ideas, qa_gate, qa_queue
from pipeline.util import BUILD_DIR, ROOT, load_config, slugify

TOPICS = ROOT / "topics-stories.txt"
GEN_DIR = ROOT / "scripts" / "_generated-stories"
HISTORY = ROOT / "history.jsonl"
LOG = ROOT / "story.log"
LOCK = ROOT / "story.lock"
PY = Path(sys.executable)
MAX_ATTEMPTS = 3
QA_CATEGORY = "story"

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


def _generate_and_render(model: str | None, acfg: dict, slot: str) -> dict | None:
    theme = ideas.next_topic(TOPICS)
    if not theme:
        return None

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
        return {"ok": False}
    ideas.mark_used(theme, TOPICS)
    md = writer.with_style(md, "story")

    from pipeline.script_parser import parse_script_text
    script = parse_script_text(md, fallback_title=theme)
    # a short uniqueness suffix - see reels.py's identical fix for why: two
    # runs close together can land on the same slug for a similar/identical
    # title, and git can't auto-merge two different NEW files at one path.
    slug = slugify(script.title)[:50] + "-" + uuid.uuid4().hex[:6]
    script_path = GEN_DIR / f"{slug}.md"
    script_path.write_text(md, encoding="utf-8")

    log(f"[{slot}] rendering story {slug}  \"{script.title}\"")
    t0 = time.time()
    cmd = [str(PY), "make_video.py", str(script_path), "--portrait"]
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    render_secs = round(time.time() - t0)

    if rc != 0:
        log(f"[story] render failed (rc {rc}) for {slug}")
        return {"ok": False, "render_failed": True, "slug": slug,
                "title": script.title, "render_secs": render_secs}

    outdir = BUILD_DIR / slug
    video = outdir / f"{slug}.mp4"
    thumb = outdir / f"{slug}_thumbnail.png"
    narration = " ".join(sc.narration for sc in script.scenes)
    return {"ok": True, "slug": slug, "title": script.title, "video": video,
           "thumb": thumb if thumb.exists() else None, "narration": narration,
           "render_secs": render_secs}


def _load_held() -> dict | None:
    held = qa_queue.peek(QA_CATEGORY)
    if not held:
        return None
    video = Path(held["video"])
    if not video.exists():
        log("[story] held item's render is gone (build/ was cleaned) - "
            "discarding, generating fresh instead")
        qa_queue.pop(QA_CATEGORY)
        return None
    log("[story] retrying a held item from a previous failed QA pass first")
    thumb = Path(held["thumb"]) if held.get("thumb") else None
    return {"ok": True, "slug": held["slug"], "title": held["title"], "video": video,
           "thumb": thumb if thumb and thumb.exists() else None,
           "narration": held.get("narration", ""), "render_secs": 0}


def run(slot: str, dry_run: bool) -> int:
    cfg = load_config()
    acfg = cfg.get("auto", {})
    if not acfg.get("enabled", True):
        log("[story] disabled in config (shares [auto].enabled with auto.py)")
        return 0

    if _posted_today() >= 1:
        log("[story] already posted today - skipping")
        return 0

    model = acfg.get("ollama_model")
    result = _load_held()
    used_held = result is not None
    qa_issues: list[str] = []
    attempts = 0
    passed = False
    last_failed = None

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        if result is None:
            result = _generate_and_render(model, acfg, slot)
        if result is None:
            log(f"[story] {TOPICS.name} exhausted - nothing to do")
            return 0
        if result.get("render_failed"):
            entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                     "slug": result["slug"], "title": result["title"], "format": "story",
                     "origin": "ai", "slot": slot, "render_seconds": result["render_secs"],
                     "status": "render_failed"}
            _record(entry)
            return 1
        if not result.get("ok"):
            result = None
            continue

        if dry_run:
            log(f"[story] rendered only ({result['render_secs']}s): {result['video']}")
            entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                     "slug": result["slug"], "title": result["title"], "format": "story",
                     "origin": "ai", "slot": slot, "render_seconds": result["render_secs"],
                     "status": "rendered"}
            _record(entry)
            return 0

        passed, qa_issues = qa_gate.check_reel(result["video"], result["thumb"],
                                                result["narration"])
        if passed:
            break
        log(f"[story] QA FAILED (attempt {attempts}/{MAX_ATTEMPTS}) for "
            f"{result['slug']}: {' | '.join(qa_issues)}")
        last_failed = result
        used_held = False
        result = None

    if not passed:
        log(f"[story] failed QA after {MAX_ATTEMPTS} attempts - "
            f"skipping today's slot, held {last_failed['slug']} for the next run")
        qa_queue.hold(QA_CATEGORY, {
            "slug": last_failed["slug"], "title": last_failed["title"],
            "video": str(last_failed["video"]),
            "thumb": str(last_failed["thumb"]) if last_failed["thumb"] else None,
            "narration": last_failed["narration"],
        })
        entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
                 "slug": last_failed["slug"], "title": last_failed["title"], "format": "story",
                 "origin": "ai", "slot": slot, "status": "qa_held", "qa_issues": qa_issues}
        _record(entry)
        return 0

    if used_held:
        qa_queue.pop(QA_CATEGORY)

    from pipeline import instagram
    posted = instagram.publish_story(
        result["video"], result["slug"], result["title"], result["thumb"], cfg)

    entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
             "slug": result["slug"], "title": result["title"], "format": "story",
             "origin": "ai", "slot": slot, "render_seconds": result["render_secs"],
             "qa_issues": qa_issues}
    if posted:
        entry.update(posted)
        entry["status"] = "uploaded" if posted.get("instagram_posted") else "post_failed"
    else:
        entry["status"] = "post_failed"

    _record(entry)
    if entry["status"] == "uploaded":
        log(f"[story] POSTED {result['slug']}  media {posted.get('instagram_media_id')}  "
            f"({result['render_secs']}s)")
    else:
        log(f"[story] rendered but did not post: {result['slug']}")
    return 0


def status() -> None:
    hist = [e for e in _history() if e.get("format") == "story"]
    up = [e for e in hist if e.get("status") == "uploaded"]
    print(f"stories posted total : {len(up)}   (today: {_posted_today()})")
    print(f"story premises left  : {ideas.remaining(TOPICS)}")
    held = qa_queue.peek(QA_CATEGORY)
    if held:
        print(f"held for retry       : {held['slug']}")
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
