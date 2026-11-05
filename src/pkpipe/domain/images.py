"""Image preparation for the vision model (spec 021 FR-005): decode, guard, remove metadata, scale, re-encode.

The bytes of an INBOX image are untrusted and private. Before a model sees one it is decoded with a pixel
limit (a decompression bomb is refused from its header, before any pixel is decoded), the EXIF orientation is
applied and every piece of metadata (EXIF with GPS and device, XMP, ICC, comments) is dropped by re-encoding
into a fresh JPEG at most ``max_side`` pixels on its longest side. The sha256 of the prepared bytes is the key
of the description cache. Pillow is the optional extra ``images``; without it :class:`ImageError` has the code
``pillow_missing``.
"""

from __future__ import annotations

import hashlib
import io
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

#: Longest side after scaling (``images.max_side``) and the pixel limit of the decoded source (header check).
DEFAULT_MAX_SIDE = 1024
DEFAULT_MAX_PIXELS = 40_000_000
JPEG_QUALITY = 85
PREPARED_MEDIA_TYPE = "image/jpeg"


class ImageError(Exception):
    """An image cannot be prepared. ``code`` is stable: ``pillow_missing``, ``decode_failed``,
    ``too_many_pixels``, ``empty``; the message never contains image content or a file name."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class PreparedImage:
    """The JPEG a vision model gets. ``data`` is private content: never in ``repr``, logs or spans."""

    data: bytes = field(repr=False)
    sha256: str
    width: int
    height: int
    source_format: str
    source_bytes: int

    def __repr__(self) -> str:
        return f"PreparedImage({self.width}x{self.height}, {len(self.data)} bytes, sha256={self.sha256[:12]})"


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


class ImageBuffer:
    """The prepared images of the running process, by item id (a bounded LRU).

    The flow state (and so the checkpoint) carries only digests, sizes and the description, never image
    bytes; ``image.prepare`` puts the JPEG here and the ``describe_image`` session takes it from here. A run
    that resumes after a restart finds the buffer empty and prepares the image again."""

    def __init__(self, max_entries: int = 64, max_bytes: int = 64 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._items: OrderedDict[str, PreparedImage] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, key: str, image: PreparedImage) -> None:
        with self._lock:
            self._items[key] = image
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries or self._total() > self.max_bytes:
                if len(self._items) <= 1:
                    break
                self._items.popitem(last=False)

    def get(self, key: str) -> PreparedImage | None:
        with self._lock:
            image = self._items.get(key)
            if image is not None:
                self._items.move_to_end(key)
            return image

    def pop(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def _total(self) -> int:
        return sum(len(i.data) for i in self._items.values())

    def __len__(self) -> int:
        return len(self._items)
