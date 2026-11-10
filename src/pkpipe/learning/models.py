"""``NotebookJob`` (spec 012 data model): one learning item's notebook and podcast generation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class BackendName(StrEnum):
    OPEN_NOTEBOOK = "open_notebook"
    NOTEBOOKLM_BROWSER = "notebooklm_browser"
    FAKE = "fake"


class JobStatus(StrEnum):
    GENERATING = "generating"
    #: A quota window is exhausted (spec 032): the notebook is kept, the audio starts after ``retry_after``.
    WAITING_QUOTA = "waiting_quota"
    READY = "ready"
    FAILED = "failed"


class NotebookJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    item_id: str = ""
    backend: BackendName
    nb_id: str | None = None
    status: JobStatus = JobStatus.GENERATING
    audio_path: str | None = None
    link: str | None = None
    reason: str | None = Field(default=None, description="Why the job failed (never a secret)")
    #: Backend specific reference needed later (for Open Notebook: the podcast episode id).
    backend_ref: str | None = None
    poll_errors: int = 0
    #: Epoch seconds when a ``waiting_quota`` job may start its audio overview (spec 032).
    retry_after: float | None = None
    started_at: float = 0.0
    finished_at: float | None = None

    def public(self) -> dict:
        """What the MCP tools return: no backend internals."""
        d = self.model_dump(mode="json", exclude={"backend_ref", "poll_errors"})
        d["elapsed_s"] = round((self.finished_at or self.started_at) - self.started_at, 1)
        return d
