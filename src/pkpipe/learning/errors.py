"""Typed failures of the learning gateway. Messages never contain credentials or full URLs of private data."""

from __future__ import annotations


class LearningError(Exception):
    """Base class; the message is safe to show to the owner and to record as a failure reason."""


class BackendUnavailable(LearningError):
    """The backend could not be reached or failed (Open Notebook down, NotebookLM UI changed)."""


class UrlBlocked(LearningError):
    """The URL is refused by the URL filter (scheme, private network, deny list)."""


class UnknownJob(LearningError):
    """No such job or notebook in the job store."""


class NotReady(LearningError):
    """The result (audio, link) is requested before the generation finished."""
