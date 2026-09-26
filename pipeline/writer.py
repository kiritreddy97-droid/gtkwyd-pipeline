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
    title: 8 Facts About Honeybees That Sound Made Up
    description_hook: A honeybee colony makes decisions with nobody in charge.
    tags: honeybees, bees, insects, nature, animal facts, biology
    ---

    ## No one is in charge
    The queen does not give orders, and no single bee has a plan for the
    colony. Thousands of workers react only to their immediate neighbours,
    through touch, scent, and movement, and the colony's larger behaviour
    emerges from millions of these small, local interactions. A hive can
    react to a threat, a new food source, or a change in outside temperature
    within minutes, all without a single bee understanding the whole picture.
    Biologists call this kind of group intelligence "swarm behaviour," and
    the same basic pattern shows up in ant colonies, termite mounds, and even
    flocks of starlings moving together in the sky.
    [[honeybees on honeycomb]] [[beehive close up]]

    ## They vote by dancing
    Scout bees advertise new nest sites with a waggle dance, a figure-eight
    movement whose angle from vertical points toward the site's direction and
    whose length and vigour signal how far away and how good it is. The
    better the site, the longer and more energetic the dance, and other
    scouts fly out to check it for themselves before returning to dance in
    support if they agree. The swarm only leaves once enough scouts have
    converged on the same site, sometimes after days of this slow, silent,
    decentralised vote. Researchers who have tracked thousands of real swarms
    find the same reliable decision-making process every time, almost like a
    parliament with no chairperson.
    [[bee waggle dance]] [[swarm of bees on branch]]

    ## A single stomach for thousands
    Worker bees constantly share food mouth to mouth in a process called
    trophallaxis, passing nectar, water, and chemical signals from bee to bee
    across the entire colony within a matter of hours. This shared "social
    stomach" is also how a hive collectively senses whether it has stored
    enough food to survive the coming winter, since the flow of food slows
    and speeds up depending on what is available. A single colony can hold
    over 50,000 bees at its summer peak, and this constant exchange is what
    keeps all of them informed and coordinated.
    [[bees feeding each other]] [[honeycomb close up]]

    ## The hive keeps its own climate
    Bees actively heat and cool their hive to keep the brood nest within a
    narrow band around 35 degrees Celsius, all year round, regardless of the
    weather outside. In summer, workers line up at the entrance and fan their
    wings to pull cooler air through the hive like a living air conditioner.
    In winter, thousands of bees cluster tightly together around the queen
    and vibrate their flight muscles without moving their wings, generating
    heat through muscle activity alone. Beekeepers who have measured hive
    interiors find the temperature rarely drifts more than a degree or two
    either way, even during a hard freeze.
    [[bees fanning wings]] [[winter beehive snow]]

    ## Drones exist for one purpose
    Male bees, called drones, do not forage for food, defend the hive, or
    help build the comb the way female worker bees do. Their only biological
    role is to mate with a new queen from a different colony during a brief
    flight, and the vast majority never get the chance to succeed at all. In
    autumn, once the mating season ends and food becomes scarce, worker bees
    often physically push the remaining drones out of the hive, since feeding
    them through winter would offer the colony nothing in return.
    [[drone bee]] [[bees at hive entrance]]

    ## Honey never spoils
    Properly sealed honey has such low moisture content and high acidity that
    bacteria and most microorganisms simply cannot grow in it, which is why
    archaeologists have found sealed pots of honey in Egyptian tombs that were
    thousands of years old and still perfectly edible. Bees achieve this by
    repeatedly passing nectar between bees and fanning it with their wings to
    evaporate its water content down to around 17 percent, before finally
    capping each cell with a thin layer of wax. Modern honey, stored the same
    simple way, can outlast almost any other food humans commonly keep.
    [[honey jar close up]] [[ancient pottery]]

    ## They remember and they learn
    Honeybees can learn to associate particular colours and scents with a
    food reward after only a handful of trials, a type of fast learning
    scientists once assumed required a much larger brain. Some experiments
    even suggest bees can distinguish between individual human faces shown in
    photographs, using the same strategy they use to recognise flower
    patterns. Their brains are smaller than a grain of rice, yet they
    reliably solve navigation, memory, and pattern-recognition tasks that
    challenge animals many times their size.
    [[bee on flower]] [[bee close up eyes]]

    ## A colony can outlive its queen many times over
    Individual worker bees typically live only a few weeks during the busy
    summer months, worked to exhaustion foraging and building. Yet a healthy
    colony as a whole can persist for many years or even decades, continuously
    replacing its queen, its workers, and every structure inside the hive.
    What looks from the outside like one single, long-lived creature is
    really a constantly renewing population, each generation carrying out the
    same instructions the last one followed.
    [[old beehive]] [[bees swarming]]
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

    FORMAT - copy this structure EXACTLY. 8 to 10 scenes (this is a 4-6 minute
    video, not a Short - go deep on each fact instead of listing many shallow
    ones). Each scene: a "## " heading, then 4-6 sentences of narration that
    explain the fact with real context, numbers, or comparisons (not just
    restate it), then one line with two [[double-bracket]] image search
    phrases. Aim for roughly 650-800 words of narration in total. No other
    text, no "Narrator:", no "[Scene: ...]".

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
                        "num_predict": 450 if kind == "short" else 1700,
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


