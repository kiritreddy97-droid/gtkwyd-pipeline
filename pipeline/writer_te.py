"""Telugu history-story scripts written by Google Gemini (free tier).

The local Ollama model used for the English channel is too small to write good
Telugu, so this uses the Gemini API instead (free key from aistudio.google.com,
set as GEMINI_API_KEY). Everything here is a no-op when the key is missing.

Output is converted into the same markdown script format the rest of the
pipeline already renders (with `lang: te` in the frontmatter).
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

from .util import PipelineError

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"

# Telugu narration runs ~76 words/minute with the neural voices (long compound
# words), so these word counts target ~10 minutes and ~60 seconds.
LONG_WORDS = (640, 900)
SHORT_WORDS = (58, 95)
LONG_SCENES = (12, 20)
SHORT_SCENES = (3, 6)

_TELUGU = re.compile(r"[ఀ-౿]")
_LATIN = re.compile(r"[A-Za-z]")


def api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def available() -> bool:
    return bool(api_key())


# --------------------------------------------------------------------------- #
# Gemini REST client
# --------------------------------------------------------------------------- #
_model_cache: dict[str, str] = {}


class GeminiUnavailable(PipelineError):
    """Gemini could not be reached / has no usable quota. Not the story's fault."""


_NOT_TEXT = ("image", "tts", "live", "audio", "embed", "robotics", "computer",
             "omni", "aqa", "veo", "imagen", "native", "thinking", "learnlm", "gemma")


def _version(name: str) -> tuple:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    v = tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
    return v + (0,) * (3 - len(v))  # pad so 3.1 > 3 when compared as tuples


def _candidates() -> list[str]:
    """Text-generation Flash models available to this key, best first: stable
    before preview, newest version first, plain flash before flash-lite."""
    r = requests.get(f"{GEMINI_BASE}/models", headers={"x-goog-api-key": api_key()},
                     params={"pageSize": 200}, timeout=30)
    r.raise_for_status()
    names = [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and not any(x in n for x in _NOT_TEXT)]

    def rank(n: str):
        return (0 if re.search(r"preview|exp", n) is None else 1,
                1 if "lite" in n else 0, tuple(-x for x in _version(n)), n)

    return sorted(flash, key=rank)


def _ping(model: str) -> int | str:
    """HTTP status of a tiny generateContent call, or 'timeout'/'error'."""
    try:
        r = requests.post(
            f"{GEMINI_BASE}/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key(), "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": "Reply with the single word OK."}]}],
                  "generationConfig": {"maxOutputTokens": 16}}, timeout=40)
    except requests.Timeout:
        return "timeout"
    except requests.RequestException:
        return "error"
    return r.status_code


def _discover_model() -> str:
    """Find a model this key can actually use right now (names get retired, and
    some models have no free-tier quota), by probing candidates with a tiny call."""
    tried = []
    for name in _candidates()[:8]:
        code = _ping(name)
        tried.append(f"{name}={code}")
        if code == 200:
            return name
    raise GeminiUnavailable("no Gemini model is usable with this key (" + ", ".join(tried) + ")")


def check() -> list[str]:
    """Diagnostic: every candidate model and whether a tiny call to it works."""
    return [f"{n}: HTTP {_ping(n)}" for n in _candidates()[:10]]


