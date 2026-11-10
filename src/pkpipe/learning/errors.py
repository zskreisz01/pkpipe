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


class QuotaExceeded(BackendUnavailable):
    """The service refuses new work until a quota window resets (NotebookLM rolling or weekly limit).

    Not a failure of the item: the gateway keeps the notebook (if one exists) as ``waiting_quota`` and the
    next run continues after ``retry_after`` (epoch seconds, ``None`` = unknown). ``nb_id`` is set when the
    notebook and its source already exist, so a later run starts only the audio overview."""

    def __init__(self, message: str, retry_after: float | None = None, nb_id: str | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.nb_id = nb_id


class AuthExpired(BackendUnavailable):
    """The owner's session of the service has expired: ``nlm login`` is needed (no retry helps)."""
