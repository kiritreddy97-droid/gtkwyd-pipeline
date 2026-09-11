"""Write a YouTube-ready metadata file: title, description, chapters, tags.

Also writes a sibling <stem>.meta.json for machine use (auto.py reads it to upload).
"""
from __future__ import annotations

import json
from pathlib import Path

_DEFAULT_TAGS = ["did you know", "learn something new", "mind blowing facts",
                 "explained", "get to know what you don't", "question everything"]

_TAGLINE = "Question everything. Discover reality."


def _ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def generate(script, scene_starts: list[float], total: float, sources: set[str],
             channel: str, out_txt: Path, is_short: bool = False) -> Path:
    title = script.title
    if is_short and "#shorts" not in title.lower() and len(title) <= 88:
        title = f"{title} #Shorts"
    warn = "  <-- over 100 chars, trim before upload" if len(title) > 100 else ""

    chapters = []
    for i, (scene, start) in enumerate(zip(script.scenes, scene_starts)):
        stamp = "0:00" if i == 0 else _ts(start)
        chapters.append(f"{stamp} {scene.heading}")

    hook = script.description_hook.strip()
    desc_parts = []
    if hook:
        desc_parts.append(hook)

    if is_short:
        desc_parts.append(
            f"{_TAGLINE}\nSubscribe for daily science, space, tech and history.\n\n"
            "#Shorts #facts #science #space #technology #history #didyouknow"
        )
    else:
        desc_parts.append(
            f"{_TAGLINE}\nSubscribe for daily deep-dives into science, space, "
            "AI, hidden history, psychology and the discoveries reshaping how we "
            "see the world.\n\nChapters:\n" + "\n".join(chapters)
        )

    src_line = ", ".join(sorted(s.title() for s in sources if s != "solid")) or "n/a"
    desc_parts.append(
        f"Footage & images: {src_line} (royalty-free). "
        "Narration: Piper (open-source, offline)."
    )
    description = "\n\n".join(desc_parts).strip()

    tags = list(dict.fromkeys([*script.tags, *_DEFAULT_TAGS]))
    tag_line = ", ".join(tags)
    if len(tag_line) > 480:
        tag_line = tag_line[:480].rsplit(",", 1)[0]

    stem = out_txt.stem.replace("_metadata", "")
    kind = "SHORT (vertical, <60s)" if is_short else "VIDEO"
    body = f"""=== {kind} ===

=== TITLE ({len(title)} chars){warn} ===
{title}

=== DESCRIPTION ===
{description}

=== TAGS (comma separated, <=500 chars) ===
{tag_line}

=== UPLOAD CHECKLIST ===
[ ] Visibility per config ([auto] visibility)
[ ] Audience: "No, it's not made for kids"
[ ] {'Vertical - it will show up as a Short automatically' if is_short else f'Thumbnail: upload {stem}_thumbnail.png'}
[ ] Language: English | Category: {'Education' if not is_short else 'Education'}
[ ] Runtime: {_ts(total)}
"""
    out_txt.write_text(body, encoding="utf-8")

    (out_txt.with_name(f"{stem}.meta.json")).write_text(json.dumps({
        "title": title,
        "description": description,
        "tags": tags,
        "runtime_seconds": round(total, 2),
        "chapters": [] if is_short else chapters,
        "made_for_kids": False,
        "category_id": "27",
        "is_short": is_short,
        "sources": sorted(s for s in sources if s != "solid"),
    }, indent=2), encoding="utf-8")
    return out_txt
