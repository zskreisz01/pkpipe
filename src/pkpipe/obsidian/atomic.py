"""Atomic, lock-protected file updates (spec 004 FR-003, T002).

Writes go to a hidden temp file in the target directory (invisible to Obsidian), are fsynced and
``os.replace``d over the target. Read-modify-write cycles re-check the content hash under an ``flock`` on the
directory and retry with a fresh read when the file changed in between (for example edited in Obsidian).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

from .errors import WriteConflict


@contextlib.contextmanager
def dir_lock(directory: Path) -> Iterator[None]:
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` all-or-nothing; never leaves a partial file or a stray temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def update_text(path: Path, transform: Callable[[str | None], str], *, retries: int = 5) -> str:
    """Read ``path`` (None when missing), apply ``transform`` and write atomically.

    ``transform`` may be called several times: it runs again on a fresh read whenever the file changed between
    the read and the write. Returns the text written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(retries):
        before = _read(path)
        new_text = transform(None if before is None else before.decode("utf-8"))
        with dir_lock(path.parent):
            if _digest(_read(path)) != _digest(before):
                continue
            write_bytes_atomic(path, new_text.encode("utf-8"))
            return new_text
    raise WriteConflict(f"{path.name} kept changing while writing; retry")
