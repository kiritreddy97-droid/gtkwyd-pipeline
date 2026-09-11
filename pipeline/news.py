"""Pull recent science / space / AI / tech / discovery headlines from curated,
reputable RSS feeds. No general world news - only feeds that stay on-theme and
carry near-zero policy/tragedy/politics risk.

Used by auto.py to give the writer a fresh, real topic to explain.
"""
from __future__ import annotations

import calendar
import time
from dataclasses import dataclass

import requests

from . import guardrails

# name -> RSS url. All science/tech/space/discovery focused.
FEEDS: dict[str, str] = {
    "NASA":            "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "ESA":             "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    "Space.com":       "https://www.space.com/feeds/all",
    "ScienceDaily":    "https://www.sciencedaily.com/rss/top/science.xml",
    "Phys.org":        "https://phys.org/rss-feed/",
    "New Scientist":   "https://www.newscientist.com/feed/home/",
    "Ars Technica":    "https://feeds.arstechnica.com/arstechnica/science",
    "MIT Tech Review": "https://www.technologyreview.com/feed/",
    "Quanta":          "https://api.quantamagazine.org/feed/",
    "Live Science":    "https://www.livescience.com/feeds/all",
}

_UA = {"User-Agent": "Mozilla/5.0 (compatible; GTKWYD-pipeline/1.0)"}

# Extra caution on top of guardrails: skip headlines that are opinion, policy,
# funding fights, obituaries, or otherwise not a clean "here is a discovery".
_SKIP = (
    "opinion", "editorial", "op-ed", "obituary", "dies at", "death of", "layoff",
    "lawsuit", "boycott", "controversy", "backlash", "slams", " vs.", "senate",
    "congress", "election", "policy", "regulation", "ban on", "funding cut",
    "budget", "trump", "biden", "feud", "review:", "best ", "deals", "coupon",
    "how to watch", "prime day", "vape", "climate policy", "flood", "wildfire",
    "drought", "hurricane", "earthquake kills", "war", "military", "weapon",
    "outbreak", "pandemic", "rescue", "warn", "warning", "threat", "danger",
    "crisis", "devastating",
)


@dataclass
class NewsItem:
    title: str
    summary: str
    link: str
    source: str
    published: float  # epoch seconds


def _parse(source: str, url: str) -> list[NewsItem]:
    try:
        import feedparser
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("feedparser not installed - run setup-auto.ps1") from e
    try:
        raw = requests.get(url, headers=_UA, timeout=20).content
    except requests.RequestException:
        return []
    feed = feedparser.parse(raw)
    items: list[NewsItem] = []
    for e in feed.entries[:20]:
        title = (e.get("title") or "").strip()
        summary = (e.get("summary") or e.get("description") or "").strip()
        summary = _strip_html(summary)[:600]
        ts = e.get("published_parsed") or e.get("updated_parsed")
        pub = calendar.timegm(ts) if ts else time.time()  # feeds give UTC structs
        if title:
            items.append(NewsItem(title, summary, e.get("link", ""), source, pub))
    return items


def _strip_html(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", s).replace("&nbsp;", " ").strip()


def _acceptable(item: NewsItem) -> bool:
    low = f"{item.title} {item.summary}".lower()
    if any(k in low for k in _SKIP):
        return False
    ok, _ = guardrails.check_topic(item.title)
    if not ok:
        return False
    # must look like a substantive story
    return len(item.title) >= 25 and len(item.summary) >= 80


def latest(max_age_hours: int = 60) -> list[NewsItem]:
    cutoff = time.time() - max_age_hours * 3600
    out: list[NewsItem] = []
    for source, url in FEEDS.items():
        for it in _parse(source, url):
            if it.published >= cutoff and _acceptable(it):
                out.append(it)
    out.sort(key=lambda i: i.published, reverse=True)
    return out


def pick(used_links: set[str], used_titles: set[str]) -> NewsItem | None:
    for it in latest():
        if it.link in used_links:
            continue
        if any(_similar(it.title, t) for t in used_titles):
            continue
        return it
    return None


def _similar(a: str, b: str) -> bool:
    aw = set(a.lower().split())
    bw = set(b.lower().split())
    if not aw or not bw:
        return False
    return len(aw & bw) / len(aw | bw) > 0.5


if __name__ == "__main__":
    for i, it in enumerate(latest()[:15], 1):
        age = (time.time() - it.published) / 3600
        print(f"{i:2}. [{it.source:14}] ({age:4.0f}h) {it.title}")
