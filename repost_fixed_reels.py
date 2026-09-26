"""One-off remediation: repost bug-fixed Instagram glimpses for Reels whose
original posts the channel owner manually deleted from Instagram. The
automation has no way to detect a manual delete on Instagram's side, so
nothing reposts on its own - this re-renders each original script fresh and
posts a new glimpse+cover using the crossfade/clean-thumbnail fixes already
shipped this session, then records the repost on the SAME history.jsonl
entry (original instagram_media_id kept for reference; new fields added).

    .venv\\Scripts\\python repost_fixed_reels.py
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

from pipeline.util import BUILD_DIR, ROOT, load_config

HISTORY = ROOT / "history.jsonl"
LOG = ROOT / "repost_fixed.log"
PY = Path(sys.executable)


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _commit_and_push(slug: str) -> None:
    """Commit+push right after each successful repost, not just once at the
    end - this loop can run for well over an hour across 17 renders, and a
    single end-of-job commit would lose every entry already written to disk
    if the runner is killed or the job times out partway through."""
    subprocess.run(["git", "config", "user.name", "gtkwyd-bot"], cwd=str(ROOT))
    subprocess.run(["git", "config", "user.email", "actions@users.noreply.github.com"],
                  cwd=str(ROOT))
    subprocess.run(["git", "add", "history.jsonl"], cwd=str(ROOT))
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(ROOT))
    if diff.returncode == 0:
        return  # nothing staged
    subprocess.run(["git", "commit", "-m", f"repost-fixed: {slug}"], cwd=str(ROOT))
    for attempt in range(5):
        push = subprocess.run(["git", "push"], cwd=str(ROOT))
        if push.returncode == 0:
            return
        log(f"{slug}: push rejected, fetching + rebasing (attempt {attempt + 1}) ...")
        subprocess.run(["git", "fetch", "origin", "main"], cwd=str(ROOT))
        subprocess.run(["git", "checkout", "--", "."], cwd=str(ROOT))
        rebase = subprocess.run(["git", "rebase", "origin/main"], cwd=str(ROOT))
        if rebase.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=str(ROOT))
    log(f"{slug}: WARNING could not push after retries - history update may be lost on rerun")


def _load_entries() -> list[dict | None]:
    lines = HISTORY.read_text(encoding="utf-8-sig").splitlines()
    return [json.loads(ln) if ln.strip() else None for ln in lines]


def _save_entries(entries: list[dict | None]) -> None:
    out = "\n".join(json.dumps(e, ensure_ascii=False) if e else "" for e in entries)
    HISTORY.write_text(out + "\n", encoding="utf-8")


def _update_entry_by_slug(slug: str, updates: dict) -> None:
    """Re-read from disk immediately before writing, not the copy loaded at
    the start of the run - a rebase in _commit_and_push (pulling in another
    workflow's concurrent commit) can change the on-disk file between
    iterations of this hours-long loop, and writing back a stale in-memory
    copy would silently discard whatever that rebase just merged in."""
    entries = _load_entries()
    for e in entries:
        if e and e.get("slug") == slug:
            e.update(updates)
            break
    _save_entries(entries)


def main() -> int:
    cfg = load_config()
    entries = _load_entries()

    targets = [e["slug"] for e in entries
              if e and e.get("instagram_posted") and not e.get("instagram_reposted_at")]
    log(f"found {len(targets)} previously-posted entries needing a fresh repost")

    from pipeline import instagram

    ok, failed = 0, 0
    for slug in targets:
        entry = next((e for e in _load_entries() if e and e.get("slug") == slug), None)
        if entry is None:
            log(f"SKIP {slug}: no longer in history.jsonl (unexpected)")
            failed += 1
            continue
        if entry.get("instagram_reposted_at"):
            log(f"SKIP {slug}: already reposted by a concurrent run")
            continue
        script_path = ROOT / "scripts" / "published" / f"video-{slug}.md"
        if not script_path.exists():
            log(f"SKIP {slug}: script not found at {script_path}")
            failed += 1
            continue

        log(f"--- {slug}: rendering fresh ---")
        rc = subprocess.run([str(PY), "make_video.py", str(script_path)], cwd=str(ROOT)).returncode
        if rc != 0:
            log(f"FAIL {slug}: render failed (rc {rc})")
            failed += 1
            continue

        outdir = BUILD_DIR / slug
        video = outdir / f"{slug}.mp4"
        thumb = outdir / f"{slug}_thumbnail.png"
        if not video.exists():
            log(f"FAIL {slug}: no video produced")
            failed += 1
            continue

        try:
            staged = instagram.stage_glimpse(video, slug, thumb if thumb.exists() else None, cfg)
        except Exception as e:  # noqa: BLE001
            log(f"FAIL {slug}: staging error: {e}")
            staged = None
        if not staged:
            log(f"FAIL {slug}: staging returned nothing")
            shutil.rmtree(outdir, ignore_errors=True)
            failed += 1
            continue

        social = cfg.get("social", {})
        handle = social.get("youtube_handle", "").strip()
        cap_lines = [entry.get("title", slug), "", "Full video on YouTube now — link in bio."]
        if handle:
            cap_lines.append(f"Search {handle} on YouTube.")
        cap_lines.append("More in the comments \U0001F447")
        caption = "\n".join(cap_lines)

        try:
            media_id = instagram.post_reel(staged["video_url"], caption,
                                           cover_url=staged.get("cover_url"))
        except Exception as e:  # noqa: BLE001
            log(f"FAIL {slug}: post failed: {e}")
            shutil.rmtree(outdir, ignore_errors=True)
            failed += 1
            continue

        updates = {
            "instagram_reposted_at": dt.datetime.now().isoformat(timespec="seconds"),
            "instagram_repost_media_id": media_id,
            "instagram_glimpse_url": staged["video_url"],
        }
        if "cover_url" in staged:
            updates["instagram_cover_url"] = staged["cover_url"]

        youtube_url = social.get("youtube_url", "").strip()
        if youtube_url:
            try:
                instagram.post_comment(media_id, f"Full video here: {youtube_url}")
                updates["instagram_repost_commented"] = True
            except Exception as e:  # noqa: BLE001
                log(f"{slug}: posted but comment failed: {e}")

        _update_entry_by_slug(slug, updates)
        _commit_and_push(slug)
        log(f"POSTED {slug} -> media {media_id}")
        ok += 1

        shutil.rmtree(outdir, ignore_errors=True)

    log(f"done: {ok} reposted, {failed} failed/skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
