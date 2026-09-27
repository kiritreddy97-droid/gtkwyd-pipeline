"""Holds content that failed QA (pipeline.qa_gate) after all regenerate
attempts, so the NEXT scheduled run for that category tries it again instead
of losing it - per the channel owner's explicit instruction: skip the slot,
log it clearly, but get it ready and post it at the next opportunity rather
than dropping it. One held item per category; a newer hold replaces an
older one for the same category (the newer failure is more relevant)."""
from __future__ import annotations

import json

from .util import ROOT

QUEUE_FILE = ROOT / ".qa_held_queue.json"


def _load() -> dict:
    if not QUEUE_FILE.exists():
        return {}
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save(data: dict) -> None:
    QUEUE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def hold(category: str, payload: dict) -> None:
    data = _load()
    data[category] = payload
    _save(data)


def pop(category: str) -> dict | None:
    data = _load()
    item = data.pop(category, None)
    if item is not None:
        _save(data)
    return item


def peek(category: str) -> dict | None:
    return _load().get(category)
