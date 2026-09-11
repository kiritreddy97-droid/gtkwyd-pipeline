"""Piper voice catalogue + on-demand downloader.

Voices come from the rhasspy/piper-voices repo on Hugging Face. Each entry maps a
voice name to its path within that repo. All are permissively licensed for
commercial use (see the model card linked from https://rhasspy.github.io/piper-samples/).
"""
from __future__ import annotations

import sys

import requests

from .util import MODELS_DIR, PipelineError

_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"

VOICES: dict[str, str] = {
    # name                              repo path (without extension)
    "en_US-lessac-medium":              "en/en_US/lessac/medium/en_US-lessac-medium",
    "en_US-lessac-high":                "en/en_US/lessac/high/en_US-lessac-high",
    "en_US-ryan-high":                  "en/en_US/ryan/high/en_US-ryan-high",
    "en_US-ryan-medium":                "en/en_US/ryan/medium/en_US-ryan-medium",
    "en_US-amy-medium":                 "en/en_US/amy/medium/en_US-amy-medium",
    "en_US-hfc_female-medium":          "en/en_US/hfc_female/medium/en_US-hfc_female-medium",
    "en_US-hfc_male-medium":            "en/en_US/hfc_male/medium/en_US-hfc_male-medium",
    "en_US-joe-medium":                 "en/en_US/joe/medium/en_US-joe-medium",
    "en_GB-alan-medium":                "en/en_GB/alan/medium/en_GB-alan-medium",
    "en_GB-northern_english_male-medium": "en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium",
    "en_GB-cori-high":                  "en/en_GB/cori/high/en_GB-cori-high",
    "en_US-ljspeech-high":              "en/en_US/ljspeech/high/en_US-ljspeech-high",
}

# ljspeech-high: trained on calm, measured audiobook narration - the closest
# free option to a "soothing" educational voice. All "-high" voices sound
# noticeably smoother than "-medium"/"-low" of the same speaker.
DEFAULT_VOICE = "en_US-ljspeech-high"


def list_voices() -> None:
    print("Available Piper voices (set one as voice.model in config.toml):\n")
    for name in VOICES:
        installed = (MODELS_DIR / f"{name}.onnx").exists()
        print(f"  {'[installed] ' if installed else '            '}{name}")
    print("\nPreview them at https://rhasspy.github.io/piper-samples/")


def _download(url: str, dest) -> None:
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                fh.write(chunk)


def ensure_voice(name: str) -> tuple:
    """Return (onnx_path, json_path), downloading the voice if needed."""
    if name not in VOICES:
        raise PipelineError(
            f"unknown voice {name!r}. Run  ./run.ps1 --list-voices  for the list."
        )
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    onnx = MODELS_DIR / f"{name}.onnx"
    meta = MODELS_DIR / f"{name}.onnx.json"
    repo_path = VOICES[name]
    if not onnx.exists():
        print(f"  downloading voice {name} ...")
        _download(_HF_BASE + repo_path + ".onnx", onnx)
    if not meta.exists():
        _download(_HF_BASE + repo_path + ".onnx.json", meta)
    return onnx, meta


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_voices()
    else:
        # --ensure [voice-name]  (default: read config, fall back to DEFAULT_VOICE)
        target = DEFAULT_VOICE
        args = [a for a in sys.argv[1:] if not a.startswith("-")]
        if args:
            target = args[0]
        else:
            try:
                from .util import load_config
                target = load_config().get("voice", {}).get("model", DEFAULT_VOICE)
            except Exception:
                pass
        ensure_voice(target)
        print(f"  voice ready: {target}")