def _call(system: str, prompt: str, schema: dict, model: str, max_tokens: int,
          temperature: float = 0.8) -> dict:
    """One structured-JSON generateContent call with retry/backoff."""
    if not available():
        raise PipelineError("GEMINI_API_KEY is not set")
    model = _model_cache.get(model, model)
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "thinkingConfig": {"thinkingBudget": 1024},
        },
    }
    last = ""
    tried_discovery = False
    for attempt in range(1, 6):
        r = requests.post(f"{GEMINI_BASE}/models/{model}:generateContent",
                          headers={"x-goog-api-key": api_key(),
                                   "Content-Type": "application/json"},
                          json=body, timeout=300)
        if r.status_code in (404, 429) and not tried_discovery:
            tried_discovery = True  # configured model retired or has no quota: probe
            new = _discover_model()
            if new != model:
                print(f"[gemini] model {model!r} not usable (HTTP {r.status_code}), using {new!r}")
                _model_cache[model] = new
                model = new
                continue
        if r.status_code == 400 and "thinking" in r.text.lower() \
                and "thinkingConfig" in body["generationConfig"]:
            del body["generationConfig"]["thinkingConfig"]  # model doesn't take it
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"{r.status_code} {r.text[:200]}"
            time.sleep(8 * attempt)
            continue
        if not r.ok:
            exc = GeminiUnavailable if r.status_code in (401, 403, 404, 429) else PipelineError
            raise exc(f"Gemini error {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            cand = data["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"])
        except (KeyError, IndexError) as e:
            last = f"empty response: {str(data)[:200]}"
            time.sleep(4 * attempt)
            continue
        if cand.get("finishReason") == "MAX_TOKENS":
            last = "response truncated (MAX_TOKENS)"
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            last = f"bad JSON: {e}"
            continue
    raise GeminiUnavailable(f"Gemini call failed after retries: {last}")


# --------------------------------------------------------------------------- #
# schemas + prompts
# --------------------------------------------------------------------------- #
_SCENE = {
    "type": "OBJECT",
    "properties": {
        "heading": {"type": "STRING"},
        "narration": {"type": "STRING"},
        "queries": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["heading", "narration", "queries"],
}
_SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "title_en": {"type": "STRING"},
        "thumb_text": {"type": "STRING"},
        "description_hook": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "scenes": {"type": "ARRAY", "items": _SCENE},
    },
    "required": ["title", "title_en", "thumb_text", "description_hook", "tags", "scenes"],
}
_REVIEW_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING"},
        "problems": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["verdict", "problems"],
}
_TOPICS_SCHEMA = {
    "type": "OBJECT",
    "properties": {"topics": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["topics"],
}

_RULES = """\
You are a master Telugu storyteller writing narration for a history YouTube
channel for Telugu-speaking viewers. You write in natural, warm, SPOKEN Telugu
(వ్యావహారిక భాష), the way a gifted storyteller would tell it aloud to family.

ACCURACY (most important):
- Only real, well-documented history. Where historians disagree, or something
  is legend or folklore, say so plainly (for example "చరిత్రకారుల ప్రకారం",
  "జానపద కథనం ప్రకారం"). Never present legend as fact.
- Do not invent facts, names, numbers, dates, or quotations. If a date or
  figure is uncertain, say "సుమారు" (about) or leave it out. Do not put
  invented dialogue in the mouths of real people.
- If you are not sure a detail is true, leave it out.

TONE AND SAFETY:
- Family-friendly. No graphic violence, no glorifying war or cruelty. Mention
  battles and conflicts factually, with respect for everyone involved.
- No present-day politics, no communal framing, no blaming any community or
  religion, no claims that one faith, region, language or people is superior.
  Religion and temples may appear only as neutral historical or architectural
  context.
- No medical, financial or legal advice.

LANGUAGE RULES:
- All narration and headings in Telugu script. Short sentences (under ~20
  words) that sound good when read aloud. No Markdown, no bullet points, no
  stage directions, no emojis.
- Write proper nouns in Telugu script. Write years as digits (for example
  1565). Avoid English words unless there is no common Telugu word.
- Each scene's "queries" are 2 short ENGLISH search phrases for royalty-free
  stock footage that visually fit that scene: concrete scenery or objects
  (for example "ancient stone fort walls", "old map on parchment", "sunrise
  over temple ruins", "horse cavalry silhouette"). Nothing modern (no cars,
  phones, city traffic), no named people, no text.
- "tags": 8-12 items, the first four being English history keywords related to
  the story (for example "history", "ancient india", "empire", "kingdom"),
  then Telugu and English search keywords people would type.
- "title_en": a short English working title. "title": a gripping Telugu title
  under 70 characters. "description_hook": one intriguing Telugu sentence.
- "thumb_text": 2 to 4 Telugu words printed huge on the thumbnail. It must
  create curiosity or tension (a question, a shocking fact, a mystery) and
  must NOT repeat the words of the title. It must be true to the story.
"""

_LONG_SYSTEM = _RULES + """
FORMAT: a ~10-minute narrated story. 16 scenes, each scene's narration 40 to
52 Telugu words, for a TOTAL of 720 to 820 words. Structure: open with the most
gripping moment or an irresistible question; set the scene; build the
tension step by step; reach the turning point; resolve it; end with what this
story teaches or why it still matters. Each scene must move the story
forward - no filler, no repetition.
"""

_SHORT_SYSTEM = _RULES + """
FORMAT: a ~60-second vertical Short. 4 scenes, TOTAL 65 to 85 Telugu words.
Scene 1 is a one- or two-sentence hook that makes people stay. Then the single
most dramatic part of the story, told fast. Finish with a punchy last line.
"""

_REVIEW_SYSTEM = """\
You are a strict history fact-checker and a native Telugu editor. Review the
narration. Reply verdict "OK" only if ALL are true: every factual claim is
well established (or clearly labelled as legend/folklore); no invented
quotations, numbers or dates; nothing communal, political or sensitive beyond
neutral context; the Telugu reads as natural spoken Telugu with no English
sentences, garbled words or mixed-script glitches. Otherwise reply "REVISE" and
list each concrete problem briefly in English.
"""


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _words(text: str) -> int:
    return len(text.split())


def _telugu_ratio(text: str) -> float:
    letters = len(_TELUGU.findall(text)) + len(_LATIN.findall(text))
    return (len(_TELUGU.findall(text)) / letters) if letters else 0.0


def validate(data: dict, kind: str) -> list[str]:
    """Return a list of problems (empty = acceptable)."""
    lo, hi = LONG_WORDS if kind == "long" else SHORT_WORDS
    s_lo, s_hi = LONG_SCENES if kind == "long" else SHORT_SCENES
    problems: list[str] = []
    scenes = data.get("scenes") or []
    if not (s_lo <= len(scenes) <= s_hi):
        problems.append(f"{len(scenes)} scenes (need {s_lo}-{s_hi})")
    total = sum(_words(sc.get("narration", "")) for sc in scenes)
    if not (lo <= total <= hi):
        problems.append(f"{total} words (need {lo}-{hi})")
    title = (data.get("title") or "").strip()
    if not (6 <= len(title) <= 95) or _telugu_ratio(title) < 0.6:
        problems.append("title missing, wrong length, or not Telugu")
    if kind == "long":
        tt = (data.get("thumb_text") or "").strip()
        if not (1 <= len(tt.split()) <= 5) or _telugu_ratio(tt) < 0.6:
            problems.append("thumb_text must be 2-4 Telugu words")
    for i, sc in enumerate(scenes, 1):
        narr = sc.get("narration", "")
        if _telugu_ratio(narr) < 0.85:
            problems.append(f"scene {i}: narration is not mostly Telugu script")
        if re.search(r"[*#_`\[\]{}<>|]", narr):
            problems.append(f"scene {i}: contains markup characters")
        if not [q for q in sc.get("queries", []) if q.strip()]:
            problems.append(f"scene {i}: no stock-footage queries")
        if not (sc.get("heading") or "").strip():
            problems.append(f"scene {i}: no heading")
    return problems[:8]


def review(data: dict, model: str) -> tuple[bool, str]:
    text = "\n".join(f"{i}. {sc['narration']}" for i, sc in enumerate(data["scenes"], 1))
    try:
        out = _call(_REVIEW_SYSTEM,
                    f"TITLE: {data['title']} ({data.get('title_en', '')})\n\nNARRATION:\n{text}",
                    _REVIEW_SCHEMA, model, max_tokens=1500, temperature=0.0)
    except PipelineError as e:
        return True, f"review skipped ({e})"  # a flaky review must not block a run
    if str(out.get("verdict", "")).strip().upper().startswith("OK"):
        return True, "ok"
    return False, "; ".join(out.get("problems", []))[:600] or "flagged"


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def to_markdown(data: dict) -> str:
    """Convert a validated script dict into the pipeline's script format."""
    def one_line(s: str) -> str:
        return " ".join(str(s).split())

    tags = [one_line(t).replace(",", " ") for t in data.get("tags", []) if str(t).strip()]
    lines = ["---", f"title: {one_line(data['title'])}",
             f"description_hook: {one_line(data.get('description_hook', ''))}",
             f"tags: {', '.join(tags[:12])}", "lang: te"]
    if one_line(data.get("thumb_text", "")):
        lines.append(f"thumb_text: {one_line(data['thumb_text'])}")
    lines += ["---", ""]
    for sc in data["scenes"]:
        qs = [one_line(q) for q in sc.get("queries", []) if str(q).strip()][:2]
        lines.append(f"## {one_line(sc['heading'])}")
        lines.append(one_line(sc["narration"]))
        lines.append(" ".join(f"[[{q}]]" for q in qs))
        lines.append("")
    return "\n".join(lines) + "\n"


def _generate(kind: str, prompt: str, model: str, attempts: int = 3,
              self_review: bool = True) -> dict:
    system = _LONG_SYSTEM if kind == "long" else _SHORT_SYSTEM
    max_tokens = 24000 if kind == "long" else 8000
    feedback = ""
    last = "no attempt made"
    content_failed = False  # the model answered but the script wasn't acceptable
    for n in range(1, attempts + 1):
        full = prompt + (f"\n\nFix these problems from the previous attempt: {feedback}"
                         if feedback else "")
        try:
            data = _call(system, full, _SCRIPT_SCHEMA, model, max_tokens)
        except GeminiUnavailable:
            raise  # key/quota/outage: retrying the same call in a loop won't help
        except PipelineError as e:
            last = str(e)
            feedback = ""
            continue
        problems = validate(data, kind)
        if problems:
            content_failed = True
            last = feedback = "; ".join(problems)
            print(f"[telugu] attempt {n}: {last}")
            continue
        if self_review:
            ok, note = review(data, model)
            if not ok:
                content_failed = True
                last = feedback = f"fact-check/language review flagged: {note}"
                print(f"[telugu] attempt {n}: {last}")
                continue
        return data
    exc = PipelineError if content_failed else GeminiUnavailable
    raise exc(f"no acceptable Telugu {kind} script after {attempts} tries: {last}")


def generate_long(topic: str, model: str = DEFAULT_MODEL, **kw) -> dict:
    return _generate("long", f"Tell this history story: {topic}", model, **kw)


def generate_short(topic: str, long_data: dict | None = None,
                   model: str = DEFAULT_MODEL, **kw) -> dict:
    if long_data:
        gist = " ".join(sc["narration"] for sc in long_data["scenes"])[:3500]
        prompt = (f"Write a 60-second Short from this history story: "
                  f"{long_data.get('title_en') or topic}.\n\n"
                  f"The full Telugu story, for reference (keep to its facts only):\n{gist}")
    else:
        prompt = f"Write a 60-second Short telling the most dramatic part of: {topic}"
    return _generate("short", prompt, model, **kw)


def new_topics(existing: list[str], n: int = 40, model: str = DEFAULT_MODEL) -> list[str]:
    """Ask Gemini for more story topics (English one-liners), avoiding repeats."""
    avoid = "; ".join(existing[-120:])
    system = ("You suggest topics for a Telugu history-storytelling YouTube channel: "
              "well-documented, dramatic true stories from Indian (especially "
              "Telugu-region) and world history, family-friendly, nothing political, "
              "communal or religious-polemical.")
    out = _call(system,
                f"Give {n} new story topics as short English one-line titles with a "
                f"hint of the angle, e.g. 'The Kohinoor diamond's journey out of "
                f"Golconda'. Do NOT repeat or closely resemble any of these: {avoid}",
                _TOPICS_SCHEMA, model, max_tokens=4000, temperature=0.9)
    seen = {t.lower() for t in existing}
    return [t.strip() for t in out.get("topics", [])
            if t.strip() and t.strip().lower() not in seen][:n]
