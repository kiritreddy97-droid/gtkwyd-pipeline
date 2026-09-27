"""Temporary stopgap: GitHub Actions' own cron trigger stopped firing for
this repo entirely (confirmed 2026-09-27 - zero schedule-event runs across
ANY workflow since 2026-09-26T07:39 UTC, despite every workflow showing
"active" via `gh workflow list` and no incident on githubstatus.com).
Touching each workflow file (a documented community workaround) did not
fix it either.

This script replicates the exact cron table baked into publish.yml/
instagram.yml/story.yml/reels.yml/fact-image.yml and manually fires
`gh workflow run` for anything due, using a local state file (NOT
committed to git - this machine only) to avoid double-firing the same
slot. Meant to be invoked every 10-15 minutes by an external trigger (a
CronCreate stopgap in the Claude session, Windows Task Scheduler, or by
hand) until GitHub's native scheduling resumes - check with:

    gh run list --json event --jq "[.[] | select(.event==\"schedule\")] | length"

A nonzero result means real schedule events are landing again and this
script is no longer needed.

    .venv\\Scripts\\python fallback_scheduler.py           fire whatever's due
    .venv\\Scripts\\python fallback_scheduler.py --dry-run  show what WOULD fire
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / ".fallback_scheduler_state.json"
WINDOW_MIN = 20  # fire if now is within this many minutes AFTER the slot time

# (workflow file, hour UTC, minute UTC, day-of-week filter (Python weekday,
# Mon=0..Sun=6), dispatch args). Mirrors each workflow's own cron + slot
# case-statement exactly - keep in sync if those ever change.
_WEEKDAY = lambda d: d < 5          # Mon-Fri
_WEEKDAY_SHIFTED = lambda d: 1 <= d <= 5  # Tue-Sat (the 8pm-crosses-midnight slot)

SLOTS = [
    ("publish.yml", 15, 7, _WEEKDAY, {"format": "video", "source": "news"}),
    ("publish.yml", 21, 4, _WEEKDAY, {"format": "video", "source": "auto"}),
    ("publish.yml", 14, 9, _WEEKDAY, {"format": "short", "source": "auto"}),
    ("publish.yml", 20, 12, _WEEKDAY, {"format": "short", "source": "auto"}),
    ("publish.yml", 2, 7, _WEEKDAY_SHIFTED, {"format": "short", "source": "auto"}),
    ("publish.yml", 8, 34, _WEEKDAY, {"format": "short", "source": "auto"}),
    ("instagram.yml", 16, 9, _WEEKDAY, {}),
    ("instagram.yml", 16, 39, _WEEKDAY, {}),
    ("instagram.yml", 17, 9, _WEEKDAY, {}),
    ("instagram.yml", 22, 6, _WEEKDAY, {}),
    ("instagram.yml", 22, 36, _WEEKDAY, {}),
    ("instagram.yml", 23, 6, _WEEKDAY, {}),
    ("story.yml", 23, 43, _WEEKDAY, {}),
    ("reels.yml", 18, 17, _WEEKDAY, {"category": "satisfying"}),
    ("reels.yml", 19, 26, _WEEKDAY, {"category": "soothing"}),
    ("reels.yml", 22, 47, _WEEKDAY, {"category": "fitness"}),
    ("fact-image.yml", 16, 52, _WEEKDAY, {}),
]


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(prog="fallback_scheduler")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    today = now.date().isoformat()
    raw_state = _load_state()
    fired_today = set(raw_state.get(today, []))
    weekday = now.weekday()

    any_due = False
    for wf, hour, minute, dow_ok, dispatch_args in SLOTS:
        if not dow_ok(weekday):
            continue
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta_min = (now - slot_dt).total_seconds() / 60
        slot_key = f"{wf}:{hour:02d}:{minute:02d}"
        if 0 <= delta_min <= WINDOW_MIN and slot_key not in fired_today:
            any_due = True
            cmd = ["gh", "workflow", "run", wf]
            for k, v in dispatch_args.items():
                cmd += ["-f", f"{k}={v}"]
            print(f"[fallback] due: {slot_key} ({delta_min:.0f} min late)  ->  {' '.join(cmd)}")
            if not args.dry_run:
                subprocess.run(cmd, cwd=str(ROOT))
                fired_today.add(slot_key)

    if not any_due:
        print(f"[fallback] nothing due right now ({now:%H:%M} UTC)")

    if not args.dry_run:
        raw_state[today] = sorted(fired_today)
        # keep only today + yesterday, no need to grow forever
        raw_state = {k: v for k, v in raw_state.items() if k >= (now.date() - dt.timedelta(days=1)).isoformat()}
        _save_state(raw_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