def _normalize(raw: str, topic: str, kind: str = "video") -> str:
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

    # generous grace margin above each kind's target scene count (see
    # scene_lo/scene_hi in generate_script / the 5-8 check in
    # generate_news_script) - this used to hard-cap at 6 regardless of kind,
    # which silently truncated every longer video script back down to ~1
    # minute's worth of scenes before the length check ever saw it.
    cap = {"short": 4, "reel": 4, "news": 8}.get(kind, 12)
    if len(scenes) > cap:
        scenes = scenes[:cap]

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

_STORY_REVIEW_PROMPT = textwrap.dedent("""\
    Review this short fictional story. Reply with exactly "OK" if all of the
    following are true: it is an original story (not a retelling or lightly
    renamed version of an existing book, film, show, or real public figure's
    story), it stays family-friendly (no violence, death on-screen, or content
    beyond mild peril), and it avoids politics or religion. Otherwise, briefly
    name what's wrong.

    STORY:
    {script}
    """)

_REEL_REVIEW_PROMPT = textwrap.dedent("""\
    Review this short narration for a satisfying/soothing/fitness-tip social
    video. Reply with exactly "OK" if all of the following are true: any
    factual or how-to claim is standard, uncontroversial knowledge (never
    diet, weight-loss, supplement, or medical advice), the tone stays calm
    and family-friendly, and it avoids politics or religion. Otherwise,
    briefly name what's wrong.

    NARRATION:
    {script}
    """)


def _self_review(script_md: str, model: str, kind: str = "video") -> tuple[bool, str]:
    prompt_template = {"story": _STORY_REVIEW_PROMPT,
                       "reel": _REEL_REVIEW_PROMPT}.get(kind, _REVIEW_PROMPT)
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": prompt_template.format(script=script_md),
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

    FORMAT - copy this structure EXACTLY. 5 to 7 scenes. Each scene: a "## "
    heading, then 3-4 sentences, then one line with two [[double-bracket]] image
    search phrases (use concrete visual nouns, not the headline). Use as much
    genuine detail as the summary actually supports - typically 300-450 words
    total. Never pad with restatement or speculation just to add length.

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
                      "options": {"temperature": 0.25, "num_predict": 1000}},
                timeout=600,
            )
            resp.raise_for_status()
            md = _normalize(resp.json().get("response", ""), headline, kind="news")
            script = parse_script_text(md, fallback_title=headline)
        except Exception as e:  # noqa: BLE001
            last = [str(e)]
            continue
        t_ok, t_why = guardrails.check_title(script.title)
        s_ok, s_issues = guardrails.check_script(
            " ".join(sc.narration for sc in script.scenes), kind="news")
        if t_ok and s_ok and 5 <= len(script.scenes) <= 8:
            return md
        last = ([] if t_ok else [f"title: {t_why}"]) + s_issues
    raise PipelineError(f"news script failed for {headline!r}: {' | '.join(last)}")


