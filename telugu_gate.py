"""Cheap pre-check for telugu.yml: should this run do any work?

Stdlib only (runs before any dependencies are installed). Writes go=true|false
to $GITHUB_OUTPUT. The workflow has several cron triggers around and after
12:00 US Eastern; this lets the first one at/after noon ET do the day's work
and the rest exit in seconds - and, because it keys off Eastern time rather
than a fixed UTC hour, it stays correct across daylight-saving changes.

  go=true  when: manually dispatched, OR (it is 12:00 ET or later AND today's
           story, Short or drama episode has not been uploaded yet, ET calendar day)
"""
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HISTORY = Path(__file__).resolve().parent / "history-telugu.jsonl"


def et_date(ts: str) -> str:
    t = dt.datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(ET).date().isoformat()


def decide(now_et: dt.datetime, manual: bool, rows: list[dict]) -> tuple[bool, str]:
    if manual:
        return True, "manual run"
    if now_et.hour < 12:
        return False, f"{now_et:%H:%M} ET is before 12:00 ET"
    today = now_et.date().isoformat()
    # a drama episode skipped because the free voice quota ran out counts as done for today
    done = {r.get("format") for r in rows
            if r.get("ts") and et_date(r["ts"]) == today
            and (r.get("status") == "uploaded"
                 or (r.get("format") == "te-drama" and r.get("status") == "deferred_quota"))}
    need = {"te-video", "te-short", "te-drama"}
    if need <= done:
        return False, "today's story, Short and drama episode are already uploaded"
    return True, f"{now_et:%H:%M} ET, still to do today: " + ", ".join(sorted(need - done))


def main() -> int:
    rows = []
    if HISTORY.exists():
        for ln in HISTORY.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
    manual = os.environ.get("GITHUB_EVENT_NAME", "") == "workflow_dispatch"
    go, why = decide(dt.datetime.now(ET), manual, rows)
    print(f"gate: go={go} ({why})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"go={'true' if go else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
