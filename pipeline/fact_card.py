"""Build a single bold 'mind-blowing fact' image card for the Instagram feed
image-post feature (fact_post.py). The background is the actual video's own
YouTube thumbnail (blurred/darkened, same treatment as the Reels cover in
pipeline.instagram) so the post visually ties back to that specific video
instead of a generic blank gradient card - falls back to a plain gradient
only if no thumbnail image is available."""
from __future__ import annotations

from pathlib import Path

from .util import ASSETS_DIR

W, H = 1080, 1350  # Instagram feed portrait, 4:5
_FONT = ASSETS_DIR / "fonts" / "Anton-Regular.ttf"

# (accent, deep background) pairs - rotated by the caller so consecutive
# posts don't all look identical.
_PALETTES = [
    ((124, 58, 237), (23, 20, 58)),   # violet / deep indigo
    ((236, 72, 153), (40, 12, 28)),   # pink / deep maroon
    ((14, 165, 233), (10, 25, 40)),   # sky / deep navy
    ((234, 179, 8), (38, 28, 6)),     # amber / deep brown
    ((34, 197, 94), (8, 30, 20)),     # green / deep forest
]


def _font(size: int):
    from PIL import ImageFont
    if _FONT.exists():
        try:
            return ImageFont.truetype(str(_FONT), size)
        except OSError:
            pass
    for name in ("ariblk.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    from PIL import ImageFont as _IF
    return _IF.load_default()


def _wrap(draw, text: str, font, max_w: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if draw.textlength(t, font=font) > max_w and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _gradient_background(accent: tuple[int, int, int], deep: tuple[int, int, int]):
    from PIL import Image, ImageDraw

    bg = Image.new("RGB", (W, H), deep)
    draw = ImageDraw.Draw(bg)
    for y in range(H):
        t = 1.0 - abs((y / H) - 0.5) * 1.6
        t = max(0.0, min(1.0, t)) * 0.22
        r = int(deep[0] + (accent[0] - deep[0]) * t)
        g = int(deep[1] + (accent[1] - deep[1]) * t)
        b = int(deep[2] + (accent[2] - deep[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    return bg


def _photo_background(src_path: Path):
    """Cover-crop the source image to the full WxH canvas, blurred (shrink-
    then-blowup - no blocky Gaussian-blur artifacts) and darkened so the
    overlaid text stays legible - same treatment as the Reels cover image."""
    from PIL import Image

    src = Image.open(src_path).convert("RGB")
    sw, sh = src.size
    scale = max(W / sw, H / sh)
    tiny = src.resize((max(1, round(sw * scale / 40)), max(1, round(sh * scale / 40))))
    bg = tiny.resize((round(sw * scale), round(sh * scale)), Image.BILINEAR)
    bx, by = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((bx, by, bx + W, by + H))
    return Image.eval(bg, lambda p: int(p * 0.45))


def generate(hook: str, title: str, channel: str, out_jpg: Path, seed: int = 0,
            bg_image_path: Path | None = None) -> Path:
    from PIL import ImageDraw

    accent, deep = _PALETTES[seed % len(_PALETTES)]
    if bg_image_path and bg_image_path.exists():
        try:
            bg = _photo_background(bg_image_path)
        except Exception:
            bg = _gradient_background(accent, deep)
    else:
        bg = _gradient_background(accent, deep)
    draw = ImageDraw.Draw(bg)

    # top label pill
    lf = _font(38)
    label = "MIND-BLOWING FACT"
    lw = draw.textlength(label, font=lf)
    pad = 30
    draw.rectangle([(W - lw) / 2 - pad, 80, (W + lw) / 2 + pad, 80 + 60], fill=accent)
    draw.text(((W - lw) / 2, 92), label, font=lf, fill=(15, 18, 26))

    # big hook text, centered, heavy outline for legibility over the gradient
    text = (hook or title).strip().strip('"')
    size = 78
    font = _font(size)
    max_w = W - 140
    lines = _wrap(draw, text, font, max_w)
    while len(lines) > 7 and size > 42:
        size -= 6
        font = _font(size)
        lines = _wrap(draw, text, font, max_w)

    lh = int(size * 1.28)
    total_h = lh * len(lines)
    y = (H - total_h) / 2
    for line in lines:
        lw2 = draw.textlength(line, font=font)
        x = (W - lw2) / 2
        for dx in range(-3, 4, 3):
            for dy in range(-3, 4, 3):
                draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += lh

    # bottom CTA + channel
    cf = _font(34)
    cta = "Full video on YouTube - link in bio"
    cw = draw.textlength(cta, font=cf)
    draw.text(((W - cw) / 2, H - 150), cta, font=cf, fill=accent)

    hf = _font(28)
    ht = channel.upper()
    hw = draw.textlength(ht, font=hf)
    draw.text(((W - hw) / 2, H - 95), ht, font=hf, fill=(230, 230, 230))

    bg.save(out_jpg, quality=92)
    return out_jpg
