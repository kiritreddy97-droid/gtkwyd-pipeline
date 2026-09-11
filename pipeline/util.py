"""Shared helpers: paths, config loading, ffmpeg wrappers."""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPER_EXE = ROOT / "piper" / ("piper.exe" if platform.system() == "Windows" else "piper")
MODELS_DIR = ROOT / "models"
CACHE_DIR = ROOT / "cache"
BUILD_DIR = ROOT / "build"
ASSETS_DIR = ROOT / "assets"

def _resolve(name: str) -> str:
    """Find an executable on PATH, or in common Windows install locations
    (a freshly winget-installed tool isn't on PATH until the shell restarts)."""
    hit = shutil.which(name)
    if hit:
        return hit
    import glob
    import os

    patterns = [
        os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\**\{name}.exe"),
        os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Links\{name}.exe"),
        rf"C:\ProgramData\chocolatey\bin\{name}.exe",
        rf"C:\ffmpeg\bin\{name}.exe",
    ]
    for pat in patterns:
        for match in glob.glob(pat, recursive=True):
            if os.path.isfile(match):
                return match
    return name


FFMPEG = _resolve("ffmpeg")
FFPROBE = _resolve("ffprobe")


class PipelineError(RuntimeError):
    pass


def load_config() -> dict:
    cfg_path = ROOT / "config.toml"
    if not cfg_path.exists():
        raise PipelineError(
            "config.toml not found. Copy config.example.toml to config.toml "
            "and add your API keys."
        )
    with cfg_path.open("rb") as fh:
        return tomllib.load(fh)


def dims(cfg: dict) -> tuple[int, int]:
    orient = cfg.get("project", {}).get("orientation", "landscape").lower()
    return (1080, 1920) if orient.startswith("port") else (1920, 1080)


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_-]+", "-", text) or "video"


def run(cmd: list, *, cwd: Path | None = None, quiet: bool = True) -> subprocess.CompletedProcess:
    """Run a command, raising PipelineError with captured stderr on failure."""
    cmd = [str(c) for c in cmd]
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").splitlines()[-25:])
        raise PipelineError(f"command failed ({proc.returncode}): {' '.join(cmd[:3])} ...\n{tail}")
    if not quiet and proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return proc


def ffprobe_duration(path: Path) -> float:
    proc = run([
        FFPROBE, "-v", "error", "-show_entries", "format=duration",
        "-of", "json", path,
    ])
    return float(json.loads(proc.stdout)["format"]["duration"])


def check_ffmpeg() -> None:
    import os

    if not os.path.isfile(FFMPEG) and not shutil.which("ffmpeg"):
        raise PipelineError(
            "ffmpeg not found. Install it with:  winget install Gyan.FFmpeg\n"
            "then open a NEW terminal (PATH updates only apply to new shells)."
        )