_STORY_EXAMPLE = textwrap.dedent("""\
    ---
    title: The Stranger's Umbrella
    description_hook: An old man forgets his umbrella on a bus, and a stranger spends the whole ride trying to return it.
    tags: short story, kindness, strangers, bus, rain
    ---

    ## The bus
    Rain streaked the windows of the evening bus as Arthur, seventy-eight and
    slow on his feet, shuffled toward the back and lowered himself into a
    seat. He set his umbrella against the window, the way he always did, and
    closed his eyes.
    [[elderly man on rainy bus]] [[rain on bus window]]

    ## The stop
    Two stops later he rose stiffly and stepped down into the rain, forgetting
    the umbrella entirely. A young woman near the front, headphones still in,
    caught the movement out of the corner of her eye and saw it leaning there,
    already forgotten.
    [[bus stop in rain]] [[young woman on bus]]

    ## The decision
    She could have left it. It was raining, she had somewhere to be, and he
    was already three doors down the street. Instead she grabbed it, called
    to the driver to wait, and stepped off into the downpour after him.
    [[woman running in rain]] [[umbrella on empty seat]]

    ## Catching up
    "Sir! Your umbrella!" she called, jogging to close the distance, rain
    soaking through her jacket. Arthur turned, confused for a moment, then
    saw what she was holding and broke into a slow, surprised smile.
    [[woman calling out in rain]] [[elderly man turning around]]

    ## A small trade
    He thanked her and, before she could protest, pressed a folded five dollar
    bill into her hand "for the trouble." She tried to refuse it, but he was
    already walking away, waving off her objection with one hand.
    [[hands exchanging money]] [[man walking away in rain]]

    ## What she found later
    Home and dry, she unfolded the bill to put it in a drawer and stopped. In
    careful handwriting on the inside, someone had written a phone number and
    three words: "call your mother."
    [[folded bill on table]] [[handwriting close up]]

    ## The call
    She sat with it for a long moment before picking up her phone. It rang
    twice. When her mother answered, surprised to hear from her on a
    Tuesday, she said the only thing that came to mind: "I just wanted to
    hear your voice."
    [[woman looking at phone]] [[phone call in dim room]]

    ## Ordinary evening
    Outside, the rain kept falling on a street where a stranger's small,
    deliberate kindness had already moved on to someone else entirely,
    the way these things do, unnoticed and unrepeated.
    [[rainy street at night]] [[city lights reflected in rain]]
    """)

_STORY_SYSTEM = textwrap.dedent(f"""\
    You write short, ORIGINAL fictional stories, narrated like a short film,
    for the channel "Get To Know What You Don't". You are given a one-line
    premise or theme. Write a complete, satisfying story around it - not a
    summary of one, an actual story with real narrative beats.

    HARD RULES:
    - The story must be entirely ORIGINAL - never retell, adapt, or lightly
      rename an existing book, film, show, or public figure's story. Invent
      your own characters, names, and specifics from the premise.
    - Family-friendly: no violence, gore, death of a character on-screen,
      romance beyond warmth, or frightening/horror content. No politics,
      religion, or real public figures.
    - Third person, past tense. Give it a real shape: a setup, something that
      shifts or is discovered partway through, and a resolution or quiet
      final beat - like the example below, not a flat list of events.
    - Concrete and visual: every scene should describe something a camera
      could actually show, so a matching stock clip can be found for it.

    FORMAT - copy this structure EXACTLY. 8 to 10 scenes, telling ONE
    continuous story in order (not disjointed clips - each scene continues
    directly from the last). Each scene: a "## " heading, then 3-5 sentences
    of narration, then one line with two [[double-bracket]] visual search
    phrases describing what's on screen in that scene (concrete nouns: a
    person, a place, an object, an action - not abstract ideas). Aim for
    roughly 500-650 words of narration in total.

    EXAMPLE:
{textwrap.indent(_STORY_EXAMPLE, "    ")}
    """)


