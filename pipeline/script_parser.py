"""Parse a markdown video script into a structured Script object.

Format
------
    ---
    title: 5 Things You Didn't Know About Octopuses
    description_hook: Three hearts is the least weird thing about them.
    tags: octopus, ocean, marine biology
    music: assets/music/curious.mp3
    voice: en_US-ryan-high            # optional, overrides config
    ---

    ## Hook
    Narration text for the first scene.
    [[octopus swimming underwater]]

    ## Three hearts
    More narration.
    [[octopus close up]] [[deep blue ocean]]

Rules
-----
* Each `## ` heading begins a new scene.
* Plain text under a heading is that scene's narration.
* `[[search terms]]` markers are stock-footage queries for the scene and are
  removed from the spoken narration. A scene with no marker reuses the previous
  scene's queries (or the video title for the first scene).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .util import PipelineError

_QUERY_RE = re.compile(r"\[\[(.+?)\]\]")


@dataclass
class Scene:
    heading: str
    narration: str
    queries: list[str] = field(default_factory=list)


@dataclass
class Script:
    title: str
    description_hook: str
    tags: list[str]
    music: str | None
    voice: str | None
    scenes: list[Scene]


def _parse_frontmatter(block: str) -> dict:
    meta: dict = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip()
    return meta


def parse_script(path: str | Path) -> Script:
    path = Path(path)
    if not path.exists():
        raise PipelineError(f"script not found: {path}")
    return parse_script_text(path.read_text(encoding="utf-8"),
                             fallback_title=path.stem.replace("-", " ").title())


def parse_script_text(text: str, fallback_title: str = "Untitled") -> Script:
    text = text.lstrip("﻿").replace("\r\n", "\n")  # BOM + CRLF tolerant
    meta: dict = {}
    body = text
    fm = re.match(r"\s*---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if fm:
        meta = _parse_frontmatter(fm.group(1))
        body = fm.group(2)

    scenes: list[Scene] = []
    heading = None
    buf: list[str] = []

    def flush() -> None:
        if heading is None and not any(s.strip() for s in buf):
            return
        raw = "\n".join(buf).strip()
        queries = [q.strip() for q in _QUERY_RE.findall(raw) if q.strip()]
        narration = _QUERY_RE.sub("", raw)
        narration = re.sub(r"\[[^\]]*\]", "", narration)  # drop stray [directions]
        narration = re.sub(r"\s+", " ", narration).strip()
        if not narration:
            return
        scenes.append(Scene(heading=heading or f"Scene {len(scenes) + 1}",
                            narration=narration, queries=queries))

    for line in body.splitlines():
        m = re.match(r"^\s{0,3}#{2,3}\s+(.*)$", line)
        if m:
            flush()
            heading = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    flush()

    if not scenes:
        raise PipelineError(
            "no scenes found. Add at least one '## Heading' section with narration."
        )

    title = meta.get("title") or fallback_title

    # Backfill missing queries.
    prev: list[str] = [title]
    for sc in scenes:
        if sc.queries:
            prev = sc.queries
        else:
            sc.queries = list(prev)

    tags = [t.strip() for t in re.split(r"[,\n]", meta.get("tags", "")) if t.strip()]

    return Script(
        title=title,
        description_hook=meta.get("description_hook", ""),
        tags=tags,
        music=meta.get("music") or None,
        voice=meta.get("voice") or None,
        scenes=scenes,
    )
