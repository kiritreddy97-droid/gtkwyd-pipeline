"""Render one 'how it's made / how it works' explainer Short.

    python explainer.py --lang en            next product from topics-explainers.txt
    python explainer.py --lang te --topic "a zipper"
    python explainer.py --lang en --dry-run   (rendering only; uploading is explainer_daily.py)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

from pipeline import explainer, ideas, writer_te
from pipeline.util import BUILD_DIR, ROOT, load_config, slugify

TOPICS = ROOT / "topics-explainers.txt"
OUT = ROOT / "scripts" / "_generated-explainers"


def build(lang: str, topic: str = "", look: str = "") -> dict | None:
    """Write + render one explainer. Returns {"video", "slug", "topic", "data"} or None
    (nothing to do / a failure that was logged)."""
    if not writer_te.available():
        print("GEMINI_API_KEY not set - skipping"); return None
    cfg = load_config()
    model = cfg.get("telugu", {}).get("gemini_model", writer_te.DEFAULT_MODEL)
    ideas.refill_if_low(TOPICS, "product", model)
    manual = bool(topic)
    topic = topic or ideas.next_topic(TOPICS)
    if not topic:
        print("no explainer topics left and none could be generated"); return None
    try:
        data = explainer.generate(topic, lang, model)
    except writer_te.GeminiUnavailable as e:
        print(f"Gemini unavailable, topic kept for the next run: {e}"); return None
    except Exception as e:  # noqa: BLE001
        print(f"explainer writer failed for {topic!r}: {e}")
        if not manual:
            ideas.mark_used(topic, TOPICS)
        return None
    if not manual:
        ideas.mark_used(topic, TOPICS)

    OUT.mkdir(parents=True, exist_ok=True)
    slug = f"{slugify(data.get('title_en') or topic)[:44]}-{lang}-{uuid.uuid4().hex[:5]}"
    script = OUT / f"{slug}.md"
    script.write_text(explainer.to_markdown(data, lang, look), encoding="utf-8")
    print(f"[explainer] {lang}: {data.get('title_en') or topic} -> {script.name}")
    rc = subprocess.run([sys.executable, "make_video.py", str(script), "--short"],
                        cwd=str(ROOT)).returncode
    key = slugify(script.stem)
    video = BUILD_DIR / key / f"{key}.mp4"
    if rc != 0 or not video.exists():
        print("render failed"); return None
    print(f"[explainer] rendered: {video}")
    return {"video": video, "slug": key, "topic": topic, "data": data}


def main() -> int:
    ap = argparse.ArgumentParser(prog="explainer")
    ap.add_argument("--lang", choices=("en", "te"), default="en")
    ap.add_argument("--topic", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return 0 if build(args.lang, args.topic) else 1


if __name__ == "__main__":
    sys.exit(main())
