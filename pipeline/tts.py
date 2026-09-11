"""Narration synthesis with the offline Piper binary."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .util import PIPER_EXE, ROOT, PipelineError

_ESPEAK_DATA = ROOT / "piper" / "espeak-ng-data"


def synthesize(text: str, out_wav: Path, onnx_path: Path,
               length_scale: float = 1.0, sentence_silence: float = 0.35) -> Path:
    if not PIPER_EXE.exists():
        raise PipelineError(
            f"Piper binary missing at {PIPER_EXE}. Re-run ./setup.ps1"
        )
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(PIPER_EXE),
        "-m", str(onnx_path),
        "-f", str(out_wav),
        "--length_scale", str(length_scale),
        "--sentence_silence", str(sentence_silence),
        "-q",
    ]
    if _ESPEAK_DATA.exists():
        cmd += ["--espeak_data", str(_ESPEAK_DATA)]

    # Piper reads one utterance per stdin line; keep the narration on a single line.
    payload = " ".join(text.split()) + "\n"
    proc = subprocess.run(
        cmd, input=payload, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not out_wav.exists():
        tail = "\n".join((proc.stderr or "").splitlines()[-15:])
        raise PipelineError(f"piper failed:\n{tail}")
    return out_wav
