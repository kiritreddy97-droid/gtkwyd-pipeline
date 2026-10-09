"""AI scene illustrations on the free CPU runner (open-source diffusion model).

Replaces mismatched stock footage for history stories: each scene's
`{{prompt}}` is drawn as a cinematic painting, upscaled to a true 1080p frame,
then given slow camera movement by the normal scene builder.

Needs torch (CPU) + diffusers (requirements-ai.txt). When they are missing, or a
generation fails, callers fall back to stock footage - never a hard failure.
The model is configurable ([telugu] image_model); the default is
stabilityai/sdxl-turbo (check its licence terms for your revenue level).
"""
from __future__ import annotations

import os
from pathlib import Path

from .util import PipelineError

STYLE = ("cinematic painterly historical illustration, rich warm colors, dramatic golden "
         "light, highly detailed, concept art, epic composition, no text, no watermark, "
         "no modern objects")
DEFAULT_MODEL = "stabilityai/sdxl-turbo"
_pipe = {}


def available(cfg: dict) -> bool:
    if not cfg.get("telugu", {}).get("illustrations", True):
        return False
    try:
        import diffusers  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def _load(model: str):
    if model not in _pipe:
        import torch
        from diffusers import AutoPipelineForText2Image

        torch.set_num_threads(os.cpu_count() or 4)
        try:
            p = AutoPipelineForText2Image.from_pretrained(
                model, torch_dtype=torch.float32, variant="fp16")
        except Exception:  # noqa: BLE001 - model has no fp16 variant files
            p = AutoPipelineForText2Image.from_pretrained(model, torch_dtype=torch.float32)
        p.set_progress_bar_config(disable=True)
        _pipe[model] = p
    return _pipe[model]


def generate(prompt: str, dest: Path, portrait: bool = False, model: str = DEFAULT_MODEL,
             steps: int = 2, seed: int | None = None) -> Path:
    """Draw `prompt` and save a 1920x1080 (or 1080x1920) JPEG at `dest`."""
    try:
        import torch
        from PIL import Image, ImageFilter

        pipe = _load(model)
        w, h = (576, 1024) if portrait else (1024, 576)
        gen = torch.Generator().manual_seed(seed) if seed is not None else None
        img = pipe(prompt=f"{prompt}, {STYLE}", width=w, height=h, num_inference_steps=steps,
                   guidance_scale=0.0, generator=gen).images[0]
        tw, th = (1080, 1920) if portrait else (1920, 1080)
        img = img.convert("RGB").resize((tw, th), Image.LANCZOS)
        img = img.filter(ImageFilter.UnsharpMask(radius=1.4, percent=120, threshold=2))
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, quality=95)
        return dest
    except Exception as e:  # noqa: BLE001
        raise PipelineError(f"illustration failed: {e}") from e
