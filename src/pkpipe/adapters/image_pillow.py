"""ImagePreparePort adapter on Pillow (spec 021 FR-005), the optional extra ``images``.

The bytes of an INBOX image are untrusted and private. Before a model sees one it is decoded with a pixel
limit (a decompression bomb is refused from its header, before any pixel is decoded), the EXIF orientation is
applied and every piece of metadata (EXIF with GPS and device, XMP, ICC, comments) is dropped by re-encoding
into a fresh JPEG at most ``max_side`` pixels on its longest side. The sha256 of the prepared bytes is the key
of the description cache. Without Pillow :class:`~pkpipe.ports.ImageError` has the code ``pillow_missing``.
"""

from __future__ import annotations

import hashlib
import io

from pkpipe.domain.images import DEFAULT_MAX_PIXELS, DEFAULT_MAX_SIDE, JPEG_QUALITY
from pkpipe.ports import ImageError, PreparedImage


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def prepare_image(
    data: bytes,
    *,
    max_side: int = DEFAULT_MAX_SIDE,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    quality: int = JPEG_QUALITY,
) -> PreparedImage:
    """Decode ``data`` and return the metadata-free JPEG scaled to ``max_side`` (never enlarged)."""
    if not data:
        raise ImageError("empty", "no image bytes")
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        raise ImageError("pillow_missing", "install the optional extra: uv sync --extra images") from None
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size  # from the header: nothing is decoded yet
            source_format = probe.format or "unknown"
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ImageError("too_many_pixels", f"limit {max_pixels} pixels")
            probe.seek(0)  # animated formats: the first frame only
            image = ImageOps.exif_transpose(probe) or probe
            image.load()
            rgb = _to_rgb(image, Image)
    except ImageError:
        raise
    except Image.DecompressionBombError:  # Pillow's own limit, hit while opening a gigantic header
        raise ImageError("too_many_pixels", "decompression bomb") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        # SyntaxError: Pillow's PNG/TIFF parsers raise it for a corrupt file
        raise ImageError("decode_failed", type(exc).__name__) from None
    if max(rgb.size) > max_side:
        rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    # a fresh encode without exif=, icc_profile= or info: no metadata survives
    rgb.save(out, format="JPEG", quality=quality, optimize=True)
    prepared = out.getvalue()
    return PreparedImage(
        data=prepared,
        sha256=hashlib.sha256(prepared).hexdigest(),
        width=rgb.size[0],
        height=rgb.size[1],
        source_format=source_format,
        source_bytes=len(data),
    )


def _to_rgb(image, image_module):  # noqa: ANN001 - Pillow types are optional
    """An RGB copy; transparency is composited onto white so a screenshot with alpha stays legible."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = image_module.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


class PillowPreparer:
    """:class:`~pkpipe.ports.ImagePreparePort` on Pillow."""

    def prepare(self, data: bytes, *, max_side: int, max_pixels: int) -> PreparedImage:
        return prepare_image(data, max_side=max_side, max_pixels=max_pixels)
