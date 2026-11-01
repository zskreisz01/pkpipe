"""Backends of the learning gateway; :func:`build_backend` picks one by name from the configuration."""

from __future__ import annotations

from typing import Any

from ..backend import LearningBackend
from ..errors import LearningError
from ..models import BackendName
from .fake import FakeBackend
from .notebooklm_browser import NotebookLMBrowserBackend, NotebookLMDriver
from .open_notebook import OpenNotebookBackend


def build_backend(name: str, options: dict[str, Any] | None = None) -> LearningBackend:
    options = dict(options or {})
    try:
        backend = BackendName(name)
    except ValueError:
        known = ", ".join(b.value for b in BackendName)
        raise LearningError(f"unknown learning backend '{name}' (known: {known})") from None
    if backend is BackendName.FAKE:
        return FakeBackend(**options)
    if backend is BackendName.NOTEBOOKLM_BROWSER:
        return NotebookLMBrowserBackend.from_config(options)
    return OpenNotebookBackend(**options)


__all__ = [
    "FakeBackend",
    "NotebookLMBrowserBackend",
    "NotebookLMDriver",
    "OpenNotebookBackend",
    "build_backend",
]
