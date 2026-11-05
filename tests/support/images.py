"""Synthetic images for tests (spec 021 FR-013): rendered by code with Pillow, nothing personal, no file.

Tests that need Pillow use ``pytest.importorskip("PIL")`` (the dependency group of the dev environment has it,
so CI runs them; without the optional extra they skip cleanly).
"""

from __future__ import annotations

import io
import struct
import zlib


def png_header_only(width: int, height: int) -> bytes:
    """A PNG of the announced size with an empty pixel stream (a decompression bomb candidate: ``Image.open``
    reads the size from the header, no pixel is ever decoded)."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")
    )


def solid(size=(64, 48), color=(200, 30, 30), fmt: str = "PNG", mode: str = "RGB", **save) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format=fmt, **save)
    return out.getvalue()


def text_image(lines: list[str], size=(640, 360), fmt: str = "PNG") -> bytes:
    """White canvas with black text lines (default font); distinct text gives distinct bytes."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=22)
    y = 12
    for line in lines:
        draw.text((12, y), line, fill="black", font=font)
        y += 32
    out = io.BytesIO()
    img.save(out, format=fmt)
    return out.getvalue()


def jpeg_with_exif(size=(320, 200), make: str = "SyntheticCam", software: str = "synthetic-1.0") -> bytes:
    """A JPEG with EXIF: device make, software and GPS coordinates (all invented)."""
    from PIL import Image

    img = Image.new("RGB", size, (30, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = make
    exif[0x0131] = software
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (47.0, 29.0, 0.0), "E", (19.0, 2.0, 0.0)
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif)
    return out.getvalue()