def generate_story_script(theme: str, model: str = DEFAULT_MODEL, attempts: int = 3,
                          self_review: bool = True) -> str:
    """Return validated fictional-story script markdown, or raise PipelineError."""
    ok, why = guardrails.check_topic(theme)
    if not ok:
        raise PipelineError(f"story theme rejected ({why}): {theme}")
    if not _ollama_up():
        raise PipelineError(
            "Ollama is not running. Start it (setup-auto.ps1), or the run will "
            "fall back to the story bank."
        )

    from .script_parser import parse_script_text

    prompt = (f"Premise: {theme}\n\nWrite the story now, in the exact format "
             f"from the example. Reply with only the markdown.")
    last: list[str] = []
    for _ in range(attempts):
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": model, "system": _STORY_SYSTEM, "prompt": prompt,
                      "stream": False,
                      "options": {"temperature": 0.8, "num_predict": 1500}},
                timeout=600,
            )
            resp.raise_for_status()
            md = _normalize(resp.json().get("response", ""), theme, kind="story")
            script = parse_script_text(md, fallback_title=theme)
        except (PipelineError, Exception) as e:  # noqa: BLE001
            last = [f"normalise/parse: {e}"]
            continue

        t_ok, t_why = guardrails.check_title(script.title, kind="story")
        s_ok, s_issues = guardrails.check_script(
            " ".join(sc.narration for sc in script.scenes), kind="story")
        n_ok = 6 <= len(script.scenes) <= 12
        if not (t_ok and s_ok and n_ok):
            last = ([] if t_ok else [f"title: {t_why}"]) + s_issues
            if not n_ok:
                last.append(f"{len(script.scenes)} scenes (need 6-12)")
            continue

        if self_review:
            ok2, note = _self_review(md, model, kind="story")
            if not ok2:
                last = [f"self-review flagged: {note}"]
                continue
        return md

    raise PipelineError(f"no clean story for {theme!r} after {attempts} tries. "
                        f"last issues: {' | '.join(last)}")


# --------------------------------------------------------------------------- #
# Instagram-only "reel" categories: satisfying / soothing / fitness tips.
# Narrated like the rest of the channel (not wordless ASMR), just off the
# facts-video theme - so they get their own title/word-count guardrail
# bypass (kind="reel") and their own review prompt instead of the strict
# fact-checker one.
# --------------------------------------------------------------------------- #
REEL_CATEGORIES = ("satisfying", "soothing", "fitness")

_REEL_GUIDANCE = {
    "satisfying": (
        "an oddly satisfying, mesmerizing process - something being cut, "
        "poured, organised, folded, or transformed in a clean, pleasing way. "
        "Narrate what's happening and why it looks so satisfying (repetition, "
        "clean motion, texture, sound). Describe a plausible generic process, "
        "don't invent implausible specifics."
    ),
    "soothing": (
        "a calm, grounding moment - a gentle natural phenomenon, a slow body "
        "or breathing process, or a simple restful fact about sleep, sound, "
        "or relaxation. The goal is to help the viewer unwind for 30-40 "
        "seconds, not to teach a lot of facts."
    ),
    "fitness": (
        "one single, correct, well-known movement or exercise-form tip - a "
        "stretch, a posture cue, a warm-up habit, or a simple body-mechanics "
        "fact. Never diet, weight-loss, supplements, or medical advice - only "
        "movement, form, and technique."
    ),
}

_REEL_EXAMPLES = {
    "satisfying": textwrap.dedent("""\
        ---
        title: The Perfect Cut
        description_hook: A hot knife through soap turns into a strangely calming ritual.
        tags: satisfying, oddly satisfying, soap cutting, relaxing, asmr
        ---

        ## The setup
        A block of soap sits under warm studio light, its surface smooth and untouched.
        A blade rests against the edge, ready for one long, clean pass.
        [[soap block close up]] [[knife resting on soap]]

        ## The cut
        The blade glides through in one slow motion, curling a thin ribbon away from the block.
        The sound alone, soft and steady, is part of why this feels so calming to watch.
        [[knife cutting soap slowly]] [[soap ribbon curling]]

        ## Why it works
        Repetition and a clean, predictable motion are what make oddly satisfying clips so soothing to the brain.
        No surprises, no mess, just one smooth motion completing exactly as expected.
        [[soap shavings falling]] [[question everything text]]
        """),
    "soothing": textwrap.dedent("""\
        ---
        title: A Minute to Breathe
        description_hook: Slowing your exhale down is one of the fastest ways to calm the body.
        tags: soothing, calm, breathing, relax, mindfulness
        ---

        ## The pattern
        Slow rain taps against a window as the room settles into quiet.
        Breathing out for longer than breathing in gently signals the body that it is safe to relax.
        [[rain on window]] [[calm room interior]]

        ## The science
        A long exhale activates the vagus nerve, which naturally slows the heart rate.
        It is a switch the body already knows how to flip, no equipment needed.
        [[calm nature scene]] [[slow motion water]]

        ## The moment
        A few slow breaths like this and the shoulders start to drop on their own.
        [[person relaxing outdoors]] [[question everything text]]
        """),
    "fitness": textwrap.dedent("""\
        ---
        title: Fix Your Squat in One Cue
        description_hook: One small adjustment stops the knees from caving in during a squat.
        tags: fitness tips, squat form, mobility, exercise technique, workout
        ---

        ## The mistake
        Knees drifting inward during a squat is one of the most common form issues, even for experienced lifters.
        It usually starts at the hips, not the knees themselves.
        [[person squatting with poor form]] [[gym training]]

        ## The fix
        Pushing the knees out gently, in the same direction as the toes, keeps the hips properly engaged.
        A single cue, "knees out," is often enough to correct it immediately.
        [[person squatting with good form]] [[close up of legs during squat]]

        ## Why it matters
        Better alignment means the effort lands on the muscles meant to do the work, not the joints.
        [[person finishing a squat]] [[question everything text]]
        """),
}


