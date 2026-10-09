"""Score a spoken opening sentence on five properties (0-100 each).

Adapted from hookscore.py in github.com/Jakeschincariol/youtube-agent-skill (MIT).
Verdict = 60% mean + 40% weakest property. It is a heuristic: a low score means
"look again", a high score is not a promise.
"""
from __future__ import annotations

import re

_FILLER = {"basically", "actually", "literally", "just", "really", "very", "so", "kind",
           "sort", "like", "guys", "hey", "welcome", "today", "video", "subscribe", "channel"}
_VAGUE = {"amazing", "incredible", "insane", "crazy", "huge", "massive", "game", "changer",
          "secret", "powerful", "ultimate", "best", "revolutionary", "mind", "blowing",
          "unbelievable"}
_CONCRETE = re.compile(r"\b(\d[\d,.]*\s?(%|k|m|x|s|m|h)?|\$\d|\d+\s?(second|minute|hour|day|week|month|year)s?)\b", re.I)
_YOU = re.compile(r"\b(you|your|you're|youre|yourself)\b", re.I)
_STAKE = re.compile(r"\b(lose|lost|wasting|waste|quit|fail|broke|cost|risk|before|stop|never|die|dying|dead)\b", re.I)
_CURIOSITY = re.compile(r"\b(why|how|what|which|until|before|but|nobody|almost|except|reason|actually)\b", re.I)


def _words(t: str) -> list[str]:
    return re.findall(r"[a-z0-9'%$.]+", t.lower())


def _specificity(t: str) -> int:
    w = _words(t)
    if not w:
        return 0
    s = (34 + len(_CONCRETE.findall(t)) * 22 - sum(x in _VAGUE for x in w) * 16
         - sum(x in _FILLER for x in w) * 5)
    s += min(18, 6 * sum(1 for x in t.split()[1:] if x[:1].isupper()))
    return max(0, min(100, s))


def _address(t: str) -> int:
    first = 30 if _YOU.search(" ".join(t.split()[:6])) else 0
    return max(0, min(100, 26 + len(_YOU.findall(t)) * 20 + first))


def _stakes(t: str) -> int:
    return max(0, min(100, 22 + len(_STAKE.findall(t)) * 26 + (14 if _CONCRETE.search(t) else 0)))


def _curiosity(t: str) -> int:
    q = 18 if t.strip().endswith("?") else 0
    closed = -18 if re.search(r"\b(because|so that|which means)\b", t, re.I) else 0
    return max(0, min(100, 24 + len(_CURIOSITY.findall(t)) * 17 + q + closed))


def _brevity(t: str) -> int:
    n = len(_words(t))
    if n == 0:
        return 0
    if 9 <= n <= 24:
        return 100
    if n < 9:
        return max(30, 100 - (9 - n) * 11)
    return max(10, 100 - (n - 24) * 7)


def score(text: str) -> tuple[int, dict[str, int]]:
    parts = {"specificity": _specificity(text), "address": _address(text),
             "stakes": _stakes(text), "curiosity": _curiosity(text),
             "brevity": _brevity(text)}
    vals = list(parts.values())
    return round(0.6 * (sum(vals) / len(vals)) + 0.4 * min(vals)), parts
