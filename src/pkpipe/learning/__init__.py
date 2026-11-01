"""learning-gateway (spec 012): the learning backend behind four MCP tools (``create``, ``status``, ``link``,
``audio``). Backends (Open Notebook, NotebookLM via browser automation, fake) sit behind
:class:`~pkpipe.learning.backend.LearningBackend`; the flows never know which one is configured."""

from .backend import BackendHandle, BackendStatus, LearningBackend
from .errors import BackendUnavailable, LearningError, UrlBlocked
from .gateway import LearningGateway
from .models import BackendName, JobStatus, NotebookJob

__all__ = [
    "BackendHandle",
    "BackendName",
    "BackendStatus",
    "BackendUnavailable",
    "JobStatus",
    "LearningBackend",
    "LearningError",
    "LearningGateway",
    "NotebookJob",
    "UrlBlocked",
]
