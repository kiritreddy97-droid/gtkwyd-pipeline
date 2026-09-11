"""Generate a 1280x720 thumbnail: stock frame + dark gradient + category badge
+ big bold title. Tuned to read well at small sizes in the YouTube feed."""
from __future__ import annotations

import re
from pathlib import Path

from .util import ASSETS_DIR, FFMPEG, run

TW, TH = 1280, 720
_BUNDLED_FONT = ASSETS_DIR / "fonts" / "Anton-Regular.ttf"

# category -> (badge label, accent RGB)
_CATS = [
    (r"\b(ai|artificial intelligence|robot|algorithm|machine learning|computer|chip)\b",
     ("FUTURE TECH", (99, 102, 241))),
    (r"\b(space|planet|moon|mars|star|galaxy|cosmos|nasa|orbit|solar|astronaut|black hole|comet)\b",
     ("SPACE", (56, 189, 248))),
    (r"\b(history|ancient|roman|empire|centur|archaeolog|lost|civilisation|civilization|war)\b",
     ("HIDDEN HISTORY", (245, 158, 11))),
    (r"\b(brain|psycholog|mind|memory|behaviou?r|bias|cognitive|perception|emotion)\b",
     ("PSYCHOLOGY", (236, 72, 153))),
    (r"\b(news|study finds|researchers|new research|scientists discover|breakthrough)\b",
     ("SCIENCE NEWS", (34, 197, 94))),
    (r"\bwhat if\b", ("WHAT IF", (168, 85, 247))),
    (r"\bwhat happens\b", ("WHAT HAPPENS", (20, 184, 166))),
]
_DEFAULT_CAT = ("DID YOU KNOW", (250, 204, 21))


def _category(title: str, tags: list[str]) -> tuple[str, tuple[int, int, int]]:
    hay = (title + " " + " ".join(tags or [])).lower()
    for pat, out in _CATS:
        if re.search(pat, hay):
            return out
    return _DEFAULT_CAT


def _font(size: int):
    from PIL import ImageFont
    if _BUNDLED_FONT.exists():
        try:
            return ImageFont.truetype(str(_BUNDLED_FONT), size)
        except OSError:
            pass
    for name in ("ariblk.ttf", "arialbd.ttf", "seguisb.ttf", "arial.ttf",
                 "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
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


def _first_frame(video: Path, dest: Path) -> Path:
    run([FFMPEG, "-y", "-ss", "2.5", "-i", video, "-vframes", "1",
         "-vf", f"scale={TW}:{TH}:force_original_aspect_ratio=increase,crop={TW}:{TH}",
         dest])
    return dest


def generate(title: str, channel: str, first_asset, workdir: Path, out_png: Path,
             tags: list[str] | None = None) -> Path:
    from PIL import Image, ImageDraw

    badge_text, accent = _category(title, tags or [])
    clean_title = re.sub(r"\s*#\w+\s*$", "", title).strip()

    bg = Image.new("RGB", (TW, TH), (15, 18, 26))
    try:
        if first_asset is not None:
            src = first_asset.path
            if first_asset.kind == "video":
                src = _first_frame(src, workdir / "_thumb_frame.jpg")
            img = Image.open(src).convert("RGB")
            scale = max(TW / img.width, TH / img.height)
            img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
            l, t = (img.width - TW) // 2, (img.height - TH) // 2
            bg = img.crop((l, t, l + TW, t + TH))
    except Exception:
        pass

    # cinematic dark gradient from the bottom + slight overall darken
    ov = Image.new("RGBA", (TW, TH), (0, 0, 0, 60))
    od = ImageDraw.Draw(ov)
    for y in range(TH):
        a = int(235 * (max(0, y - TH * 0.35) / (TH * 0.65)) ** 1.5)
        od.line([(0, y), (TW, y)], fill=(0, 0, 0, min(a, 235)))
    bg = Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")
    draw = ImageDraw.Draw(bg)

    # left accent bar
    draw.rectangle([0, 0, 12, TH], fill=accent)

    # category badge (top-left)
    bf = _font(34)
    bw = draw.textlength(badge_text, font=bf)
    draw.rectangle([40, 34, 40 + bw + 40, 34 + 54], fill=accent)
    draw.text((60, 43), badge_text, font=bf, fill=(15, 18, 26))

    # channel handle (top-right, subtle)
    hf = _font(26)
    ht = channel.upper()
    draw.text((TW - 40 - draw.textlength(ht, font=hf), 47), ht, font=hf,
              fill=(255, 255, 255))

    # title (bottom-left, big, wrapped, heavy outline)
    size = 104
    font = _font(size)
    max_w = TW - 100
    lines = _wrap(draw, clean_title, font, max_w)
    while len(lines) > 3 and size > 54:
        size -= 8
        font = _font(size)
        lines = _wrap(draw, clean_title, font, max_w)

    lh = int(size * 1.12)
    y = TH - 56 - lh * len(lines)
    for line in lines:
        for dx in range(-4, 5, 2):
            for dy in range(-4, 5, 2):
                draw.text((50 + dx, y + dy), line, font=font, fill=(0, 0, 0))
        draw.text((50, y), line, font=font, fill=(255, 255, 255))
        y += lh

    bg.save(out_png)
    return out_png
