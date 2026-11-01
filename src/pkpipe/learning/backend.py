"""The learning backend port (spec 012 FR-001): everything a learning service has to do for the gateway."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import BackendName, JobStatus


@dataclass(frozen=True)
class BackendHandle:
    """A notebook with a running generation. ``ref`` is opaque backend state kept in the job store."""

    nb_id: str
    job_id: str
    ref: str | None = None


@dataclass(frozen=True)
class BackendStatus:
    status: JobStatus
    reason: str | None = None
    #: Updated backend reference once known (Open Notebook: the episode id of a finished job).
    ref: str | None = None


@runtime_checkable
class LearningBackend(Protocol):
    """Implementations raise :class:`~pkpipe.learning.errors.BackendUnavailable` when the service cannot be
    reached or fails; ``poll`` reports a failed *generation* as ``JobStatus.FAILED`` with a reason instead."""

    name: BackendName

    def create(self, url: str, template: str, item_id: str) -> BackendHandle:
        """Create a notebook from ``url`` and start the podcast generation guided by ``template``."""
        ...

    def poll(self, handle: BackendHandle) -> BackendStatus: ...

    def link(self, nb_id: str) -> str:
        """URL the owner opens (in the browser or the mobile app) to reach the notebook."""
        ...

    def fetch_audio(self, handle: BackendHandle, dest: Path) -> Path:
        """Download the finished podcast into ``dest`` (a directory) and return the file path."""
        ...
