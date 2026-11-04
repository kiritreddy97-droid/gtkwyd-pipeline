"""'How it's made / how it works' explainer Shorts for any product in the world.

Gemini writes a ~60-second step-by-step script (hook, then raw material -> machine ->
finished product), as English or Telugu narration, with a clean 3D-render picture prompt and a
real factory-footage search for every step. make_video.py then voices it, draws the pictures,
mixes in real stock clips, adds camera moves and word-by-word captions.
"""
from __future__ import annotations

import re

from . import writer_te
from .util import PipelineError

LIMITS = {"en": (95, 175), "te": (60, 100)}  # total narration words
SCENES = (8, 12)

_SYSTEM = """\
You write a ~60-second vertical "how it's made / how it works" explainer Short about a
product or everyday thing, in the style of viral 3D explainer reels.

STRUCTURE: scene 1 is a one-sentence hook (a surprising fact or a question about the thing).
Then 7 to 9 scenes that follow the real process in order (raw materials, the key machines
and steps, the finished product, how it works in use). The last scene is a short, surprising
payoff. Each scene's narration is ONE short sentence of {words} words, so the picture
changes every few seconds.

ACCURACY: only real, well-documented processes. Where factories differ, say so ("in most
factories") and stay generic. Never invent numbers, brands or claims. Family-friendly.
No medical, financial or political content.

LANGUAGE: {language_rule}

PICTURES: each scene's "image_prompts" are 2 ENGLISH descriptions of two different moments of
that step, as a clean 3D render: the raw material, the machine, a cutaway of the inside, a
close-up of the part being made. One or two large subjects, simple background. No people's
faces (hands or silhouettes are fine), no text, no labels, no logos, no readable signs.
Each scene's "queries" are 2 short ENGLISH search phrases for REAL factory or process stock
footage that fits the step (for example "factory assembly line", "molten metal pouring",
"cutting wood in a sawmill"). Nothing with named people or brands.

"title": a curiosity-driven title under 70 characters ({title_rule}). "title_en": the English
working title. "thumb_text": 2-3 words. "description_hook": one intriguing sentence.
"tags": 8-12 items: the first four English ("how it's made", "how it works",
"manufacturing", "explained"), then the product and related words.
"""

_RULES = {
    "en": dict(words="9 to 15", language_rule="clear, energetic, conversational English.",
               title_rule="for example 'How a Pencil Is Made'"),
    "te": dict(words="7 to 11",
               language_rule=("natural, warm SPOKEN Telugu in Telugu script (వ్యావహారిక భాష), "
                              "short sentences. Write the product and technical terms in Telugu "
                              "script too. No English letters in the narration."),
               title_rule="in Telugu script"),
}


def validate(data: dict, lang: str) -> list[str]:
    lo, hi = LIMITS[lang]
    probs: list[str] = []
    scenes = data.get("scenes") or []
    if not SCENES[0] <= len(scenes) <= SCENES[1]:
        probs.append(f"{len(scenes)} scenes (need {SCENES[0]}-{SCENES[1]})")
    total = sum(len(sc.get("narration", "").split()) for sc in scenes)
    if not lo <= total <= hi:
        probs.append(f"{total} words (need {lo}-{hi})")
    for i, sc in enumerate(scenes, 1):
        narr = sc.get("narration", "")
        if re.search(r"[*#_`\[\]{}<>|]", narr):
            probs.append(f"scene {i}: markup characters")
        if lang == "te" and writer_te._telugu_ratio(narr) < 0.85:
            probs.append(f"scene {i}: narration is not Telugu")
        if lang == "en" and writer_te._telugu_ratio(narr) > 0.1:
            probs.append(f"scene {i}: narration is not English")
        if not [p for p in sc.get("image_prompts", []) if p.strip()]:
            probs.append(f"scene {i}: no image prompts")
        if not [q for q in sc.get("queries", []) if q.strip()]:
            probs.append(f"scene {i}: no stock queries")
    if lang == "te" and writer_te._telugu_ratio(data.get("title", "")) < 0.6:
        probs.append("title is not Telugu")
    return probs[:6]


def generate(topic: str, lang: str = "en", model: str = writer_te.DEFAULT_MODEL,
             attempts: int = 3) -> dict:
    system = _SYSTEM.format(**_RULES[lang])
    feedback = ""
    last = "no attempt"
    for _ in range(attempts):
        prompt = f"Write the explainer Short about: {topic}" + (
            f"\n\nFix these problems from the previous attempt: {feedback}" if feedback else "")
        data = writer_te._call(system, prompt, writer_te._SCRIPT_SCHEMA, model, 8000, 0.8)
        probs = validate(data, lang)
        if not probs:
            return data
        last = feedback = "; ".join(probs)
        print(f"[explainer] attempt problems: {last}")
    raise PipelineError(f"no acceptable explainer after {attempts} tries: {last}")


def to_markdown(data: dict, lang: str) -> str:
    md = writer_te.to_markdown(data)
    extra = f"lang: {lang}\nformat: explainer"
    md = md.replace("\nlang: te\n", f"\n{extra}\n", 1)
    return md
