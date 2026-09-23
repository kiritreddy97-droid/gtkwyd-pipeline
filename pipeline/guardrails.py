"""Brand-fit and safety checks for automated (AI-written) content.

The channel is "Get To Know What You Don't": short videos of surprising but
well-established, encyclopedia-grade facts. Anything that is risky, controversial,
advice-shaped, or off-theme is rejected before it can be rendered or uploaded.
"""
from __future__ import annotations

import re

CHANNEL_THEME = (
    "fascinating, well-established discoveries and ideas across six areas: "
    "artificial intelligence and future technology; science and space "
    "exploration; hidden history and untold stories; psychology and human "
    "behaviour; science and tech news explained; and mind-blowing facts. "
    "The tone is curious and a little provocative - 'question everything, "
    "discover reality' - but every claim is accurate and non-sensational"
)

# Off-limits SUBJECTS. Phrased with enough context that ordinary educational use
# of a word (the fur "trade", a rifle "stock", soup "stock", a "disease" in
# history, how "vaccines" were invented) does NOT trip it. We reject genuine
# hot-button / advice / adult / dangerous territory, not vocabulary.
_BANNED = re.compile("|".join([
    # financial advice / speculation
    r"\binvest(?:ing|ment)?\b", r"\bstock market\b", r"\bstocks? (?:to buy|and shares)\b",
    r"\bcrypto\w*\b", r"\bbitcoin\b", r"\bethereum\b", r"\bforex\b",
    r"\bday.?trading\b", r"\bget rich\b", r"\bmake money (?:online|fast|from home)\b",
    r"\bpassive income\b", r"\bside hustle\b", r"\bfinancial (?:advice|freedom)\b",
    # medical advice / sensitive health
    r"\bmiracle cure\b", r"\bhome remed(?:y|ies)\b", r"\bweight ?loss\b",
    r"\bdiet plan\b", r"\bsupplements?\b", r"\bself.?diagnos\w*\b",
    r"\bmental health\b", r"\bdepression\b", r"\banxiety\b", r"\bsuicide\b",
    r"\bself.?harm\b", r"\bvaccin\w*\b", r"\bcovid\b", r"\bcancer\b",
    r"\babortion\b",
    # politics / religion / social division (current-affairs, not ancient history)
    r"\bpolitic(?:s|al|ian)\b", r"\belection\w*\b", r"\bpresident (?:biden|trump|of the)\b",
    r"\bprime minister\b", r"\bimmigration\b", r"\bgun control\b",
    r"\breligio(?:n|us)\b", r"\bislam\w*\b", r"\bchristian\w*\b", r"\bjewish\b",
    r"\bhindu\w*\b", r"\bmuslim\w*\b", r"\bconspiracy\b", r"\billuminati\b",
    r"\bflat earth\b", r"\bnew world order\b",
    # unsafe / harmful / adult
    r"\bweapons?\b", r"\bfirearms?\b", r"\bhow to make a bomb\b", r"\bexplosives?\b",
    r"\bhow to hack\b", r"\bcocaine\b", r"\bheroin\b", r"\bmethamphetamine\b",
    r"\bcannabis\b", r"\bmarijuana\b", r"\brecreational drugs?\b", r"\bvaping\b",
    r"\bgambling\b", r"\bcasino\b", r"\blottery\b", r"\bporn\w*\b",
    r"\bsexual\b", r"\bnsfw\b", r"\bgraphic violence\b", r"\bgore\b",
    r"\bterroris\w*\b", r"\bmurder(?:ed|er|ing)?\b", r"\bserial killer\b",
    # clickbait framing
    r"you won'?t believe", r"doctors hate", r"shocking truth",
    r"they don'?t want you to know", r"this one (?:weird )?trick",
    r"will change your life", r"gone (?:wrong|sexual)",
]), re.I)

# Script text must not read like advice or clickbait.
_BANNED_SCRIPT_PATTERNS = [
    r"\byou should\b", r"\byou need to\b", r"\byou must\b",
    r"\bconsult (?:your|a) doctor\b", r"\bbuy now\b", r"\bclick the link\b",
    r"\btalk to your (?:doctor|financial)\b",
    r"\bnot (?:financial|medical) advice\b", r"\bdo your own research\b",
]

_ALLOWED_TITLE_SHAPES = re.compile(
    r"(facts?|things?|reasons?|ways?|signs?|did you know|what you don'?t|"
    r"you didn'?t know|nobody tells you|hidden|weird|strange|surprising|secret|"
    r"smarter than|sound (fake|made up|wrong|impossible|exaggerated)|feel wrong|"
    r"^how |^why |^what |^when |^where |^the [a-z]+|^your |^inside |^meet |"
    r"actually|really|myth|truth about|explained|makes? no sense|"
    r"needs? \w+ to work|out of a shipwreck|into (deep )?space)",
    re.I,
)


def check_topic(topic: str) -> tuple[bool, str]:
    t = topic.strip()
    if not t or len(t) < 4:
        return False, "topic too short"
    if len(t) > 120:
        return False, "topic too long"
    m = _BANNED.search(t)
    if m:
        return False, f"off-limits area: {m.group(0)!r}"
    return True, "ok"


def check_title(title: str) -> tuple[bool, str]:
    if not (10 <= len(title) <= 100):
        return False, f"title length {len(title)} (need 10-100)"
    m = _BANNED.search(title)
    if m:
        return False, f"title hits off-limits area: {m.group(0)!r}"
    if title.count("!") > 1 or title.isupper():
        return False, "title over-punctuated / shouting"
    if not _ALLOWED_TITLE_SHAPES.search(title):
        return False, "title doesn't read like an on-brand facts video"
    return True, "ok"


def check_script(text: str, kind: str = "video") -> tuple[bool, list[str]]:
    issues: list[str] = []
    low = text.lower()
    words = re.findall(r"\w+", text)
    # video: ~550-900 words targets ~4-6 min of narration at the pipeline's
    # TTS pace (length_scale=1.12, ~134 wpm effective) - was capped at 320
    # words (~1 min), which is why full videos were landing around 1 minute
    # regardless of how the prompt was worded.
    # news is its own, shorter bound: it must stick to what the source
    # summary actually says, so it can't be forced to the same word count
    # without risking padding/speculation - better to let it fall back to
    # the bank/AI path than stretch a thin news item artificially.
    if kind == "short":
        lo, hi = 45, 150
    elif kind == "news":
        lo, hi = 150, 500
    else:
        lo, hi = 550, 900

    if len(words) < lo:
        issues.append(f"too short ({len(words)} words; need >={lo})")
    if len(words) > hi:
        issues.append(f"too long ({len(words)} words; keep <={hi})")

    for hit in sorted({m.group(0).lower() for m in _BANNED.finditer(text)}):
        issues.append(f"off-limits area: {hit!r}")
    for pat in _BANNED_SCRIPT_PATTERNS:
        if re.search(pat, low):
            issues.append(f"advice/clickbait phrasing: /{pat}/")

    # some "you" is fine for an explainer; a lot of it reads like a how-to
    if len(re.findall(r"\b(you|your|yourself)\b", low)) > 12:
        issues.append("too much second-person address; keep it mostly factual")
    if re.search(r"\b(i think|i believe|in my opinion|might be aliens)\b", low):
        issues.append("speculation / opinion language")

    return (not issues), issues
