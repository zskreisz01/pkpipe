"""Image data of spec 021 that needs no library: constants, the prepared-image buffer and the description.

Decoding and re-encoding is the adapter :mod:`pkpipe.adapters.image_pillow` (port ``ImagePreparePort``).
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


# -- salvage of an answer that overran its limits ------------------------------------------------------------
_DEFAULTS: dict[str, Any] = {"urls": [], "language": "", "category_hint": "unknown", "evidence": None}
_REQUIRED = ("kind", "topic", "injection_suspected")


def _cut_string(raw: str, start: int) -> str:
    """The text of a JSON string that starts after the quote at ``start``-1 and may be cut off: unescaped
    as far as it goes (a dangling escape or half a ``\\uXXXX`` at the end is dropped)."""
    chars: list[str] = []
    i, n = start, len(raw)
    escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
    while i < n:
        c = raw[i]
        if c == '"':
            break
        if c == "\\":
            if i + 1 >= n:
                break
            e = raw[i + 1]
            if e == "u":
                digits = raw[i + 2 : i + 6]
                if len(digits) < 4 or any(d not in "0123456789abcdefABCDEF" for d in digits):
                    break
                chars.append(chr(int(digits, 16)))
                i += 6
                continue
            chars.append(escapes.get(e, e))
            i += 2
            continue
        chars.append(c)
        i += 1
    return "".join(chars)


def salvage_description(raw: str | None) -> dict[str, Any] | None:
    """A usable description out of an answer that failed schema validation only because it overran its limits.

    A vision model does not keep ``visible_text`` under 400 characters (the decoder does not enforce
    ``maxLength``): it goes on transcribing until ``max_tokens`` cuts the JSON off, or it ends the JSON with
    a field too long. Everything before the cut is complete: this reads the fields in order, keeps the
    complete ones and the text of a cut string, caps every field at its limit, drops unknown keys, fills
    only the optional fields (urls, language, category_hint, evidence) and returns ``None`` unless ``kind``,
    ``topic`` and ``injection_suspected`` are present and valid: that flag is never assumed (fail closed).
    The result goes through the same output guard, canary check and URL policy as any description.
    Content-free: no text of the answer leaves this function except in the returned fields."""
    if not raw:
        return None
    text = raw.rsplit("</think>", 1)[-1] if "</think>" in raw else raw
    start = text.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    found: dict[str, Any] = {}
    pos = start + 1
    n = len(text)
    while pos < n:
        while pos < n and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= n or text[pos] != '"':
            break
        try:
            key, pos = decoder.raw_decode(text, pos)
        except ValueError:
            break
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        if pos >= n or text[pos] != ":":
            break
        pos += 1
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        if pos >= n:
            break
        try:
            value, pos = decoder.raw_decode(text, pos)
        except ValueError:
            if text[pos] == '"':  # a string that was cut off: what there is of it
                found[key] = _cut_string(text, pos + 1)
            break
        found[key] = value
    fields = {k: found[k] for k in ImageDescription.model_fields if k in found}
    if any(k not in fields for k in _REQUIRED):
        return None
    for key, limit in (
        ("topic", MAX_TOPIC),
        ("visible_text", MAX_VISIBLE_TEXT),
        ("language", 20),
        ("category_hint", 60),
        ("evidence", 1000),
    ):
        if isinstance(fields.get(key), str):
            fields[key] = fields[key][:limit]
    if isinstance(fields.get("urls"), list):
        fields["urls"] = [u for u in fields["urls"] if isinstance(u, str) and len(u) <= 500][:10]
    for key, default in _DEFAULTS.items():
        fields.setdefault(key, default)
    fields.setdefault("visible_text", "")
    try:
        return ImageDescription.model_validate(fields).model_dump()
    except ValidationError:
        return None
