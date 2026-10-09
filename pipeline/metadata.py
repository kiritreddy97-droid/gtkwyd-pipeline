"""Write a YouTube-ready metadata file: title, description, chapters, tags.

Also writes a sibling <stem>.meta.json for machine use (auto.py reads it to upload).
"""
from __future__ import annotations

import json
from pathlib import Path

_DEFAULT_TAGS = ["did you know", "learn something new", "mind blowing facts",
                 "explained", "get to know what you don't", "question everything"]

_TAGLINE = "Question everything. Discover reality."

_TE_TAGS = ["telugu history", "telugu stories", "history in telugu", "telugu story narration",
            "indian history", "తెలుగు చరిత్ర", "చరిత్ర కథలు", "తెలుగు కథలు"]
_TE_TAGLINE = "ప్రతిరోజూ చరిత్రలోని ఒక అద్భుతమైన కథ."
_TE_SUBSCRIBE = "ఇలాంటి మరిన్ని చరిత్ర కథల కోసం ఛానల్‌ను సబ్‌స్క్రైబ్ చేయండి."


def _ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def generate(script, scene_starts: list[float], total: float, sources: set[str],
             channel: str, out_txt: Path, is_short: bool = False,
             lang: str = "en", voice_credit: str = "Piper (open-source, offline)") -> Path:
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

    if lang == "te":
        if is_short:
            desc_parts.append(
                f"{_TE_TAGLINE}\n{_TE_SUBSCRIBE}\n\n"
                "#Shorts #Telugu #TeluguHistory #చరిత్ర #తెలుగుకథలు"
            )
        else:
            desc_parts.append(
                f"{_TE_TAGLINE}\n{_TE_SUBSCRIBE}\n\nఅధ్యాయాలు:\n" + "\n".join(chapters)
                + "\n\n#Telugu #TeluguHistory #చరిత్ర #తెలుగుకథలు"
            )
    elif is_short:
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
        f"Narration: {voice_credit}."
    )
    description = "\n\n".join(desc_parts).strip()

    tags = list(dict.fromkeys([*script.tags, *(_TE_TAGS if lang == "te" else _DEFAULT_TAGS)]))
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
[ ] Language: {'Telugu' if lang == 'te' else 'English'} | Category: Education
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
        "lang": lang,
        "sources": sorted(s for s in sources if s != "solid"),
    }, indent=2), encoding="utf-8")
    return out_txt
