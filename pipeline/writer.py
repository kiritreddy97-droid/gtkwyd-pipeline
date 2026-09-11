"""Generate an on-brand video script from a topic using a local Ollama model.

Ollama runs entirely on this machine (no API key, no cost). Install it with
setup-auto.ps1. If Ollama is unavailable, callers should fall back to the
hand-written script bank.

Small local models do not follow a format spec reliably, so `_normalize` is
forgiving: it strips stage directions, "Narrator:" prefixes, single-bracket
markers, and re-segments free-form output into scenes. Every result is then run
through pipeline.guardrails before it is accepted.
"""
from __future__ import annotations

import re
import textwrap

import requests

from . import guardrails
from .util import PipelineError

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:3b"

_EXAMPLE = textwrap.dedent("""\
    ---
    title: 6 Facts About Honeybees That Sound Made Up
    description_hook: A honeybee colony makes decisions with nobody in charge.
    tags: honeybees, bees, insects, nature, animal facts, biology
    ---

    ## No one is in charge
    The queen does not give orders. Thousands of workers react to each other,
    and the colony's behaviour emerges from those small interactions.
    [[honeybees on honeycomb]] [[beehive close up]]

    ## They vote by dancing
    Scout bees advertise new nest sites with a waggle dance. The better the
    site, the longer the dance, and the swarm leaves once enough scouts agree.
    [[bee waggle dance]] [[swarm of bees on branch]]
    """)

_SYSTEM = textwrap.dedent(f"""\
    You write short factual scripts for a YouTube channel called
    "Get To Know What You Don't". Each video presents {guardrails.CHANNEL_THEME}.

    HARD RULES:
    - Only facts found in mainstream encyclopedias and textbooks. If unsure a
      fact is true and well known, leave it out.
    - No medical, financial, legal, current-political, religious, or
      controversial content. No advice to the viewer. Third person, declarative.
    - No clickbait or exaggeration.
    - Calm, clear, curious tone. Plain words. Short sentences.

    FORMAT - copy this structure EXACTLY. 5 or 6 scenes. Each scene: a "## "
    heading, then 2-3 sentences of narration, then one line with two
    [[double-bracket]] image search phrases. No other text, no "Narrator:",
    no "[Scene: ...]".

    EXAMPLE:
{textwrap.indent(_EXAMPLE, "    ")}
    """)

_SYSTEM_SHORT = textwrap.dedent(f"""\
    You write scripts for 40-second YouTube Shorts for the channel
    "Get To Know What You Don't" ({guardrails.CHANNEL_THEME}).

    HARD RULES:
    - Only well-known, textbook-accurate facts. No medical, financial,
      current-political, religious, or controversial content. No advice.
    - Hook the viewer in the very first sentence.
    - Punchy. Short sentences. One single idea for the whole Short - do not
      drift to a second topic.

    FORMAT - copy EXACTLY. 3 scenes only. Each scene: a "## " heading, then
    2 sentences, then one line with two [[double-bracket]] image phrases.
    End the last scene's image line with [[question everything text]].

    EXAMPLE:
    ---
    title: The Confidence Trap in Your Brain
    description_hook: The less you know about something, the more certain you tend to feel.
    tags: psychology, cognitive bias, brain, mind, human behaviour, learning
    ---

    ## The pattern
    People who score lowest on a test often rate their performance the highest.
    Psychologists call it the Dunning-Kruger effect.
    [[confident person talking]] [[rising graph]]

    ## Why it happens
    The skill you would need to do well is the same skill you need to spot your
    own mistakes. Without it, the mistakes stay invisible.
    [[person studying]] [[puzzle pieces]]

    ## The flip side
    True experts often underrate themselves, assuming what feels easy to them is
    easy for everyone.
    [[expert working calmly]] [[question everything text]]
    """)


def _ollama_up() -> bool:
    try:
        return requests.get(f"{OLLAMA_URL}/api/tags", timeout=3).ok
    except requests.RequestException:
        return False


