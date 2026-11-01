"""Scripted backend for tests and flow evals (spec 012 T002): no network, deterministic.

Per ``item_id``: ready after ``ready_after`` polls (default 1), ``fail`` -> the generation fails with a
reason,
``never`` -> stays generating (the timeout or the wait deadline ends it), ``down`` -> ``create`` fails like an
unreachable service. ``audio_bytes`` is written as the "podcast".
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..backend import BackendHandle, BackendStatus
from ..errors import BackendUnavailable, LearningError
from ..models import BackendName, JobStatus


class FakeBackend:
    name = BackendName.FAKE

    def __init__(
        self,
        ready_after: int = 1,
        fail: Iterable[str] = (),
        never: Iterable[str] = (),
        down: bool = False,
        audio_bytes: bytes = b"ID3 fake podcast",
        fail_reason: str = "scripted failure",
    ):
        self.ready_after = ready_after
        self.fail, self.never = set(fail), set(never)
        self.down = down
        self.audio_bytes = audio_bytes
        self.fail_reason = fail_reason
        self.created: list[dict[str, str]] = []  # what the backend received: url, template, item_id only
        self.polls: dict[str, int] = {}

    def create(self, url: str, template: str, item_id: str) -> BackendHandle:
        if self.down:
            raise BackendUnavailable("fake backend is down")
        self.created.append({"url": url, "template": template, "item_id": item_id})
        key = item_id or f"n{len(self.created)}"
        return BackendHandle(nb_id=f"nb-{key}", job_id=f"job-{key}")

    def poll(self, handle: BackendHandle) -> BackendStatus:
        item = handle.job_id.removeprefix("job-")
        self.polls[handle.job_id] = self.polls.get(handle.job_id, 0) + 1
        if item in self.fail:
            return BackendStatus(JobStatus.FAILED, self.fail_reason)
        if item in self.never or self.polls[handle.job_id] < self.ready_after:
            return BackendStatus(JobStatus.GENERATING)
        return BackendStatus(JobStatus.READY)

    def link(self, nb_id: str) -> str:
        return f"https://notebook.example.invalid/{nb_id}"

    def fetch_audio(self, handle: BackendHandle, dest: Path) -> Path:
        if self.audio_bytes is None:
            raise LearningError("no audio")
        path = dest / f"{handle.job_id}.mp3"
        path.write_bytes(self.audio_bytes)
        return path
