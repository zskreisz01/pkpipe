"""ContextStorePort fake (spec 011): files in memory, same path rules as the file adapter."""

from __future__ import annotations

from pkpipe.adapters.context_store_files import ALLOWED_DIRS
from pkpipe.engine.snapshot import safe_asset_path


class FakeContextStore:
    def __init__(self, files: dict[str, str] | None = None):
        self.files: dict[str, str] = dict(files or {})
        self.writes: list[str] = []

    def _key(self, relative: str) -> str:
        rel = safe_asset_path(relative)
        if rel is None or rel.split("/", 1)[0] not in ALLOWED_DIRS or "/" not in rel:
            raise ValueError(f"'{relative}' is not a context store path (contexts/... or security/...)")
        return rel

    def read(self, relative: str) -> str | None:
        return self.files.get(self._key(relative))

    def write(self, relative: str, text: str) -> None:
        self.files[self._key(relative)] = text
        self.writes.append(self._key(relative))

    def remove(self, relative: str) -> None:
        self.files.pop(self._key(relative), None)