def _generate_once(topic: str, model: str, kind: str = "video") -> str:
    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": model,
            "system": _SYSTEM_SHORT if kind == "short" else _SYSTEM,
            "prompt": f"Topic: {topic}\n\nWrite the script now, in the exact "
                      f"format from the example. Reply with only the markdown.",
            "stream": False,
            "options": {"temperature": 0.3,
                        "num_predict": 450 if kind == "short" else 900,
                        "top_p": 0.9},
        },
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


# --------------------------------------------------------------------------- #
# normalisation: turn messy model output into a valid script
# --------------------------------------------------------------------------- #
_FENCE = re.compile(r"```[a-z]*\n?|```")
_BRACKET_QUERY = re.compile(
    r"\[\[?\s*(?:stock(?: footage)?(?: search)?(?: phrase)?|b-?roll|visual|scene)?"
    r"\s*[:=]?\s*(.+?)\s*\]\]?", re.I)
_STAGE = re.compile(r"\[[^\]]*\]")
_SPEAKER = re.compile(r"^\s*(narrator|voice ?over|vo|host)\s*[:\-]\s*", re.I)
_SCENE_LABEL = re.compile(r"^\s*(scene|part)\s*\d+\s*[:\-.]?\s*", re.I)
_STOPWORDS = set("the a an of to in on and or for with is are was were be by "
                 "that this these those from as at into how why what".split())


def _kw(text: str, extra: str = "") -> str:
    words = re.findall(r"[A-Za-z][A-Za-z'-]+", f"{extra} {text}")
    keep = [w for w in words if w.lower() not in _STOPWORDS]
    return " ".join(keep[:5]) or text.strip()[:40] or "abstract background"


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _frontmatter(raw: str, topic: str) -> tuple[dict, str]:
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.DOTALL)
    meta: dict = {}
    body = raw
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip()
        body = m.group(2)
    else:
        # pull a leading "title: ..." / "tags: ..." block if present
        for line in raw.splitlines()[:6]:
            mm = re.match(r"^(title|description_hook|tags)\s*[:\-]\s*(.+)$", line, re.I)
            if mm:
                meta[mm.group(1).lower()] = mm.group(2).strip()
                body = body.replace(line, "", 1)
    meta.setdefault("title", topic.strip().capitalize())
    meta.setdefault("tags", _kw(topic).lower().replace(" ", ", "))
    return meta, body


def _scene_blocks(body: str) -> list[tuple[str, str]]:
    """Return [(heading, raw_text)]. Handles '## h', 'Scene 1:', or a blob."""
    lines = body.splitlines()
    blocks: list[tuple[str, list[str]]] = []
    heading = None
    for line in lines:
        h = re.match(r"^\s{0,3}#{1,4}\s+(.*)$", line)
        s = re.match(r"^\s*(?:scene|part)\s*\d+\s*[:\-.]\s*(.*)$", line, re.I)
        if h or s:
            heading = (h.group(1) if h else s.group(1)).strip(" :*-") or None
            blocks.append((heading or "", []))
        else:
            if not blocks:
                blocks.append(("", []))
            blocks[-1][1].append(line)
    merged = [(hd, "\n".join(ls).strip()) for hd, ls in blocks]
    merged = [b for b in merged if b[1] or b[0]]

    # a single blob -> split into ~5 scenes by sentences
    if len(merged) <= 1:
        text = merged[0][1] if merged else body
        text = _SPEAKER.sub("", _STAGE.sub("", text))
        sents = _split_sentences(text)
        if len(sents) < 8:
            return [("", " ".join(sents))]
        per = max(2, round(len(sents) / 5))
        out = []
        for i in range(0, len(sents), per):
            chunk = " ".join(sents[i:i + per])
            out.append((_kw(chunk).title(), chunk))
        return out[:6]
    return merged


