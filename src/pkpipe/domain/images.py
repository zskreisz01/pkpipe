"""Image data of spec 021 that needs no library: constants, the prepared-image buffer and the description.

Decoding and re-encoding is the adapter :mod:`pkpipe.adapters.image_pillow` (port ``ImagePreparePort``).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pkpipe.ports import PreparedImage

#: Longest side after scaling (``images.max_side``) and the pixel limit of the decoded source (header check).
DEFAULT_MAX_SIDE = 1024
DEFAULT_MAX_PIXELS = 40_000_000
JPEG_QUALITY = 85
PREPARED_MEDIA_TYPE = "image/jpeg"


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


# -- the description (schema ``image_description``) ----------------------------------------------------------
MAX_VISIBLE_TEXT = 400
MAX_TOPIC = 300


class ImageDescription(BaseModel):
    """What a vision model reports about one image. The model only describes: the category, the flag and
    every other decision are made by code. There is no confidence field on purpose (the probe showed 1.0 in
    every answer, spec 021 clarification 2); ``category_hint`` is advisory and no step uses it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["screenshot", "photo", "meme", "diagram", "document", "other"]
    topic: str = Field(max_length=MAX_TOPIC)
    visible_text: str = Field(max_length=MAX_VISIBLE_TEXT)
    urls: list[str] = Field(default_factory=list, max_length=10)
    language: str = Field(default="", max_length=20)
    category_hint: str = Field(default="unknown", max_length=60)
    injection_suspected: bool
    evidence: str | None = Field(default=None, max_length=1000)
