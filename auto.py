"""Fully-automated single run: pick content, render it, upload it, archive it.

    .venv\\Scripts\\python auto.py --format video               one long video
    .venv\\Scripts\\python auto.py --format short                one Short
    .venv\\Scripts\\python auto.py --format video --source news  a science-news explainer
    .venv\\Scripts\\python auto.py --status                      queue / history summary
    .venv\\Scripts\\python auto.py --format video --dry-run      render, do not upload

Content selection
  video : scripts/ready/  ->  [news, if --source news]  ->  scripts/bank/  ->  AI (gated)
  short : scripts/shorts-ready/  ->  scripts/shorts-bank/  ->  AI shorts (gated)

AI-written scripts (including news) land in scripts/review/ or scripts/shorts-review/
and are NOT published unless [auto] require_review_for_ai = false.
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

from pipeline import guardrails, ideas
from pipeline.script_parser import parse_script
from pipeline.util import BUILD_DIR, ROOT, load_config, slugify

DIRS = {
    "video": {"ready": ROOT / "scripts" / "ready",
              "bank": ROOT / "scripts" / "bank",
              "review": ROOT / "scripts" / "review",
              "gen": ROOT / "scripts" / "_generated",
              "topics": ROOT / "topics.txt"},
    "short": {"ready": ROOT / "scripts" / "shorts-ready",
              "bank": ROOT / "scripts" / "shorts-bank",
              "review": ROOT / "scripts" / "shorts-review",
              "gen": ROOT / "scripts" / "_generated-shorts",
              "topics": ROOT / "topics-shorts.txt"},
}
PUBLISHED = ROOT / "scripts" / "published"
HISTORY = ROOT / "history.jsonl"
LOG = ROOT / "auto.log"
LOCK = ROOT / "auto.lock"
# Re-exec with whatever interpreter is running this file: the local venv on
# Windows, or plain "python" on the GitHub Actions Linux runner (no venv there).
PY = Path(sys.executable)

for fmt in DIRS.values():
    for k in ("ready", "bank", "review", "gen"):
        fmt[k].mkdir(parents=True, exist_ok=True)
PUBLISHED.mkdir(parents=True, exist_ok=True)


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


def _count_today() -> int:
    today = dt.date.today().isoformat()
    return sum(1 for e in _history()
               if e.get("ts", "").startswith(today) and e.get("status") == "uploaded")


def _oldest_md(folder: Path, exclude: set[str]) -> Path | None:
    cands = sorted((p for p in folder.glob("*.md") if slugify(p.stem) not in exclude),
                   key=lambda p: (p.stat().st_mtime, p.name))
    return cands[0] if cands else None


# --------------------------------------------------------------------------- #
def pick(fmt: str, source: str, cfg: dict) -> tuple[Path, str] | None:
    d = DIRS[fmt]
    hist = _history()
    done = {e.get("slug", "") for e in hist}
    acfg = cfg.get("auto", {})
    gate = bool(acfg.get("require_review_for_ai", True))
    model = acfg.get("ollama_model")

    hand = _oldest_md(d["ready"], done)
    if hand:
        return hand, "ready"

    # science-news explainer (video only, when asked)
    if fmt == "video" and source == "news":
        try:
            from pipeline import news, writer
            used_links = {e.get("news_link", "") for e in hist}
            used_titles = {e.get("news_title", "") for e in hist}
            item = news.pick(used_links, used_titles)
            if item:
                md = writer.generate_news_script(item.title, item.summary,
                                                 item.source, model=model or writer.DEFAULT_MODEL)
                slug = slugify(item.title)[:60]
                dest = (d["review"] if gate else d["gen"]) / f"news-{slug}.md"
                dest.write_text(md, encoding="utf-8")
                if gate:
                    log(f"news script -> {dest.relative_to(ROOT)} (awaiting your review)")
                else:
                    return dest, "news"
            else:
                log("no fresh, on-theme news item found - using bank")
        except Exception as e:  # noqa: BLE001
            log(f"news path failed ({e}) - using bank")

    bank = _oldest_md(d["bank"], done)
    if bank:
        return bank, "bank"

    # AI fallback from the topic queue
    topic = ideas.next_topic(d["topics"])
    if not topic:
        log(f"[{fmt}] bank empty and {d['topics'].name} exhausted - nothing to do")
        return None
    try:
        from pipeline import writer
        md = writer.generate_script(topic, model=model or writer.DEFAULT_MODEL,
                                    self_review=bool(acfg.get("self_review", True)),
                                    kind=fmt)
    except Exception as e:  # noqa: BLE001
        log(f"AI writer failed for {topic!r}: {e}")
        ideas.mark_used(topic, d["topics"])
        return None
    slug = slugify(topic)
    ideas.mark_used(topic, d["topics"])
    if gate:
        (d["review"] / f"{slug}.md").write_text(md, encoding="utf-8")
        log(f"AI script for {topic!r} -> {fmt} review queue (move to "
            f"{d['ready'].relative_to(ROOT)} to publish)")
        return None
    dest = d["gen"] / f"{slug}.md"
    dest.write_text(md, encoding="utf-8")
    return dest, "ai"


# --------------------------------------------------------------------------- #
def run(slot: str, fmt: str, source: str, dry_run: bool) -> int:
    cfg = load_config()
    acfg = cfg.get("auto", {})
    if not acfg.get("enabled", True):
        log("[auto] disabled in config"); return 0

    cap = int(acfg.get("max_per_day", 6))
    if _count_today() >= cap:
        log(f"daily cap reached ({cap})"); return 0

    picked = pick(fmt, source, cfg)
    if not picked:
        return 0
    script_path, origin = picked
    script = parse_script(script_path)
    slug = slugify(script_path.stem)

    if origin in ("ai", "news"):
        ok, why = guardrails.check_title(script.title)
        if not ok:
            log(f"guardrail blocked {slug} ({why})"); return 1

    log(f"[{slot}] rendering {slug}  ({fmt}, {origin})  \"{script.title}\"")
    t0 = time.time()
    cmd = [str(PY), "make_video.py", str(script_path)]
    if fmt == "short":
        cmd.append("--short")
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    render_secs = round(time.time() - t0)

    entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
             "slug": slug, "title": script.title, "format": fmt,
             "origin": origin, "slot": slot, "render_seconds": render_secs}
    if origin == "news":
        entry["news_title"] = script.title

    if rc != 0:
        entry["status"] = "render_failed"
        _record(entry)
        log(f"render failed (rc {rc}) for {slug}")
        return 1

    outdir = BUILD_DIR / slug
    meta = json.loads((outdir / f"{slug}.meta.json").read_text(encoding="utf-8"))
    video = outdir / f"{slug}.mp4"
    thumb = outdir / f"{slug}_thumbnail.png"
    entry["title"] = meta["title"]
    entry["is_short"] = meta.get("is_short", fmt == "short")

    if dry_run or not acfg.get("upload", True):
        entry["status"] = "rendered"
        _record(entry)
        log(f"rendered only ({render_secs}s): {video}")
        return 0

    from pipeline import youtube
    visibility = acfg.get("visibility", "unlisted")
    try:
        vid = youtube.upload(
            video, title=meta["title"], description=meta["description"],
            tags=meta["tags"], privacy=visibility, made_for_kids=False,
            thumbnail=thumb if (thumb.exists() and not meta.get("is_short")) else None,
            playlist_id=acfg.get("playlist_id") or None,
        )
    except Exception as e:  # noqa: BLE001
        entry["status"] = "upload_failed"
        entry["error"] = str(e)
        _record(entry)
        log(f"upload failed for {slug}: {e}")
        return 1

    entry["status"] = "uploaded"
    entry["video_id"] = vid
    entry["visibility"] = visibility
    _record(entry)
    try:
        script_path.replace(PUBLISHED / f"{fmt}-{script_path.name}")
    except OSError:
        pass
    log(f"UPLOADED {slug}  https://youtu.be/{vid}  ({fmt}, {visibility}, {render_secs}s)")
    return 0


def status() -> None:
    hist = _history()
    up = [e for e in hist if e.get("status") == "uploaded"]
    print(f"uploaded total     : {len(up)}   (today: {_count_today()})")
    for fmt in ("video", "short"):
        d = DIRS[fmt]
        print(f"{fmt:6} bank / ready / review : "
              f"{len(list(d['bank'].glob('*.md')))} / "
              f"{len(list(d['ready'].glob('*.md')))} / "
              f"{len(list(d['review'].glob('*.md')))}")
    print(f"video topics left  : {ideas.remaining(DIRS['video']['topics'])}")
    print(f"short topics left  : {ideas.remaining(DIRS['short']['topics'])}")
    for e in up[-8:]:
        print(f"  {e['ts']}  {e.get('format','?'):5} {e.get('visibility','?'):8} {e['title']}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="auto")
    ap.add_argument("--slot", default="manual")
    ap.add_argument("--format", choices=["video", "short"], default="video")
    ap.add_argument("--source", choices=["auto", "news", "bank"], default="auto")
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
        return run(args.slot, args.format, args.source, args.dry_run)
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
