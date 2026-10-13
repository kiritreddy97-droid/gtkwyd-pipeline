"""2.5D parallax camera moves for still illustrations (free, CPU).

A monocular depth model (Depth-Anything-V2-Small, Apache-2.0) estimates how far each
pixel is; during the shot, near pixels shift more than far ones as the camera glides,
so a flat drawing reads as a 3D scene. Needs opencv + numpy; the depth model needs
torch/transformers (requirements-ai.txt). Callers fall back to the plain Ken Burns
zoom when anything here is unavailable or fails.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .util import FFMPEG, PipelineError

DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
_depth_pipe = None


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def depth_map(img_path: Path):
    """Float32 array in [0, 1] (1 = nearest), same size as the image, cached as .npy."""
    import numpy as np

    cache = img_path.with_suffix(".depth.npy")
    if cache.exists():
        return np.load(cache)
    global _depth_pipe
    from PIL import Image
    from transformers import pipeline

    if _depth_pipe is None:
        _depth_pipe = pipeline("depth-estimation", model=DEPTH_MODEL, device=-1)
    img = Image.open(img_path).convert("RGB")
    d = np.array(_depth_pipe(img)["depth"], dtype=np.float32)
    d = (d - d.min()) / max(1e-6, d.max() - d.min())
    import cv2

    d = cv2.resize(d, img.size, interpolation=cv2.INTER_CUBIC)
    np.save(cache, d)
    return d


def render_clip(img_path: Path, out_mp4: Path, seconds: float, w: int, h: int, fps: int,
                move: int = 0, depth=None) -> Path:
    """Render `seconds` of camera move over the image into out_mp4 (no audio).
    `move` picks the camera path: 0 push-in, 1 pan right, 2 pull-out, 3 pan left."""
    import cv2
    import numpy as np

    img = cv2.imread(str(img_path))
    if img is None:
        raise PipelineError(f"cannot read {img_path}")
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LANCZOS4)
    d = depth if depth is not None else depth_map(img_path)
    d = cv2.resize(d.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    d = cv2.GaussianBlur(d, (0, 0), max(2.0, w / 250))  # smooth, so edges don't tear
    near = d - 0.45  # points around the middle depth stay put

    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    cx, cy = w / 2.0, h / 2.0
    frames = max(2, round(seconds * fps))
    amp = 0.028 * w  # max sideways parallax shift of the nearest pixels, in pixels

    cmd = [FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
           "-r", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", "-pix_fmt", "yuv420p", str(out_mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(frames):
            t = i / (frames - 1)
            s = 0.5 - 0.5 * np.cos(np.pi * t)            # ease in/out, 0 -> 1
            if move == 0:    zoom, lat = 1.06 + 0.07 * s, (s - 0.5) * 0.9
            elif move == 2:  zoom, lat = 1.13 - 0.07 * s, (0.5 - s) * 0.9
            elif move == 1:  zoom, lat = 1.09, (s - 0.5) * 2.0
            else:            zoom, lat = 1.09, (0.5 - s) * 2.0
            mx = ((gx - cx) / zoom + cx - lat * amp * near).astype(np.float32)
            my = ((gy - cy) / zoom + cy - 0.35 * lat * amp * near).astype(np.float32)
            frame = cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")[-600:]
        if proc.wait() != 0:
            raise PipelineError(f"ffmpeg failed: {err}")
    except BrokenPipeError as e:
        raise PipelineError("ffmpeg closed the pipe early") from e
    finally:
        if proc.poll() is None:
            proc.kill()
    return out_mp4