def _reel_system(category: str) -> str:
    return textwrap.dedent(f"""\
        You write scripts for 30-40 second Instagram Reels for the channel
        "Get To Know What You Don't". This is a "{category}" Reel:
        {_REEL_GUIDANCE[category]}

        HARD RULES:
        - Calm, clear tone. Short sentences. No clickbait, no hype.
        - No medical, financial, current-political, religious, or
          controversial content. No advice beyond the tip itself for fitness
          Reels, and no diet/weight-loss/supplement content ever.
        - Narrate in third person or general statements - do not address the
          viewer with commands ("you must", "you should").

        FORMAT - copy EXACTLY. 3 scenes only. Each scene: a "## " heading,
        then 2 sentences, then one line with two [[double-bracket]] image
        search phrases describing concrete visuals. End the last scene's
        image line with [[question everything text]].

        EXAMPLE:
{textwrap.indent(_REEL_EXAMPLES[category], "        ")}
        """)


def generate_reel_script(theme: str, category: str, model: str = DEFAULT_MODEL,
                         attempts: int = 3, self_review: bool = True) -> str:
    """Return validated satisfying/soothing/fitness-tip Reel script markdown,
    or raise PipelineError."""
    if category not in REEL_CATEGORIES:
        raise PipelineError(f"unknown reel category {category!r} (need one of "
                            f"{REEL_CATEGORIES})")
    ok, why = guardrails.check_topic(theme)
    if not ok:
        raise PipelineError(f"reel theme rejected ({why}): {theme}")
    if not _ollama_up():
        raise PipelineError(
            "Ollama is not running. Start it (setup-auto.ps1), or the run will "
            "fall back to the reel bank."
        )

    from .script_parser import parse_script_text

    system = _reel_system(category)
    prompt = (f"Theme: {theme}\n\nWrite the Reel script now, in the exact "
             f"format from the example. Reply with only the markdown.")
    last: list[str] = []
    for _ in range(attempts):
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": model, "system": system, "prompt": prompt,
                      "stream": False,
                      "options": {"temperature": 0.6, "num_predict": 450}},
                timeout=600,
            )
            resp.raise_for_status()
            md = _normalize(resp.json().get("response", ""), theme, kind="reel")
            script = parse_script_text(md, fallback_title=theme)
        except (PipelineError, Exception) as e:  # noqa: BLE001
            last = [f"normalise/parse: {e}"]
            continue

        t_ok, t_why = guardrails.check_title(script.title, kind="reel")
        s_ok, s_issues = guardrails.check_script(
            " ".join(sc.narration for sc in script.scenes), kind="reel")
        n_ok = 2 <= len(script.scenes) <= 5
        if not (t_ok and s_ok and n_ok):
            last = ([] if t_ok else [f"title: {t_why}"]) + s_issues
            if not n_ok:
                last.append(f"{len(script.scenes)} scenes (need 2-5)")
            continue

        if self_review:
            ok2, note = _self_review(md, model, kind="reel")
            if not ok2:
                last = [f"self-review flagged: {note}"]
                continue
        return md

    raise PipelineError(f"no clean {category} reel for {theme!r} after {attempts} "
                        f"tries. last issues: {' | '.join(last)}")


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

    # Word count (via check_script) is the real length gate; scene count is
    # just a sanity check. A 3B local model doesn't reliably hit an exact
    # scene target even when it nails the word count, so keep this loose.
    scene_lo, scene_hi = (3, 4) if kind == "short" else (6, 12)
    last: list[str] = []
    for _ in range(attempts):
        try:
            md = _normalize(_generate_once(topic, model, kind), topic, kind)
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
