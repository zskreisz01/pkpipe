from __future__ import annotations

import hashlib

from pkpipe.ports import ImageError, PreparedImage


class FakePreparer:
    """ImagePreparePort fake that needs no Pillow: the prepared image is the input itself (its sha256 is the
    digest of the bytes). A marker in ``fail`` (a bytes substring of the input) raises that ``ImageError``
    code; empty input is ``empty``."""

    def __init__(self, fail: dict[bytes, str] | None = None):
        self.fail = dict(fail or {})
        self.calls: list[dict[str, int]] = []

    def prepare(self, data: bytes, *, max_side: int, max_pixels: int) -> PreparedImage:
        self.calls.append({"bytes": len(data), "max_side": max_side, "max_pixels": max_pixels})
        if not data:
            raise ImageError("empty")
        for marker, code in self.fail.items():
            if marker in data:
                raise ImageError(code)
        return PreparedImage(
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            width=64,
            height=48,
            source_format="FAKE",
            source_bytes=len(data),
        )
