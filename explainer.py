"""Render one 'how it's made / how it works' explainer Short.

    python explainer.py --lang en            next product from topics-explainers.txt
    python explainer.py --lang te --topic "a zipper"
    python explainer.py --lang en --dry-run   (rendering only; uploading is wired separately)
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


def main() -> int:
    ap = argparse.ArgumentParser(prog="explainer")
    ap.add_argument("--lang", choices=("en", "te"), default="en")
    ap.add_argument("--topic", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not writer_te.available():
        print("GEMINI_API_KEY not set - skipping"); return 0
    cfg = load_config()
    model = cfg.get("telugu", {}).get("gemini_model", writer_te.DEFAULT_MODEL)
    ideas.refill_if_low(TOPICS, "product", model)
    topic = args.topic or ideas.next_topic(TOPICS)
    if not topic:
        print("no explainer topics left and none could be generated"); return 0
    try:
        data = explainer.generate(topic, args.lang, model)
    except Exception as e:  # noqa: BLE001
        print(f"explainer writer failed for {topic!r}: {e}")
        if not args.topic:
            ideas.mark_used(topic, TOPICS)
        return 1
    if not args.topic:
        ideas.mark_used(topic, TOPICS)

    OUT.mkdir(parents=True, exist_ok=True)
    slug = f"{slugify(data.get('title_en') or topic)[:44]}-{args.lang}-{uuid.uuid4().hex[:5]}"
    script = OUT / f"{slug}.md"
    script.write_text(explainer.to_markdown(data, args.lang), encoding="utf-8")
    print(f"[explainer] {args.lang}: {data.get('title_en') or topic} -> {script.name}")
    rc = subprocess.run([sys.executable, "make_video.py", str(script), "--short"],
                        cwd=str(ROOT)).returncode
    if rc != 0:
        print("render failed"); return 1
    print(f"[explainer] rendered: {BUILD_DIR / slugify(script.stem) / (slugify(script.stem) + '.mp4')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
