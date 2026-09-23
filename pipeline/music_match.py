"""Pick a music track whose mood actually fits the video, instead of a fully
random pick from the library.

Two layers, because YouTube Audio Library titles are usually NOT
mood-descriptive ("Chase The Sun", "Vibe Check", "Melissa" tell you nothing
about the track by keyword alone):

1. _TRACK_MOOD - an explicit, hand-classified mood mapping for every track
   in the current 146-track library (see MUSIC_SETUP.md), judged from
   title/artist/genre convention. This is the primary matcher and is what
   actually fixes most videos landing on a random track.
2. _TRACK_MOOD_WORDS - a keyword fallback for any track added to the library
   later that isn't in the table above yet.

Topic detection here is independent of pipeline.thumbnail's category badges
(those are a visible on-screen label and shouldn't be expanded just to steer
music) - it covers more topic ground on purpose, since most real titles on
this channel are tech/body-science/everyday-phenomena content that the
narrower thumbnail categories don't recognise and were falling through to
"neutral" (fully random) every time.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

def _kw(*terms: str) -> str:
    """Build a word-boundary pattern where a single-word term also matches
    its plain plural (bone -> bones, organ -> organs) - a bare \\bword\\b does
    NOT match the plural, since there's no boundary between the word and a
    trailing 's'. Deliberately just `s?`, not `\\w*` - the latter would also
    match unrelated words sharing a prefix (war -> warm, warning; star ->
    start, starting)."""
    parts = [re.escape(t) if " " in t else re.escape(t) + "s?" for t in terms]
    return r"\b(?:" + "|".join(parts) + r")\b"


# --------------------------------------------------------------------------- #
# topic -> target mood(s). Checked in order, first match wins per pattern list.
# --------------------------------------------------------------------------- #
_TOPIC_MOOD = [
    (_kw("ai", "artificial intelligence", "robot", "algorithm", "machine learning",
        "computer", "chip", "gadget", "wireless", "charging", "phone", "screen",
        "app", "software", "drone", "satellite"),
     ("energetic", "upbeat")),
    (_kw("space", "planet", "moon", "mars", "star", "galaxy", "cosmos", "nasa",
        "orbit", "solar", "astronaut", "black hole", "comet", "asteroid",
        "voyager", "interstellar"),
     ("cosmic",)),
    (_kw("history", "ancient", "roman", "empire", "century", "centuries",
        "archaeolog", "lost", "civilisation", "civilization", "war", "egypt",
        "medieval"),
     ("mysterious",)),
    (_kw("brain", "psycholog", "mind", "memory", "behavior", "behaviour", "bias",
        "cognitive", "perception", "emotion", "dream", "sleep"),
     ("calm", "mysterious")),
    (_kw("news", "study finds", "researcher", "new research",
        "scientists discover", "breakthrough"),
     ("energetic",)),
    (_kw("body", "bodies", "bone", "blood", "brain", "heart", "lung", "muscle",
        "nerve", "cell", "dna", "gene", "hormone", "immune", "reflex", "hiccup",
        "breath", "fingerprint", "skin", "organ"),
     ("warm", "calm")),
    (_kw("animal", "insect", "bird", "bee", "ant", "crow", "elephant", "shark",
        "octopus", "dinosaur", "species", "creature"),
     ("warm", "playful")),
    (_kw("ocean", "glacier", "volcano", "earthquake", "desert", "mountain",
        "weather", "storm", "climate", "geology", "planet earth"),
     ("calm", "dramatic")),
    (_kw("math", "number", "pattern", "physics", "engineering", "traffic",
        "bridge", "machine", "mechanism", "formula", "equation"),
     ("playful", "mysterious")),
    (r"\bwhat if\b", ("playful", "cosmic")),
    (r"\bwhat happens\b", ("playful", "energetic")),
    (_kw("danger", "extreme", "survive", "survival", "deadly", "catastrophe",
        "disaster"),
     ("dramatic",)),
]


def target_moods(title: str, tags: list[str] | None = None) -> tuple[str, ...]:
    hay = (title + " " + " ".join(tags or [])).lower()
    for pat, moods in _TOPIC_MOOD:
        if re.search(pat, hay):
            return moods
    return ()


# --------------------------------------------------------------------------- #
# explicit per-track moods for the current library (best-effort, judged from
# title/artist/genre convention - not verified by ear). Matched by substring
# against the track's title (the part before " - "), so filename variants
# like a trailing " (1)" or a different artist credit still match.
# --------------------------------------------------------------------------- #
_TRACK_MOOD: dict[str, tuple[str, ...]] = {
    "29 palms": ("warm", "calm"),
    "accidents will happen": ("playful",),
    "airline": ("upbeat",),
    "alone time": ("calm",),
    "angel": ("calm",),  # Angel's Dream (mojibake apostrophe in source file)
    "attraction": ("energetic",),
    "be the one": ("warm",),
    "book bag": ("playful",),
    "burned out": ("calm", "dramatic"),
    "chase the sun": ("upbeat",),
    "chef brian": ("playful",),
    "circular beginning": ("calm", "mysterious"),
    "come with us": ("warm",),
    "cooked": ("playful", "energetic"),
    "daydream bliss": ("calm",),
    "doorway": ("mysterious",),
    "dream escape": ("calm", "mysterious"),
    "dream lagoon": ("calm", "cosmic"),
    "dreamer": ("calm",),
    "easy day": ("calm", "upbeat"),
    "ether real": ("cosmic",),
    "eureka": ("playful", "upbeat"),
    "finding me": ("warm",),
    "fringe": ("mysterious",),
    "frolic": ("playful",),
    "frozen in love": ("calm",),
    "galactic bass": ("cosmic", "energetic"),
    "gone away": ("calm",),
    "gymnopedie": ("calm",),
    "happy trails": ("upbeat",),
    "hear the noise": ("energetic",),
    "i love you": ("warm",),
    "i'll follow you": ("warm", "energetic"),
    "apostrophe": ("playful",),
    "in the atmosphere": ("cosmic", "playful"),
    "innocence": ("calm", "warm"),
    "jazz tape": ("playful",),
    "kuntry boy": ("upbeat",),
    "land of my fathers": ("warm", "mysterious"),
    "lazy porch swing blues": ("calm",),
    "length of light": ("cosmic", "mysterious"),
    "let's do this": ("upbeat", "energetic"),
    "level eleven": ("energetic",),
    "lily's song": ("calm",),
    "long distance": ("calm",),
    "moist": ("energetic",),
    "neither sweat nor tears": ("calm",),
    "nevada city": ("warm",),
    "no slope": ("energetic",),
    "now i know": ("playful", "upbeat"),
    "oracion": ("mysterious", "warm"),
    "orient": ("mysterious",),
    "precious girl": ("warm",),
    "rainy sundays": ("calm",),
    "ruminate": ("calm", "mysterious"),
    "run letting": ("energetic",),
    "see you on the otherside": ("calm",),
    "she's gone": ("calm",),
    "shining": ("warm", "upbeat"),
    "side path": ("mysterious",),
    "sleep music": ("calm",),
    "solar flares": ("cosmic", "energetic"),
    "somnia": ("calm",),
    "soul ballad": ("calm", "warm"),
    "species": ("dramatic", "energetic"),
    "staring at the valley": ("calm",),
    "static": ("mysterious", "dramatic"),
    "sugar pines": ("calm",),
    "sunday": ("calm",),
    "sunshine cantina": ("upbeat",),
    "talk to me": ("warm",),
    "that night in your car": ("calm", "playful"),
    "the joy definitive": ("upbeat", "playful"),
    "turning slowly": ("mysterious", "calm"),
    "twinkle": ("cosmic",),
    "undeniable": ("upbeat",),
    "until we meet again": ("calm", "warm"),
    "vespers": ("calm", "mysterious"),
    "watercolors": ("calm",),
    "we will be": ("warm", "upbeat"),
    "you like it": ("upbeat", "playful"),
    # videos/
    "a face in a cloud": ("calm",),
    "all i've ever felt all at once": ("calm", "dramatic"),
    "among the stars": ("cosmic",),
    "an excuse to do less": ("playful",),
    "ancient history": ("mysterious",),
    "back to the future jellyfish": ("playful", "energetic"),
    "baskets in the sky": ("cosmic", "calm"),
    "beatiful mess": ("warm",),
    "beautiful world": ("warm", "upbeat"),
    "before i go": ("calm", "dramatic"),
    "body and attitude": ("energetic",),
    "book me 2 flirt": ("playful",),
    "city lights": ("energetic", "warm"),
    "clover 3": ("calm",),
    "cutscene crush": ("playful", "energetic"),
    "dripped out": ("energetic",),
    "easy stroll": ("calm", "playful"),
    "everything": ("warm",),
    "faith": ("warm", "calm"),
    "feelin diff": ("upbeat",),
    "fields of fariness": ("calm",),
    "forever ever": ("warm",),
    "gently, onwards": ("calm",),
    "high beams": ("energetic",),
    "intergalactic": ("cosmic",),
    "june time": ("warm", "calm"),
    "jungle trip": ("playful", "warm"),
    "last sunrise": ("calm", "cosmic"),
    "last laugh": ("playful",),
    "let it ride": ("upbeat", "energetic"),
    "locked in": ("energetic", "dramatic"),
    "los encinos": ("warm",),
    "lottery": ("upbeat",),
    "melissa": ("calm", "warm"),
    "miles beyond": ("cosmic",),
    "morning mist": ("calm",),
    "neon nights": ("energetic", "mysterious"),
    "no one here gets in alive": ("dramatic", "mysterious"),
    "oceans, rivers, canyons": ("calm",),
    "on our side": ("warm", "upbeat"),
    "on the flip": ("energetic",),
    "resolution or reflection": ("calm",),
    "save me": ("dramatic", "calm"),
    "sky is the limit": ("upbeat",),
    "sunday skate in golden gate": ("playful", "upbeat"),
    "supersize me": ("playful", "energetic"),
    "supreme": ("upbeat", "energetic"),
    "survival mode": ("dramatic", "energetic"),
    "tell the angels": ("calm", "warm"),
    "through the night": ("calm", "mysterious"),
    "timelapsed tides": ("cosmic", "calm"),
    "tiny shell": ("calm", "playful"),
    "turn in the sun": ("upbeat", "warm"),
    "vibe check": ("upbeat", "playful"),
    "vitality": ("energetic", "upbeat"),
    "wait too long": ("calm",),
    "when it ends": ("dramatic", "calm"),
    "wonderland": ("playful", "mysterious"),
    "would it matter": ("calm", "warm"),
}

# fallback keyword matcher, used only for a track not found in _TRACK_MOOD
# (e.g. a new track added to the library later)
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
    "energetic": r"\b(run\w*|fast|sprint|chase|rush|power|drive|pump)\b",
    "warm": r"\b(love|heart|warm|friend|family|home|sunrise|sunshine)\b",
    "playful": r"\b(fun|silly|quirky|bounce|giggle|toy|game|joy)\b",
    "dramatic": r"\b(danger|storm|battle|fight|alarm|crisis|survive)\b",
}


def _track_title(stem: str) -> str:
    return stem.split(" - ")[0].strip().lower()


def track_moods(track_name: str) -> tuple[str, ...]:
    title = _track_title(track_name)
    for key, moods in _TRACK_MOOD.items():
        if key in title:
            return moods
    low = track_name.lower()
    return tuple(m for m, pat in _TRACK_MOOD_WORDS.items() if re.search(pat, low))


def target_mood(title: str, tags: list[str] | None = None) -> str:
    moods = target_moods(title, tags)
    return moods[0] if moods else "neutral"


def pick(pool: list[Path], title: str, tags: list[str] | None = None) -> Path | None:
    if not pool:
        return None
    moods = target_moods(title, tags)
    if moods:
        matched = [p for p in pool if set(track_moods(p.stem)) & set(moods)]
        if matched:
            return random.choice(matched)
    return random.choice(pool)
