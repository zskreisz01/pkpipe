"""Synthetic images for the ``images`` eval suite (spec 021 FR-013, US4): rendered by code, never a photo of
anything real, so the truth of every image (its text, URL, kind, whether it carries an injection) is known.

A dataset case describes the picture as data (``input.render``); :func:`render` draws it with Pillow (the
optional extra ``images``) and :func:`render_ref` is its stable identity: a digest of the recipe and
:data:`GENERATOR_VERSION`. The cassette of the suite is keyed by that identity (``ImageInput.ref``), not by
the encoded bytes, which can differ between imaging library versions; bump :data:`GENERATOR_VERSION` when the
drawing changes, so a cassette recorded for the old drawing is not replayed for the new one.

Kinds of recipes: ``project_page`` (a browser window with title, tagline, bullets and a URL), ``post`` (a
social post card), ``chart`` (a bar chart with a caption), ``meme`` (big top and bottom text), ``photo`` (a
landscape without any text) and ``text_card`` (a card with a heading and lines, for example one that carries
an instruction aimed at an AI).
"""

from __future__ import annotations

import hashlib
import io
import json
import random
from typing import Any

GENERATOR_VERSION = "1"
WIDTH, HEIGHT = 1000, 640
KINDS = ("project_page", "post", "chart", "meme", "photo", "text_card")


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def render_ref(recipe: dict[str, Any]) -> str:
    """The stable identity of a rendering: recipe plus generator version, independent of the encoder."""
    blob = json.dumps({"v": GENERATOR_VERSION, "recipe": recipe}, sort_keys=True, ensure_ascii=False)
    return "synthetic:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def render(recipe: dict[str, Any]) -> bytes:
    """The PNG of a recipe (deterministic for one Pillow version)."""
    from PIL import Image, ImageDraw

    kind = recipe.get("kind")
    if kind not in KINDS:
        raise ValueError(f"unknown synthetic image kind {kind!r} (known: {', '.join(KINDS)})")
    img = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(img)
    getattr(_Painter, kind)(draw, img, recipe)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _font(size: int):
    from PIL import ImageFont

    return ImageFont.load_default(size=size)


def _centered(draw, y: int, text: str, font, fill, width: int = WIDTH) -> None:
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (box[2] - box[0])) // 2, y), text, font=font, fill=fill)


