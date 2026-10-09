"""Daily Telugu history-story channel: one ~10-minute narrated story and one
~60-second Short, written by Gemini, voiced by the Edge Telugu neural voices,
illustrated with stock footage, uploaded to its own YouTube channel.

    python telugu.py --kind long        write + render + upload today's story,
                                        and queue its Short for the next short slot
    python telugu.py --kind short       render + upload the next queued Short
    python telugu.py --kind long --dry-run   render only, do not upload
    python telugu.py --status

Needs GEMINI_API_KEY (free, aistudio.google.com). Without it every run is a
logged no-op, never a failure. Uploads need youtube_token_te.json (authorise the
Telugu channel once with  python -m pipeline.youtube --auth --token
youtube_token_te.json); without it the video is rendered but not uploaded.

State lives in history-telugu.jsonl (kept apart from history.jsonl so the English
channel's daily cap and health checks never see these entries).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline import ideas, writer_te
from pipeline.util import BUILD_DIR, ROOT, load_config, slugify

TOPICS = ROOT / "topics-telugu-history.txt"
LONG_DIR = ROOT / "scripts" / "_generated-telugu"
SHORT_QUEUE = ROOT / "scripts" / "telugu-shorts-queue"
PUBLISHED = ROOT / "scripts" / "published"
HISTORY = ROOT / "history-telugu.jsonl"
LOG = ROOT / "telugu.log"
TOKEN = ROOT / "youtube_token_te.json"
PY = Path(sys.executable)
LOW_TOPICS = 10

for _d in (LONG_DIR, SHORT_QUEUE, PUBLISHED):
    _d.mkdir(parents=True, exist_ok=True)


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
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


ET = ZoneInfo("America/New_York")


def _et_date(ts: str) -> str:
    """Calendar date in US Eastern for a recorded timestamp (naive = UTC)."""
    t = dt.datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(ET).date().isoformat()


def _uploaded_today(fmt: str) -> bool:
    """'Today' is the US Eastern calendar day, so the once-a-day guard lines up
    with the 12pm ET schedule year-round (including across DST changes)."""
    today = dt.datetime.now(ET).date().isoformat()
    return any(e.get("ts") and _et_date(e["ts"]) == today and e.get("format") == fmt
               and e.get("status") == "uploaded" for e in _history())


def _refill_topics_if_low(model: str) -> None:
    if ideas.remaining(TOPICS) >= LOW_TOPICS or not writer_te.available():
        return
    try:
        existing = [ln.strip() for ln in TOPICS.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")]
        fresh = writer_te.new_topics(existing, 40, model)
    except Exception as e:  # noqa: BLE001 - a refill miss must not stop today's run
        log(f"[telugu] topic refill failed: {e}")
        return
    if fresh:
        with TOPICS.open("a", encoding="utf-8") as fh:
            fh.write(f"\n# auto-added {dt.date.today().isoformat()}\n")
            fh.write("\n".join(fresh) + "\n")
        log(f"[telugu] topic queue was low - added {len(fresh)} new story topics")


def _render(script_path: Path, short: bool) -> tuple[int, int]:
    cmd = [str(PY), "make_video.py", str(script_path)]
    if short:
        cmd.append("--short")
    t0 = time.time()
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    return rc, round(time.time() - t0)


def _finish(script_path: Path, fmt: str, tcfg: dict, dry_run: bool, entry: dict) -> int:
    """Render, upload and record one Telugu video. Returns a process exit code."""
    slug = slugify(script_path.stem)
    short = fmt == "te-short"
    log(f"[{fmt}] rendering {slug}  \"{entry.get('title', '')}\"")
    rc, secs = _render(script_path, short)
    entry["render_seconds"] = secs
    if rc != 0:
        entry["status"] = "render_failed"
        _record(entry)
        log(f"[{fmt}] render failed (rc {rc}) for {slug}")
        return 1

    outdir = BUILD_DIR / slug
    meta = json.loads((outdir / f"{slug}.meta.json").read_text(encoding="utf-8"))
    video = outdir / f"{slug}.mp4"
    thumb = outdir / f"{slug}_thumbnail.png"
    entry["title"] = meta["title"]

    if dry_run:
        entry["status"] = "rendered"
        _record(entry)
        log(f"[{fmt}] rendered only ({secs}s): {video}")
        return 0
    if not TOKEN.exists():
        entry["status"] = "rendered_no_token"
        _record(entry)
        log(f"[{fmt}] rendered, but the Telugu channel is not authorised yet "
            f"(no youtube_token_te.json) - nothing uploaded")
        return 0

    from pipeline import youtube
    visibility = tcfg.get("visibility", "unlisted")
    try:
        vid = youtube.upload(
            video, title=meta["title"], description=meta["description"],
            tags=meta["tags"], privacy=visibility, made_for_kids=False,
            thumbnail=thumb if (thumb.exists() and not short) else None,
            playlist_id=tcfg.get("playlist_id") or None,
            token_path=TOKEN, language="te")
    except Exception as e:  # noqa: BLE001
        entry["status"] = "upload_failed"
        entry["error"] = str(e)
        _record(entry)
        log(f"[{fmt}] upload failed for {slug}: {e}")
        return 1

    entry.update(status="uploaded", video_id=vid, visibility=visibility)
    _record(entry)
    try:
        script_path.replace(PUBLISHED / f"{fmt}-{script_path.name}")
    except OSError:
        pass
    log(f"[{fmt}] UPLOADED {slug}  https://youtu.be/{vid}  ({visibility}, {secs}s)")
    return 0


def _new_entry(fmt: str, slug: str, title: str, title_en: str, topic: str, hook: str) -> dict:
    return {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "slug": slug,
            "title": title, "title_en": title_en, "topic": topic, "format": fmt,
            "lang": "te", "hook": hook}


def run_long(dry_run: bool) -> int:
    cfg = load_config()
    tcfg = cfg.get("telugu", {})
    if not tcfg.get("enabled", True):
        log("[te-video] disabled in config"); return 0
    if not writer_te.available():
        log("[te-video] GEMINI_API_KEY not set - skipping (add it as a repo secret)"); return 0
    if _uploaded_today("te-video") and not dry_run:
        log("[te-video] already uploaded today - skipping"); return 0

    model = tcfg.get("gemini_model", writer_te.DEFAULT_MODEL)
    _refill_topics_if_low(model)
    topic = ideas.next_topic(TOPICS)
    if not topic:
        log("[te-video] no story topics left and none could be generated"); return 0

    try:
        data = writer_te.generate_long(topic, model)
    except writer_te.GeminiUnavailable as e:
        # key/quota/outage - not the story's fault, so the topic stays in the queue
        log(f"[te-video] Gemini unavailable, topic kept for the next run: {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        ideas.mark_used(topic, TOPICS)
        log(f"[te-video] writer failed for {topic!r}: {e}")
        return 0  # a content miss is "nothing this round", not a CI failure
    ideas.mark_used(topic, TOPICS)

    slug = slugify(data.get("title_en") or topic)[:50] + "-" + uuid.uuid4().hex[:6]
    script_path = LONG_DIR / f"te-{slug}.md"
    script_path.write_text(writer_te.to_markdown(data), encoding="utf-8")

    # queue the matching Short (best effort) for the next short slot
    try:
        short = writer_te.generate_short(topic, data, model)
        (SHORT_QUEUE / f"te-{slug}-short.md").write_text(
            writer_te.to_markdown(short), encoding="utf-8")
        log(f"[te-video] queued a Short for this story")
    except Exception as e:  # noqa: BLE001
        log(f"[te-video] no Short queued ({e}); the short slot will write its own")

    entry = _new_entry("te-video", slugify(script_path.stem), data["title"],
                       data.get("title_en", ""), topic, data.get("description_hook", ""))
    return _finish(script_path, "te-video", tcfg, dry_run, entry)


def run_short(dry_run: bool) -> int:
    cfg = load_config()
    tcfg = cfg.get("telugu", {})
    if not tcfg.get("enabled", True):
        log("[te-short] disabled in config"); return 0
    if _uploaded_today("te-short") and not dry_run:
        log("[te-short] already uploaded today - skipping"); return 0

    queued = sorted(SHORT_QUEUE.glob("*.md"), key=lambda p: p.stat().st_mtime)
    if queued:
        script_path = queued[0]
        topic = script_path.stem
    else:
        if not writer_te.available():
            log("[te-short] nothing queued and GEMINI_API_KEY not set - skipping"); return 0
        model = tcfg.get("gemini_model", writer_te.DEFAULT_MODEL)
        _refill_topics_if_low(model)
        topic = ideas.next_topic(TOPICS)
        if not topic:
            log("[te-short] nothing queued and no story topics left"); return 0
        try:
            data = writer_te.generate_short(topic, None, model)
        except writer_te.GeminiUnavailable as e:
            log(f"[te-short] Gemini unavailable, topic kept for the next run: {e}")
            return 1
        except Exception as e:  # noqa: BLE001
            ideas.mark_used(topic, TOPICS)
            log(f"[te-short] writer failed for {topic!r}: {e}")
            return 0
        ideas.mark_used(topic, TOPICS)
        script_path = SHORT_QUEUE / f"te-{slugify(data.get('title_en') or topic)[:50]}-{uuid.uuid4().hex[:6]}-short.md"
        script_path.write_text(writer_te.to_markdown(data), encoding="utf-8")

    from pipeline.script_parser import parse_script
    s = parse_script(script_path)
    entry = _new_entry("te-short", slugify(script_path.stem), s.title, "", topic,
                       s.description_hook)
    return _finish(script_path, "te-short", tcfg, dry_run, entry)


def run_daily(dry_run: bool) -> int:
    """The 12pm-ET job: today's story first (which also writes today's Short),
    then that Short. Each half has its own once-a-day guard, so a re-run after
    a partial failure only redoes what is missing."""
    rc_long = run_long(dry_run)
    rc_short = run_short(dry_run)
    return rc_long or rc_short


def run_selftest() -> int:
    """Render the bundled sample story end to end (Telugu voice, fonts, text
    shaping, thumbnail). Needs no Gemini key and uploads nothing."""
    sample = ROOT / "scripts" / "samples" / "te-selftest.md"
    log("[selftest] rendering the bundled Telugu sample ...")
    rc, secs = _render(sample, short=False)
    slug = slugify(sample.stem)
    video = BUILD_DIR / slug / f"{slug}.mp4"
    thumb = BUILD_DIR / slug / f"{slug}_thumbnail.png"
    if rc != 0 or not video.exists() or video.stat().st_size < 100_000 or not thumb.exists():
        log(f"[selftest] FAILED (rc {rc}) - see the log above")
        return 1
    log(f"[selftest] OK in {secs}s: {video.name} ({video.stat().st_size // 1024} KB) + thumbnail")
    return 0


def status() -> None:
    hist = _history()
    up = [e for e in hist if e.get("status") == "uploaded"]
    print(f"telugu uploads total : {len(up)}  "
          f"(videos {sum(e.get('format') == 'te-video' for e in up)}, "
          f"shorts {sum(e.get('format') == 'te-short' for e in up)})")
    print(f"story topics left    : {ideas.remaining(TOPICS)}")
    print(f"shorts queued        : {len(list(SHORT_QUEUE.glob('*.md')))}")
    print(f"Gemini key           : {'set' if writer_te.available() else 'NOT set'}")
    print(f"channel authorised   : {'yes' if TOKEN.exists() else 'NO (no youtube_token_te.json)'}")
    for e in hist[-5:]:
        print(f"  {e['ts']}  {e.get('format', '?'):9} {e.get('status', '?'):18} {e.get('title_en') or e.get('title', '')}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="telugu")
    ap.add_argument("--kind", choices=("daily", "long", "short", "selftest", "gemini-check",
                                       "media-check"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
        return 0
    if not args.kind:
        ap.print_help()
        return 1
    if args.kind == "gemini-check":
        if not writer_te.available():
            print("GEMINI_API_KEY is not set"); return 1
        try:
            for line in writer_te.check():
                print(line)
        except Exception as e:  # noqa: BLE001
            print(f"model list failed: {e}"); return 1
        return 0
    if args.kind == "media-check":
        if not writer_te.available():
            print("GEMINI_API_KEY is not set"); return 1
        from pipeline import genmedia
        for line in genmedia.check(BUILD_DIR / "media-check"):
            print(line)
        return 0
    if args.kind == "selftest":
        return run_selftest()
    if args.kind == "daily":
        return run_daily(args.dry_run)
    return run_long(args.dry_run) if args.kind == "long" else run_short(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
