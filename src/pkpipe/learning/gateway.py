"""The gateway service behind the MCP tools (spec 012 FR-001 to FR-003).

It owns what must not depend on the backend: URL filter, job store, timeout, failure reasons, idempotent
``create`` per item and the audio folder. A backend only talks to its learning service.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .backend import BackendHandle, LearningBackend
from .errors import BackendUnavailable, LearningError, NotReady, QuotaExceeded, UnknownJob, UrlBlocked
from .failures import until_text
from .models import JobStatus, NotebookJob
from .store import JobStore
from .urlcheck import Resolver, _resolve, check_url

DEFAULT_TIMEOUT_S = 90 * 60  # local TTS is slow (FR-003)
MAX_POLL_ERRORS = 5
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def clean_reason(text: str, limit: int = 300) -> str:
    """A failure reason that is safe to store: one line, no control characters, bounded."""
    one = _CONTROL.sub(" ", str(text)).strip()
    one = re.sub(r"\s+", " ", one)
    return one[:limit] if one else "unknown error"


class LearningMetrics(Protocol):
    """The two learning metrics of docs/operations.md (``pkpipe.telemetry.Metrics`` implements them)."""

    def learning_timeout(self) -> None: ...

    def learning_generation(self, seconds: float) -> None: ...


class _NoMetrics:
    def learning_timeout(self) -> None:
        pass

    def learning_generation(self, seconds: float) -> None:
        pass


@dataclass
class GatewayConfig:
    timeout_s: float = DEFAULT_TIMEOUT_S
    block_private_networks: bool = True
    deny: list[str] = field(default_factory=list)
    #: Reads the deny-list file again on every create (the security-flag handler extends it while the
    #: gateway runs, spec 009); ``None`` = only ``deny``.
    deny_loader: Callable[[], Iterable[str]] | None = None
    audio_dir: Path = Path("learning-audio")
    max_template_chars: int = 4000


class LearningGateway:
    def __init__(
        self,
        backend: LearningBackend,
        store: JobStore,
        config: GatewayConfig | None = None,
        *,
        clock: Callable[[], float] = time.time,
        resolver: Resolver = _resolve,
        metrics: LearningMetrics | None = None,
    ):
        self.backend = backend
        self.store = store
        self.config = config or GatewayConfig()
        self.clock = clock
        self.resolver = resolver
        self.metrics = metrics or _NoMetrics()

    # -- tools -----------------------------------------------------------------------------------------
    def create(self, url: str, template: str, item_id: str = "") -> dict[str, Any]:
        """Notebook from ``url`` + podcast job. The gateway only forwards the URL and the template."""
        cfg = self.config
        deny = list(cfg.deny)
        if cfg.deny_loader is not None:
            deny += [d for d in cfg.deny_loader() if d not in deny]
        url = check_url(
            url, block_private_networks=cfg.block_private_networks, deny=deny, resolver=self.resolver
        )
        template = (template or "").strip()
        if len(template) > cfg.max_template_chars:
            raise LearningError("template too long")
        now = self.clock()
        waiting: NotebookJob | None = None
        if item_id:  # a restart or a repeated run must not create a second notebook for the same item
            existing = self.store.active_for_item(item_id)
            if existing and existing.status is JobStatus.WAITING_QUOTA:
                waiting = existing
            elif existing:
                return existing.public()
        try:
            if waiting is not None:
                handle = self._resume(waiting, template, now)
            else:
                handle = self.backend.create(url, template, item_id)
        except QuotaExceeded as exc:
            return self._wait_for_quota(exc, waiting, item_id, now)
        except LearningError as exc:
            reason = clean_reason(exc)
            self.store.save(
                NotebookJob(
                    job_id=f"failed-{uuid.uuid4().hex[:12]}",
                    item_id=item_id,
                    backend=self.backend.name,
                    status=JobStatus.FAILED,
                    reason=reason,
                    started_at=now,
                    finished_at=now,
                )
            )
            raise type(exc)(reason) from None
        job = NotebookJob(
            job_id=handle.job_id,
            item_id=item_id,
            backend=self.backend.name,
            nb_id=handle.nb_id,
            backend_ref=handle.ref,
            started_at=now,
        )
        if waiting is not None and waiting.job_id != job.job_id:
            self._finish(waiting, JobStatus.FAILED, "superseded: the audio overview was started again")
        self.store.save(job)
        return job.public()

    def _resume(self, job: NotebookJob, template: str, now: float) -> BackendHandle:
        """A job that waited for the quota: still waiting -> the same refusal (nothing is called); the wait is
        over -> the backend starts only the audio overview of the notebook it already has."""
        if job.retry_after and now < job.retry_after:
            raise QuotaExceeded(
                f"quota: NotebookLM usage limit reached, waiting until {until_text(job.retry_after)}",
                retry_after=job.retry_after,
                nb_id=job.nb_id,
            )
        resume = getattr(self.backend, "resume", None)
        if resume is None or not job.nb_id:
            raise BackendUnavailable("the backend cannot resume a notebook; the item is started again")
        return resume(job.nb_id, template)

    def _wait_for_quota(
        self, exc: QuotaExceeded, waiting: NotebookJob | None, item_id: str, now: float
    ) -> dict[str, Any]:
        """Not a failure (spec 032): keep the notebook as ``waiting_quota`` (when one exists) and tell the
        flow to leave the item released; the next run after ``retry_after`` continues. Raises the typed
        error."""
        reason = clean_reason(exc)
        nb_id = exc.nb_id or (waiting.nb_id if waiting else None)
        if nb_id:
            job = waiting or NotebookJob(
                job_id=f"wait-{uuid.uuid4().hex[:12]}",
                item_id=item_id,
                backend=self.backend.name,
                nb_id=nb_id,
                started_at=now,
            )
            job.status, job.reason, job.retry_after = JobStatus.WAITING_QUOTA, reason, exc.retry_after
            self.store.save(job)
        raise QuotaExceeded(reason, retry_after=exc.retry_after, nb_id=nb_id)

    def status(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        if job.status is not JobStatus.GENERATING:
            return job.public()
        now = self.clock()
        if now - job.started_at >= self.config.timeout_s:
            self.metrics.learning_timeout()
            return self._finish(
                job, JobStatus.FAILED, f"timeout after {round(self.config.timeout_s / 60)} min"
            )
        try:
            result = self.backend.poll(self._handle(job))
        except BackendUnavailable as exc:
            job.poll_errors += 1
            if job.poll_errors >= MAX_POLL_ERRORS:
                return self._finish(job, JobStatus.FAILED, clean_reason(f"backend unavailable: {exc}"))
            self.store.save(job)
            return job.public()
        job.poll_errors = 0
        if result.ref:
            job.backend_ref = result.ref
        if result.status is JobStatus.GENERATING:
            self.store.save(job)
            return job.public()
        if result.status is JobStatus.READY:
            job.link = job.link or self.backend.link(job.nb_id or "")
            self.metrics.learning_generation(max(0.0, now - job.started_at))
            return self._finish(job, JobStatus.READY, None)
        return self._finish(job, JobStatus.FAILED, clean_reason(result.reason or "generation failed"))

    def link(self, nb_id: str) -> dict[str, Any]:
        job = self.store.by_notebook(nb_id)
        if job is None or not job.nb_id:
            raise UnknownJob("unknown notebook")
        if not job.link:
            job.link = self.backend.link(job.nb_id)
            self.store.save(job)
        return {"nb_id": job.nb_id, "url": job.link}

    def audio(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        if job.status is not JobStatus.READY:
            raise NotReady(f"job is {job.status.value}, audio exists only for a ready job")
        if job.audio_path and Path(job.audio_path).is_file():
            return {
                "job_id": job.job_id,
                "audio_path": job.audio_path,
                "bytes": Path(job.audio_path).stat().st_size,
            }
        dest = self.config.audio_dir / re.sub(r"[^A-Za-z0-9_.-]", "_", job.job_id)
        dest.mkdir(parents=True, exist_ok=True)
        path = self.backend.fetch_audio(self._handle(job), dest)
        job.audio_path = str(path)
        self.store.save(job)
        return {"job_id": job.job_id, "audio_path": job.audio_path, "bytes": path.stat().st_size}

    # -- helpers ---------------------------------------------------------------------------------------
    def _job(self, job_id: str) -> NotebookJob:
        job = self.store.get(job_id)
        if job is None:
            raise UnknownJob("unknown job")
        return job

    @staticmethod
    def _handle(job: NotebookJob) -> BackendHandle:
        return BackendHandle(nb_id=job.nb_id or "", job_id=job.job_id, ref=job.backend_ref)

    def _finish(self, job: NotebookJob, status: JobStatus, reason: str | None) -> dict[str, Any]:
        job.status = status
        job.reason = reason
        job.finished_at = self.clock()
        self.store.save(job)
        return job.public()


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "GatewayConfig",
    "LearningGateway",
    "LearningMetrics",
    "UrlBlocked",
    "clean_reason",
]
