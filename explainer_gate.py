"""Cheap pre-check for explainer.yml (stdlib only): go=true when it is 12:20 ET or later and an
explainer for today (ET calendar day) is still missing in English or Telugu, or on a manual run."""
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HISTORY = Path(__file__).resolve().parent / "history-explainer.jsonl"


def et_date(ts: str) -> str:
    t = dt.datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(ET).date().isoformat()


def decide(now_et: dt.datetime, manual: bool, rows: list[dict]) -> tuple[bool, str]:
    if manual:
        return True, "manual run"
    if (now_et.hour, now_et.minute) < (12, 20):
        return False, f"{now_et:%H:%M} ET is before 12:20 ET"
    today = now_et.date().isoformat()
    done = {r.get("lang") for r in rows
            if r.get("status") == "uploaded" and r.get("ts") and et_date(r["ts"]) == today}
    if {"en", "te"} <= done:
        return False, "today's English and Telugu explainers are already uploaded"
    return True, f"still to do today: {', '.join(sorted({'en', 'te'} - done))}"


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