def _clean_narration(text: str) -> str:
    text = _BRACKET_QUERY.sub("", text)
    text = _STAGE.sub("", text)
    lines = []
    for ln in text.splitlines():
        ln = _SPEAKER.sub("", ln)
        ln = _SCENE_LABEL.sub("", ln)
        lines.append(ln)
    text = " ".join(lines)
    return re.sub(r"\s+", " ", text).strip(" -*:")


def _queries_from(raw_text: str, heading: str, topic: str) -> list[str]:
    qs = []
    for m in _BRACKET_QUERY.finditer(raw_text):
        q = re.sub(r'^["\']|["\']$', "", m.group(1)).strip()
        q = re.sub(r"\s+", " ", q)
        if 3 <= len(q) <= 60 and not q.lower().startswith(("http", "www")):
            qs.append(q)
    for m in re.finditer(r"\[scene\s*[:\-]\s*(.+?)\]", raw_text, re.I):
        qs.append(_kw(m.group(1)))
    if not qs:
        qs = [_kw(heading, topic), _kw(topic)]
    # de-dupe, keep 1-2
    seen, out = set(), []
    for q in qs:
        k = q.lower()
        if k not in seen:
            seen.add(k)
            out.append(q)
    return out[:2]


def _normalize(raw: str, topic: str) -> str:
    raw = _FENCE.sub("", raw).strip()
    idx = raw.find("---")
    if 0 < idx < 40:
        raw = raw[idx:]
    meta, body = _frontmatter(raw, topic)

    scenes = []
    for heading, chunk in _scene_blocks(body):
        narr = _clean_narration(chunk)
        if len(narr.split()) < 6:
            continue
        qs = _queries_from(chunk, heading or topic, topic)
        head = heading.strip() or _kw(narr).title()
        scenes.append((head[:60], narr, qs))

    if len(scenes) > 7:
        scenes = scenes[:6]

    title = re.sub(r'^["\']|["\']$', "", meta["title"]).strip()
    tags = meta.get("tags", "")
    hook = meta.get("description_hook", "").strip().strip('"')

    lines = ["---", f"title: {title}"]
    if hook:
        lines.append(f"description_hook: {hook}")
    lines += [f"tags: {tags}", "---", ""]
    for head, narr, qs in scenes:
        lines.append(f"## {head}")
        lines.append(narr)
        lines.append(" ".join(f"[[{q}]]" for q in qs))
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# self-review (accuracy net when the review gate is off)
# --------------------------------------------------------------------------- #
_REVIEW_PROMPT = textwrap.dedent("""\
    Fact-check this short script. Reply with exactly "OK" if every statement is
    standard textbook knowledge. Otherwise, briefly name the statements that are
    incorrect or that you are not confident about.

    SCRIPT:
    {script}
    """)


def _self_review(script_md: str, model: str) -> tuple[bool, str]:
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": _REVIEW_PROMPT.format(script=script_md),
                  "stream": False, "options": {"temperature": 0.0, "num_predict": 250}},
            timeout=300,
        )
        resp.raise_for_status()
        verdict = resp.json().get("response", "").strip()
    except requests.RequestException as e:
        return True, f"review skipped ({e})"
    clean = verdict.upper().replace("*", "").strip()
    if clean.startswith("OK") or clean in ("", "NONE", "NONE.", "N/A"):
        return True, "ok"
    hard = re.search(r"\b(incorrect|false|not true|inaccurate|wrong|misleading|"
                     r"fabricat\w+|no evidence|it is a myth|debunk\w*)\b", verdict, re.I)
    many = verdict.count("\n") >= 4 or len(verdict) > 600
    if hard or many:
        return False, verdict[:400]
    return True, "ok (minor notes ignored)"


