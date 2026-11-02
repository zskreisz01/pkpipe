"""ContextStorePort on files (spec 011): ``<overlay>/contexts/*`` and ``<overlay>/security/*``.

The overlay is the owner's private config repository; the refresh only touches the working tree (nothing is
committed or pushed). Writes are atomic, so a reader (the next run's config load, Obsidian, an editor) never
sees a partial pack. Only ``contexts/`` and ``security/`` are reachable, with plain relative paths.
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path

from pkpipe.engine.snapshot import safe_asset_path
from pkpipe.obsidian.atomic import write_bytes_atomic

ALLOWED_DIRS = ("contexts", "security")


class FileContextStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()

    def _path(self, relative: str) -> Path:
        rel = safe_asset_path(relative)
        if rel is None or rel.split("/", 1)[0] not in ALLOWED_DIRS or "/" not in rel:
            raise ValueError(f"'{relative}' is not a context store path (contexts/... or security/...)")
        return self.root.joinpath(*rel.split("/"))

    def read(self, relative: str) -> str | None:
        try:
            return self._path(relative).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write(self, relative: str, text: str) -> None:
        path = self._path(relative)
        with self._lock:
            write_bytes_atomic(path, text.encode("utf-8"))

    def remove(self, relative: str) -> None:
        path = self._path(relative)
        with self._lock, contextlib.suppress(FileNotFoundError):
            path.unlink()
