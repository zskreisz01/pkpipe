"""NotebookLM backend via browser automation (spec 012 T006, optional, off by default).

NotebookLM has no official consumer API, so this backend only exists behind the :class:`LearningBackend`
interface. It drives an automation client over its own interface, :class:`NotebookLMDriver`, so that:

* the real driver (for example ``notebooklm-mcp``) is plugged in by the owner (``driver`` in the backend
  config: ``module:factory``), against the owner's own session in a private overlay setup; this repository
  never ships credentials, cookies or a Google account and no test of it uses one;
* a UI change fails fast with :class:`~pkpipe.learning.errors.BackendUnavailable` and a clear message,
  never with a hang: every driver call has a hard timeout and a missing selector is an immediate error.
"""

from __future__ import annotations

import importlib
import re
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..backend import BackendHandle, BackendStatus
from ..errors import BackendUnavailable
from ..models import BackendName, JobStatus


@runtime_checkable
class NotebookLMDriver(Protocol):
    """What the backend needs from a browser automation client. Methods raise ``DriverError`` subclasses
    (any exception is mapped to ``BackendUnavailable``); ``audio_state`` is ``generating|ready|failed``."""

    def create_notebook(self, title: str) -> str: ...

    def add_url_source(self, notebook_id: str, url: str) -> None: ...

    def start_audio_overview(self, notebook_id: str, instructions: str) -> str: ...

    def audio_state(self, notebook_id: str, artifact_id: str) -> tuple[str, str | None]: ...

    def notebook_url(self, notebook_id: str) -> str: ...

    def download_audio(self, notebook_id: str, artifact_id: str, dest: Path) -> Path: ...


class NotebookLMBrowserBackend:
    name = BackendName.NOTEBOOKLM_BROWSER

    def __init__(self, driver: NotebookLMDriver, *, call_timeout_s: float = 120.0):
        if call_timeout_s <= 0:
            raise BackendUnavailable("call_timeout_s must be positive")
        self.driver = driver
        self.call_timeout_s = call_timeout_s

    @classmethod
    def from_config(cls, cfg: dict) -> NotebookLMBrowserBackend:
        """``{"driver": "package.module:factory", "options": {...}, "call_timeout_s": 120}``: the factory
        returns the driver; ``call_timeout_s`` bounds every driver call."""
        spec = cfg.get("driver")
        if not isinstance(spec, str) or not re.fullmatch(r"[A-Za-z_][\w.]*:[A-Za-z_]\w*", spec):
            raise BackendUnavailable(
                "backend notebooklm_browser needs 'driver': 'module:factory' (a browser automation client)"
            )
        module, _, factory = spec.partition(":")
        try:
            driver = getattr(importlib.import_module(module), factory)(**(cfg.get("options") or {}))
        except Exception as exc:  # noqa: BLE001 - any import or setup problem is a configuration error
            raise BackendUnavailable(
                f"NotebookLM driver '{spec}' could not be loaded ({type(exc).__name__})"
            ) from None
        return cls(driver, call_timeout_s=float(cfg.get("call_timeout_s", 120.0)))

    def _call(self, what: str, fn, *args):
        """Run one driver call with a hard timeout. A hung browser leaves a daemon thread behind, but the
        gateway answers at once instead of blocking the flow."""
        box: dict = {}

        def target() -> None:
            try:
                box["value"] = fn(*args)
            except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
                box["error"] = exc

        worker = threading.Thread(target=target, name=f"notebooklm-{what}", daemon=True)
        worker.start()
        worker.join(self.call_timeout_s)
        if worker.is_alive():
            raise BackendUnavailable(
                f"NotebookLM browser automation timed out at '{what}' after {self.call_timeout_s:g} s: "
                "the NotebookLM page may have changed or the browser hangs"
            )
        exc = box.get("error")
        if exc is None:
            return box.get("value")
        if isinstance(exc, BackendUnavailable):
            raise exc
        raise BackendUnavailable(
            f"NotebookLM browser automation failed at '{what}' ({type(exc).__name__}): "
            "the NotebookLM page may have changed or the browser session expired"
        ) from None

    def create(self, url: str, template: str, item_id: str) -> BackendHandle:
        nb = self._call("create notebook", self.driver.create_notebook, f"pkpipe {item_id}".strip())
        self._call("add source", self.driver.add_url_source, nb, url)
        artifact = self._call("start audio overview", self.driver.start_audio_overview, nb, template)
        return BackendHandle(nb_id=str(nb), job_id=f"nlm-{nb}-{artifact}", ref=str(artifact))

    def poll(self, handle: BackendHandle) -> BackendStatus:
        state, reason = self._call("audio status", self.driver.audio_state, handle.nb_id, handle.ref or "")
        if state == "ready":
            return BackendStatus(JobStatus.READY)
        if state == "failed":
            return BackendStatus(
                JobStatus.FAILED, reason or "NotebookLM could not generate the audio overview"
            )
        return BackendStatus(JobStatus.GENERATING)

    def link(self, nb_id: str) -> str:
        return str(self._call("notebook link", self.driver.notebook_url, nb_id))

    def fetch_audio(self, handle: BackendHandle, dest: Path) -> Path:
        return Path(
            self._call("download audio", self.driver.download_audio, handle.nb_id, handle.ref or "", dest)
        )