class _Painter:
    """One drawing function per recipe kind (``fn(draw, image, recipe)``)."""

    @staticmethod
    def project_page(draw, img, r: dict[str, Any]) -> None:
        draw.rectangle([0, 0, WIDTH, 64], fill=(222, 226, 232))
        draw.rounded_rectangle([90, 14, WIDTH - 40, 50], radius=14, fill="white")
        draw.text((110, 18), str(r.get("url", "")), font=_font(26), fill=(40, 60, 110))
        for i, c in enumerate(((235, 90, 80), (240, 190, 60), (90, 200, 100))):
            draw.ellipse([18 + i * 22, 24, 32 + i * 22, 38], fill=c)
        draw.text((60, 110), str(r.get("title", "")), font=_font(76), fill=(20, 24, 40))
        draw.text((60, 215), str(r.get("tagline", "")), font=_font(34), fill=(70, 78, 98))
        y = 320
        for bullet in r.get("bullets", []):
            draw.ellipse([64, y + 12, 78, y + 26], fill=(40, 90, 200))
            draw.text((100, y), str(bullet), font=_font(32), fill=(30, 34, 50))
            y += 62
        draw.rounded_rectangle([60, HEIGHT - 110, 330, HEIGHT - 40], radius=12, fill=(40, 90, 200))
        draw.text((100, HEIGHT - 98), "Get started", font=_font(36), fill="white")

    @staticmethod
    def post(draw, img, r: dict[str, Any]) -> None:
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(240, 242, 245))
        draw.rounded_rectangle([60, 50, WIDTH - 60, HEIGHT - 50], radius=28, fill="white")
        draw.ellipse([100, 90, 190, 180], fill=(120, 150, 230))
        draw.text((215, 95), str(r.get("author", "")), font=_font(40), fill=(20, 24, 40))
        draw.text((215, 145), str(r.get("handle", "")), font=_font(30), fill=(110, 118, 135))
        y = 225
        for line in r.get("text", []):
            draw.text((100, y), str(line), font=_font(38), fill=(25, 28, 40))
            y += 58
        draw.text(
            (100, HEIGHT - 120),
            f"{r.get('replies', 0)} replies    {r.get('likes', 0)} likes",
            font=_font(30),
            fill=(110, 118, 135),
        )

    @staticmethod
    def chart(draw, img, r: dict[str, Any]) -> None:
        _centered(draw, 24, str(r.get("title", "")), _font(44), (20, 24, 40))
        series = r.get("series", [])
        top = max((v for _, v in series), default=1) or 1
        left, base, plot_h = 120, HEIGHT - 150, 380
        draw.line([left - 20, base, WIDTH - 60, base], fill=(60, 60, 70), width=3)
        draw.line([left - 20, base, left - 20, base - plot_h - 10], fill=(60, 60, 70), width=3)
        slot = (WIDTH - 60 - left) // max(1, len(series))
        for i, (label, value) in enumerate(series):
            x0 = left + i * slot + slot // 6
            x1 = left + (i + 1) * slot - slot // 6
            h = int(plot_h * value / top)
            draw.rectangle([x0, base - h, x1, base], fill=(60, 120, 210))
            draw.text((x0, base - h - 38), str(value), font=_font(28), fill=(20, 24, 40))
            draw.text((x0, base + 12), str(label), font=_font(28), fill=(20, 24, 40))
        _centered(draw, HEIGHT - 70, str(r.get("caption", "")), _font(30), (90, 98, 115))

    @staticmethod
    def meme(draw, img, r: dict[str, Any]) -> None:
        color = tuple(r.get("color", (70, 110, 90)))
        for y in range(HEIGHT):
            shade = int(60 * y / HEIGHT)
            draw.line([0, y, WIDTH, y], fill=tuple(max(0, c - shade) for c in color))
        draw.ellipse(
            [WIDTH // 2 - 110, HEIGHT // 2 - 110, WIDTH // 2 + 110, HEIGHT // 2 + 110], fill=(250, 220, 90)
        )
        for dx in (-45, 45):
            draw.ellipse(
                [WIDTH // 2 + dx - 14, HEIGHT // 2 - 40, WIDTH // 2 + dx + 14, HEIGHT // 2 - 12], fill="black"
            )
        draw.arc(
            [WIDTH // 2 - 60, HEIGHT // 2 - 10, WIDTH // 2 + 60, HEIGHT // 2 + 70],
            20,
            160,
            fill="black",
            width=6,
        )
        font = _font(72)
        for y, text in ((24, r.get("top", "")), (HEIGHT - 110, r.get("bottom", ""))):
            for ox, oy in ((-3, 0), (3, 0), (0, -3), (0, 3)):  # outline
                _centered(draw, y + oy, str(text).upper(), font, "black", WIDTH + 2 * ox)
            _centered(draw, y, str(text).upper(), font, "white")

    @staticmethod
    def photo(draw, img, r: dict[str, Any]) -> None:
        rng = random.Random(int(r.get("seed", 1)))
        for y in range(HEIGHT):
            t = y / HEIGHT
            draw.line([0, y, WIDTH, y], fill=(int(120 + 100 * t), int(170 + 60 * t), int(235 - 30 * t)))
        sx, sy = rng.randint(150, 850), rng.randint(70, 170)
        draw.ellipse([sx - 55, sy - 55, sx + 55, sy + 55], fill=(255, 235, 150))
        for layer, shade in enumerate(((95, 110, 130), (70, 95, 90), (45, 80, 60))):
            base = HEIGHT - 260 + layer * 70
            pts = [(0, HEIGHT)]
            x = 0
            while x <= WIDTH + 80:
                pts.append((x, base - rng.randint(40, 190)))
                x += rng.randint(90, 180)
            pts += [(WIDTH, HEIGHT)]
            draw.polygon(pts, fill=shade)
        draw.rectangle([0, HEIGHT - 70, WIDTH, HEIGHT], fill=(40, 70, 45))

    @staticmethod
    def text_card(draw, img, r: dict[str, Any]) -> None:
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(250, 248, 240))
        draw.rectangle([0, 0, WIDTH, 14], fill=(200, 120, 60))
        draw.text((60, 50), str(r.get("heading", "")), font=_font(58), fill=(30, 30, 40))
        y = 150
        for line in r.get("lines", []):
            draw.text((60, y), str(line), font=_font(36), fill=(40, 42, 55))
            y += 56