_NEWS_SYSTEM = textwrap.dedent(f"""\
    You write short explainer scripts for the YouTube channel
    "Get To Know What You Don't". You are given a real news headline and summary
    from a reputable science or technology outlet. Explain it clearly for a
    general audience.

    HARD RULES:
    - Stick to what the summary actually says. Do not invent numbers, names,
      quotes, or outcomes. If a detail is not in the summary, do not state it.
    - Give context: what this is, why it is interesting, what is genuinely new.
    - Neutral and factual. No hype, no "this changes everything", no politics,
      no speculation about the future beyond what the source says.
    - Third person, declarative. Calm and curious.

    FORMAT - copy this structure EXACTLY. 4 or 5 scenes. Each scene: a "## "
    heading, then 2-3 sentences, then one line with two [[double-bracket]] image
    search phrases (use concrete visual nouns, not the headline).

    EXAMPLE:
{textwrap.indent(_EXAMPLE, "    ")}
    """)


def generate_news_script(headline: str, summary: str, source: str,
                         model: str = DEFAULT_MODEL, attempts: int = 3) -> str:
    from .script_parser import parse_script_text

    prompt = (
        f"HEADLINE: {headline}\nSOURCE: {source}\nSUMMARY: {summary}\n\n"
        "Write the explainer script now, in the exact format. Only use facts "
        "from the summary. Reply with only the markdown."
    )
    last: list[str] = []
    for _ in range(attempts):
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": model, "system": _NEWS_SYSTEM, "prompt": prompt,
                      "stream": False,
                      "options": {"temperature": 0.25, "num_predict": 800}},
                timeout=600,
            )
            resp.raise_for_status()
            md = _normalize(resp.json().get("response", ""), headline)
            script = parse_script_text(md, fallback_title=headline)
        except Exception as e:  # noqa: BLE001
            last = [str(e)]
            continue
        t_ok, t_why = guardrails.check_title(script.title)
        s_ok, s_issues = guardrails.check_script(
            " ".join(sc.narration for sc in script.scenes))
        if t_ok and s_ok and 3 <= len(script.scenes) <= 6:
            return md
        last = ([] if t_ok else [f"title: {t_why}"]) + s_issues
    raise PipelineError(f"news script failed for {headline!r}: {' | '.join(last)}")


# --------------------------------------------------------------------------- #
def generate_script(topic: str, model: str = DEFAULT_MODEL, attempts: int = 3,
                    self_review: bool = True, kind: str = "video") -> str:
    """Return validated script markdown, or raise PipelineError."""
    ok, why = guardrails.check_topic(topic)
    if not ok:
        raise PipelineError(f"topic rejected ({why}): {topic}")
    if not _ollama_up():
        raise PipelineError(
            "Ollama is not running. Start it (setup-auto.ps1), or the run will "
            "fall back to the script bank."
        )

    from .script_parser import parse_script_text

    scene_lo, scene_hi = (3, 4) if kind == "short" else (4, 7)
    last: list[str] = []
    for _ in range(attempts):
        try:
            md = _normalize(_generate_once(topic, model, kind), topic)
            script = parse_script_text(md, fallback_title=topic)
        except (PipelineError, Exception) as e:  # noqa: BLE001 - any failure -> retry
            last = [f"normalise/parse: {e}"]
            continue

        t_ok, t_why = guardrails.check_title(script.title)
        s_ok, s_issues = guardrails.check_script(
            " ".join(sc.narration for sc in script.scenes), kind=kind)
        n_ok = scene_lo <= len(script.scenes) <= scene_hi
        if not (t_ok and s_ok and n_ok):
            last = ([] if t_ok else [f"title: {t_why}"]) + s_issues
            if not n_ok:
                last.append(f"{len(script.scenes)} scenes (need {scene_lo}-{scene_hi})")
            continue

        if self_review:
            good, note = _self_review(md, model)
            if not good:
                last = [f"self-review flagged: {note}"]
                continue
        return md

    raise PipelineError(
        f"no clean script for {topic!r} after {attempts} tries. "
        f"last issues: {' | '.join(last)}"
    )
