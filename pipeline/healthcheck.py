"""Pre-flight and post-flight health checks for the publish pipeline.

Pre-flight (run ~5 min before a scheduled slot): verifies the things that can
actually be checked without a full render - YouTube auth still works, the
stock-footage APIs still respond, and there's real content available to
publish. It cannot rewrite arbitrary code bugs (that needs a human or an LLM
session), but it catches the class of failure that silently breaks a schedule
for days: an expired token, a dead API key, an empty content bank.

Post-flight (run ~10 min after a slot): confirms something with a matching
recent timestamp actually landed in history.jsonl with status "uploaded" (or
"rendered" if uploads are disabled), instead of just assuming the scheduled
run worked.

Usage:
    python -m pipeline.healthcheck preflight video
    python -m pipeline.healthcheck preflight short
    python -m pipeline.healthcheck postflight
"""
from __future__ import annotations

import json
import sys
import time
import tomllib
from pathlib import Path

from .util import ROOT


def _load_cfg() -> dict:
    cfg_path = ROOT / "config.toml"
    if not cfg_path.exists():
        return {}
    return tomllib.loads(cfg_path.read_text(encoding="utf-8"))


def check_youtube_auth() -> tuple[bool, str]:
    try:
        from . import youtube
        yt = youtube.get_service(interactive=False)
        name = youtube.channel_title(yt)
        return True, f"channel reachable ({name})"
    except Exception as e:  # noqa: BLE001
        return False, f"YouTube auth broken: {e}"


def check_stock_apis(cfg: dict) -> tuple[bool, str]:
    import requests
    apis = cfg.get("apis", {})
    pexels_key = apis.get("pexels_key", "")
    pixabay_key = apis.get("pixabay_key", "")
    problems = []

    if not pexels_key:
        problems.append("Pexels key missing from config")
    else:
        try:
            r = requests.get("https://api.pexels.com/v1/search",
                             headers={"Authorization": pexels_key},
                             params={"query": "test", "per_page": 1}, timeout=15)
            if r.status_code != 200:
                problems.append(f"Pexels returned HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"Pexels unreachable: {e}")

    if not pixabay_key:
        problems.append("Pixabay key missing from config")
    else:
        try:
            r = requests.get("https://pixabay.com/api/",
                             params={"key": pixabay_key, "q": "test", "per_page": 3},
                             timeout=15)
            if r.status_code != 200:
                problems.append(f"Pixabay returned HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"Pixabay unreachable: {e}")

    if problems:
        return False, "; ".join(problems)
    return True, "Pexels + Pixabay both responding"


def check_content_available(fmt: str) -> tuple[bool, str]:
    bank = ROOT / "scripts" / ("shorts-bank" if fmt == "short" else "bank")
    ready = ROOT / "scripts" / ("shorts-ready" if fmt == "short" else "ready")
    n = len(list(bank.glob("*.md"))) + len(list(ready.glob("*.md")))
    if n == 0:
        return False, (f"no {fmt} scripts left in bank/ready - the AI writer "
                       f"would have to carry this slot, check topics{'​-shorts' if fmt=='short' else ''}.txt")
    return True, f"{n} {fmt} script(s) available"


def check_daily_cap(cfg: dict) -> tuple[bool, str]:
    """Catches the exact failure that broke video-pm/short-pm on 2026-09-15:
    preflight said OK, but the cap was already exhausted so the run silently
    no-op'd. Mirrors auto.py's own _count_today() logic."""
    import datetime as dt
    history = ROOT / "history.jsonl"
    acfg = cfg.get("auto", {})
    cap = int(acfg.get("max_per_day", 6))
    today = dt.date.today().isoformat()
    count = 0
    if history.exists():
        for line in history.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("ts", "").startswith(today) and entry.get("status") == "uploaded":
                count += 1
    if count >= cap:
        return False, f"daily cap already reached ({count}/{cap}) - this slot will silently no-op"
    return True, f"{count}/{cap} uploads used today"


def check_slot_resolves(fmt: str, source: str) -> tuple[bool, str]:
    """Catches the exact class of bug that broke the schedule for days: make
    sure format/source are non-empty and well-formed before a real run
    depends on them."""
    if fmt not in ("video", "short"):
        return False, f"format resolved to invalid value {fmt!r}"
    if source not in ("auto", "news", "bank"):
        return False, f"source resolved to invalid value {source!r}"
    return True, f"format={fmt} source={source}"


def run_preflight(fmt: str, source: str) -> int:
    cfg = _load_cfg()
    checks = [
        ("slot", check_slot_resolves(fmt, source)),
        ("daily_cap", check_daily_cap(cfg)),
        ("youtube_auth", check_youtube_auth()),
        ("stock_apis", check_stock_apis(cfg)),
        ("content", check_content_available(fmt)),
    ]
    failed = []
    for name, (ok, msg) in checks:
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {msg}")
        if not ok:
            failed.append(f"{name}: {msg}")

    if failed:
        print("PREFLIGHT_FAILED")
        print("\n".join(failed))
        return 1
    print("PREFLIGHT_OK")
    return 0


def run_postflight(fmt: str, slot: str, since_minutes: int = 30) -> int:
    """Look for a matching history.jsonl entry newer than `since_minutes` ago."""
    history = ROOT / "history.jsonl"
    if not history.exists():
        print("POSTFLIGHT_FAILED\nno history.jsonl found at all")
        return 1

    cutoff = time.time() - since_minutes * 60
    recent = []
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            ts = time.mktime(time.strptime(entry["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
        except (KeyError, ValueError):
            continue
        if ts >= cutoff:
            recent.append(entry)

    matching = [e for e in recent if e.get("format") == fmt
               and e.get("status") in ("uploaded", "rendered")]
    if matching:
        e = matching[-1]
        print(f"POSTFLIGHT_OK\nfound {e.get('slug')} status={e.get('status')} "
             f"at {e.get('ts')}")
        return 0

    if recent:
        print("POSTFLIGHT_FAILED")
        print(f"found {len(recent)} recent entries but none matching "
             f"format={fmt} with a success status:")
        for e in recent:
            print(f"  {e.get('ts')} {e.get('slug')} format={e.get('format')} "
                 f"status={e.get('status')}")
        return 1

    print(f"POSTFLIGHT_FAILED\nno history.jsonl entries at all in the last "
         f"{since_minutes} minutes for slot {slot!r} - the run likely never "
         f"completed the render/pick step")
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    mode = sys.argv[1]
    if mode == "preflight":
        fmt = sys.argv[2] if len(sys.argv) > 2 else "video"
        source = sys.argv[3] if len(sys.argv) > 3 else "auto"
        return run_preflight(fmt, source)
    if mode == "postflight":
        fmt = sys.argv[2] if len(sys.argv) > 2 else "video"
        slot = sys.argv[3] if len(sys.argv) > 3 else "unknown"
        return run_postflight(fmt, slot)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
