"""Daily 'how it's made' explainers: render and publish, once per ET day per language.

    python explainer_daily.py --lang both     English (YouTube + Instagram) then Telugu (YouTube)
    python explainer_daily.py --lang en --dry-run

State lives in history-explainer.jsonl. English posts to the main channel and Instagram, Telugu
to the Telugu channel; each language has its own once-a-day guard, so a re-run after a partial
failure only redoes what is missing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from explainer import build
from pipeline.util import ROOT, load_config

HISTORY = ROOT / "history-explainer.jsonl"
ET = ZoneInfo("America/New_York")
TOKENS = {"en": ROOT / "youtube_token.json", "te": ROOT / "youtube_token_te.json"}


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def _rows() -> list[dict]:
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


def _done_today(lang: str) -> bool:
    today = dt.datetime.now(ET).date().isoformat()
    for r in _rows():
        if r.get("lang") != lang or r.get("status") != "uploaded" or not r.get("ts"):
            continue
        t = dt.datetime.fromisoformat(r["ts"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        if t.astimezone(ET).date().isoformat() == today:
            return True
    return False


def run(lang: str, dry_run: bool) -> int:
    cfg = load_config()
    if _done_today(lang) and not dry_run:
        log(f"[explainer-{lang}] already uploaded today - skipping"); return 0
    res = build(lang)
    if not res:
        log(f"[explainer-{lang}] nothing rendered"); return 1
    slug, video, data = res["slug"], res["video"], res["data"]
    meta = json.loads((video.parent / f"{slug}.meta.json").read_text(encoding="utf-8"))
    entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "lang": lang,
             "slug": slug, "title": meta["title"], "title_en": data.get("title_en", ""),
             "topic": res["topic"]}
    token = TOKENS[lang]
    if dry_run or not token.exists():
        entry["status"] = "rendered" if dry_run else "rendered_no_token"
        _record(entry)
        log(f"[explainer-{lang}] rendered only: {video}")
        return 0

    from pipeline import youtube
    visibility = (cfg.get("telugu", {}) if lang == "te" else cfg.get("auto", {})).get(
        "visibility", "public")
    try:
        vid = youtube.upload(video, title=meta["title"], description=meta["description"],
                             tags=meta["tags"], privacy=visibility, made_for_kids=False,
                             thumbnail=None, playlist_id=None, token_path=token, language=lang)
    except Exception as e:  # noqa: BLE001
        entry.update(status="upload_failed", error=str(e))
        _record(entry)
        log(f"[explainer-{lang}] upload failed: {e}")
        return 1
    entry.update(status="uploaded", video_id=vid, visibility=visibility)
    log(f"[explainer-{lang}] UPLOADED {meta['title']}  https://youtu.be/{vid}  ({visibility})")

    if lang == "en":  # Instagram: the English explainers also go out as a Reel + Story
        try:
            from pipeline import instagram
            posted = instagram.publish_explainer(video, slug, meta["title"],
                                                 data.get("description_hook", ""), cfg)
            if posted:
                entry.update(posted)
                log(f"[explainer-en] Instagram: {'posted' if posted.get('instagram_posted') else 'FAILED'}")
        except Exception as e:  # noqa: BLE001 - never undo a good YouTube upload
            log(f"[explainer-en] Instagram step failed: {e}")
    _record(entry)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="explainer_daily")
    ap.add_argument("--lang", choices=("en", "te", "both"), default="both")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rc = 0
    for lang in (("en", "te") if args.lang == "both" else (args.lang,)):
        rc = run(lang, args.dry_run) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
