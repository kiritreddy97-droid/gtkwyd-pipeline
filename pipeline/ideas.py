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


LOW_WATER = 12


def refill_if_low(path: Path, kind: str, model: str | None = None, n: int = 40) -> int:
    """Top up an English topic queue from Gemini when it runs low, so the channel never
    runs out of things to publish. Returns the number of topics added (0 = nothing done,
    including when there is no GEMINI_API_KEY or Gemini is unreachable)."""
    if remaining(path) >= LOW_WATER:
        return 0
    try:
        from . import writer_te
        if not writer_te.available():
            return 0
        existing = [ln for ln in _iter(path)]
        used = sorted(_used_set())
        avoid = "; ".join((existing + used)[-150:])
        shape = ("a punchy single idea that works as a 40-second Short"
                 if kind == "short" else "a topic that can fill a 6-8 minute explainer video")
        system = ("You suggest topics for an English YouTube channel called 'Get To Know What "
                  "You Don't': surprising, well-established facts from science, technology, "
                  "psychology, nature, history and everyday life. Nothing medical, financial, "
                  "political or controversial.")
        out = writer_te._call(
            system, f"Give {n} new topics as short lower-case one-line phrases, each {shape}, "
                    f"in the style of 'the Zeigarnik effect and unfinished tasks'. Do NOT repeat "
                    f"or closely resemble any of these: {avoid}",
            writer_te._TOPICS_SCHEMA, model or writer_te.DEFAULT_MODEL,
            max_tokens=4000, temperature=0.9)
    except Exception as e:  # noqa: BLE001 - a refill miss must never break a publish run
        print(f"[ideas] topic refill failed: {e}")
        return 0
    seen = {t.lower() for t in existing} | set(used)
    fresh = []
    for t in out.get("topics", []):
        t = " ".join(str(t).split()).strip()
        if t and t.lower() not in seen and "#" not in t:
            fresh.append(t)
            seen.add(t.lower())
    if fresh:
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n# auto-added by Gemini\n" + "\n".join(fresh) + "\n")
        print(f"[ideas] {path.name} was low - added {len(fresh)} topics")
    return len(fresh)
