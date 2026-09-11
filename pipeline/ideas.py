"""Topic queues for automated script generation.

Each format has its own file: topics.txt (videos), topics-shorts.txt (shorts).
'#' lines and blanks are ignored. Consumed top-down. topics_used.txt is an
append-only log shared across both.

Hand-written scripts in the bank folders are used first and never touch these.
"""
from __future__ import annotations

from pathlib import Path

from .util import ROOT

USED = ROOT / "topics_used.txt"


def _used_set() -> set[str]:
    if not USED.exists():
        return set()
    return {ln.strip().lower() for ln in USED.read_text(encoding="utf-8").splitlines()
            if ln.strip()}


def _iter(path: Path):
    if not path.exists():
        return
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            yield ln


def peek_topics(path: Path, limit: int = 5) -> list[str]:
    used = _used_set()
    out = []
    for ln in _iter(path):
        if ln.lower() not in used:
            out.append(ln)
            if len(out) >= limit:
                break
    return out


def next_topic(path: Path) -> str | None:
    picks = peek_topics(path, 1)
    return picks[0] if picks else None


def mark_used(topic: str, path: Path | None = None) -> None:
    with USED.open("a", encoding="utf-8") as fh:
        fh.write(topic.strip() + "\n")


def remaining(path: Path) -> int:
    return len(peek_topics(path, limit=10_000))
