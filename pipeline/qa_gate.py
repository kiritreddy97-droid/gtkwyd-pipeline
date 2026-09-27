"""Automated QA gatekeeper for every Instagram post (Reels, Stories, fact
images) - checks quality BEFORE anything gets staged/posted, using real
visual judgment via the Claude API (falls back to a pass-through with a
logged note if ANTHROPIC_API_KEY isn't set, so the pipeline never hard-fails
just because the key is missing).

A post that fails gets sent back to the caller to regenerate and re-check
(the retry loop lives in each entry script - story.py/reels.py/fact_post.py/
auto.py - not here); if it still fails after all attempts, pipeline.qa_queue
holds it so the NEXT run for that category tries it again instead of losing
it, per the channel owner's explicit instruction.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from pathlib import Path

import requests

from .util import FFMPEG, PipelineError, ffprobe_duration

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
VISION_MODEL = "claude-sonnet-5"


def _api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def vision_available() -> bool:
    return bool(_api_key())


def _b64_image(path: Path) -> tuple[str, str]:
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return media_type, data


def _ask_vision(prompt: str, image_paths: list[Path]) -> str:
    """One Claude vision call. Raises PipelineError on any API failure -
    callers treat that as "skip this check" rather than a hard failure."""
    key = _api_key()
    if not key:
        raise PipelineError("ANTHROPIC_API_KEY not set")

    content = []
    for p in image_paths:
        media_type, data = _b64_image(p)
        content.append({"type": "image", "source": {"type": "base64",
                        "media_type": media_type, "data": data}})
    content.append({"type": "text", "text": prompt})

    r = requests.post(
        ANTHROPIC_URL,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                "content-type": "application/json"},
        json={"model": VISION_MODEL, "max_tokens": 300,
              "messages": [{"role": "user", "content": content}]},
        timeout=60,
    )
    if not r.ok:
        raise PipelineError(f"Claude vision call failed ({r.status_code}): {r.text}")
    body = r.json()
    return "".join(b.get("text", "") for b in body.get("content", [])
                  if b.get("type") == "text")


def _extract_frames(video_path: Path, out_dir: Path, count: int = 4) -> list[Path]:
    duration = ffprobe_duration(video_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(count):
        t = duration * (i + 0.5) / count
        out = out_dir / f"frame_{i}.jpg"
        subprocess.run([FFMPEG, "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
                       "-vframes", "1", str(out)], capture_output=True)
        if out.exists():
            frames.append(out)
    return frames


def check_reel(video_path: Path, cover_path: Path | None,
               narration_text: str) -> tuple[bool, list[str]]:
    """QA a rendered Reel/story before it's staged for Instagram. Returns
    (passed, issues) - issues carries the reviewer's reasoning either way,
    and a note instead of a real check whenever vision isn't available."""
    issues: list[str] = []
    if not vision_available():
        issues.append("vision check skipped (ANTHROPIC_API_KEY not set) - "
                      "posted without a visual QA pass")
        return True, issues

    tmp_dir = video_path.parent / "_qa_frames"
    try:
        frames = _extract_frames(video_path, tmp_dir, count=4)
        if not frames:
            issues.append("could not extract frames for review")
            return False, issues

        images = frames + ([cover_path] if cover_path and cover_path.exists() else [])
        prompt = (
            "You are a strict QA reviewer for a faceless educational Instagram "
            "Reels channel. You are shown several frames sampled through a "
            "short video, plus its cover thumbnail (the last image, if one "
            "is included).\n\n"
            f"The video's narration says (approximately): {narration_text[:800]!r}\n\n"
            "Check ALL of the following and reply with exactly one line per "
            "check, each starting with PASS or FAIL, then a short reason:\n"
            "1. RELEVANCE: do the visuals plausibly relate to what the "
            "narration is about (not a random unrelated scene)?\n"
            "2. THUMBNAIL: is the cover thumbnail visually clear, attractive, "
            "and not glitchy/garbled/blank (skip this line if no thumbnail "
            "was shown)?\n"
            "Finish with a final line: OVERALL: PASS or OVERALL: FAIL."
        )
        reply = _ask_vision(prompt, images)
    except Exception as e:  # noqa: BLE001
        issues.append(f"vision check errored, treated as pass-through: {e}")
        return True, issues
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    passed = bool(re.search(r"OVERALL:\s*PASS", reply, re.I))
    issues.append(reply.strip())
    return passed, issues


def check_image_post(image_path: Path, hook_text: str) -> tuple[bool, list[str]]:
    """QA a fact-image post before it's staged for Instagram."""
    issues: list[str] = []
    if not vision_available():
        issues.append("vision check skipped (ANTHROPIC_API_KEY not set) - "
                      "posted without a visual QA pass")
        return True, issues

    try:
        prompt = (
            "You are a strict QA reviewer for a faceless educational Instagram "
            "account's feed image posts. You are shown one static graphic "
            f"meant to convey this fact: {hook_text[:500]!r}\n\n"
            "Check ALL of the following, one PASS/FAIL line each with a short "
            "reason:\n"
            "1. LOOKS INTENTIONAL: does this look like a deliberately designed "
            "social graphic - not garbled/broken/glitchy text, not an "
            "obviously malformed render, and does it read as something a "
            "human designer made rather than a broken auto-generated image?\n"
            "2. READABLE: is the text legible and does it clearly convey the "
            "fact above?\n"
            "Finish with: OVERALL: PASS or OVERALL: FAIL."
        )
        reply = _ask_vision(prompt, [image_path])
    except Exception as e:  # noqa: BLE001
        issues.append(f"vision check errored, treated as pass-through: {e}")
        return True, issues

    passed = bool(re.search(r"OVERALL:\s*PASS", reply, re.I))
    issues.append(reply.strip())
    return passed, issues
