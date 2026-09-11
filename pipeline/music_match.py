"""Pick a music track whose mood actually fits the video, instead of a fully
random pick from the library. Matching is keyword-based on the track's
filename (Audio Library titles are usually mood-descriptive - "Dream Lagoon",
"Solar Flares", "Ancient History") crossed with the same category detector the
thumbnail generator uses, so a space video reaches for something cosmic, a
psychology video for something calm, and so on. Falls back to a random pick
from the whole pool whenever nothing matches, so an unmatched pool is never a
failure - just less curated."""
from __future__ import annotations

import random
import re
from pathlib import Path

from . import thumbnail

# thumbnail category label -> the mood bucket that suits it
_MOOD_BY_CATEGORY = {
    "FUTURE TECH": "upbeat",
    "SPACE": "cosmic",
    "HIDDEN HISTORY": "mysterious",
    "PSYCHOLOGY": "calm",
    "SCIENCE NEWS": "neutral",
    "WHAT IF": "cosmic",
    "WHAT HAPPENS": "neutral",
    "DID YOU KNOW": "neutral",
}

# mood -> words that tend to show up in a track's own title for that mood
_TRACK_MOOD_WORDS = {
    "cosmic": r"\b(galactic|intergalactic|space|orbit|cosmic|interstellar|"
             r"nebula|starfield|twinkle|stars?|atmosphere)\b",
    "calm": r"\b(calm|gentle|quiet|slow|peaceful|soft|lullaby|morning|mist|"
           r"clouds?|rain|ambient|reflection|still|float|drift|sleep|dreams?|"
           r"dreaming)\b",
    "mysterious": r"\b(shadow|mystery|unknown|secret|enigma|ancient|ruins?|"
                 r"dark|haunt\w*|whisper\w*)\b",
    "upbeat": r"\b(bounce|beat|dance|party|energy|pulse|drive|hustle|jam|"
             r"groove|funk|swing|upbeat)\b",
}


def track_moods(track_name: str) -> set[str]:
    low = track_name.lower()
    return {m for m, pat in _TRACK_MOOD_WORDS.items() if re.search(pat, low)}


def target_mood(title: str, tags: list[str]) -> str:
    label, _ = thumbnail._category(title, tags or [])
    return _MOOD_BY_CATEGORY.get(label, "neutral")


def pick(pool: list[Path], title: str, tags: list[str] | None = None) -> Path | None:
    if not pool:
        return None
    mood = target_mood(title, tags or [])
    if mood != "neutral":
        matched = [p for p in pool if mood in track_moods(p.stem)]
        if matched:
            return random.choice(matched)
    return random.choice(pool)
