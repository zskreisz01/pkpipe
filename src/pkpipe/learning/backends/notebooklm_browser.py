"""NotebookLM backend via browser automation (spec 012 T006, optional, off by default).

NotebookLM has no official consumer API, so this backend only exists behind the :class:`LearningBackend`
interface. It drives an automation client over its own interface, :class:`NotebookLMDriver`, so that:

* the real driver (for example ``notebooklm-mcp``) is plugged in by the owner (``driver`` in the backend
  config: ``module:factory``), against the owner's own session in a private overlay setup; this repository
  never ships credentials, cookies or a Google account and no test of it uses one;
* a failure is classified (spec 032): quota, login needed, page changed, transient or unknown; a transient one
  is retried a bounded number of times with a backoff, a quota one is reported as :class:`QuotaExceeded`
  (the gateway keeps the notebook and the next run continues), and the optional driver method ``usage()``
  (the JSON of ``nlm usage --json``) lets the backend check the quota before it creates anything;
* a UI change fails fast with :class:`~pkpipe.learning.errors.BackendUnavailable` and a clear message,
  never with a hang: every driver call has a hard timeout and a missing selector is an immediate error.
"""

from __future__ import annotations

import importlib
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..backend import BackendHandle, BackendStatus
from ..errors import AuthExpired, BackendUnavailable, QuotaExceeded
from ..failures import (
    AUTH,
    QUOTA,
    TRANSIENT,
    classify_failure,
    quota_reset_guess,
    quota_wait,
    until_text,
)
from ..models import BackendName, JobStatus

DEFAULT_DRIVER = "pkpipe.learning.drivers.nlm_cli:make_driver"  # the ``nlm`` CLI of the owner (spec 032)
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_S = (5.0, 20.0)
DETAIL_CHARS = 160
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


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

    # Optional: ``usage() -> dict`` returns the JSON of ``nlm usage --json`` (spec 032) for the quota check.


class NotebookLMBrowserBackend:
    name = BackendName.NOTEBOOKLM_BROWSER

    def __init__(
        self,
        driver: NotebookLMDriver,
        *,
        call_timeout_s: float = 120.0,
        retries: int = DEFAULT_RETRIES,
        backoff_s: tuple[float, ...] = DEFAULT_BACKOFF_S,
        min_remaining_percent: float = 5.0,
        reason_detail: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if call_timeout_s <= 0:
            raise BackendUnavailable("call_timeout_s must be positive")
        if retries < 0:
            raise BackendUnavailable("retries must not be negative")
        self.driver = driver
        self.call_timeout_s = call_timeout_s
        self.retries = retries
        self.backoff_s = tuple(backoff_s) or (0.0,)
        self.min_remaining_percent = min_remaining_percent
        #: Append the cleaned short message of the driver to a failure reason (off: only the class and the
        #: error type, because a driver message may name a page element).
        self.reason_detail = reason_detail
        self.sleep = sleep

    @classmethod
    def from_config(cls, cfg: dict) -> NotebookLMBrowserBackend:
        """``{"driver": "package.module:factory", "options": {...}, "call_timeout_s": 120, "retries": 2,
        "backoff_s": [5, 20], "min_remaining_percent": 5, "reason_detail": false}``: the factory returns the
        driver (default: the shipped ``nlm`` CLI driver); ``call_timeout_s`` bounds every driver call."""
        spec = cfg.get("driver", DEFAULT_DRIVER)
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
        return cls(
            driver,
            call_timeout_s=float(cfg.get("call_timeout_s", 120.0)),
            retries=int(cfg.get("retries", DEFAULT_RETRIES)),
            backoff_s=tuple(float(x) for x in cfg.get("backoff_s", DEFAULT_BACKOFF_S)),
            min_remaining_percent=float(cfg.get("min_remaining_percent", 5.0)),
            reason_detail=bool(cfg.get("reason_detail", False)),
        )

    # -- one driver call ------------------------------------------------------------------------------------
    def _failure(self, what: str, exc: BaseException) -> BackendUnavailable:
        """The classified error of a failed driver call (fixed texts, the driver message only if allowed)."""
        raw = " ".join(_CONTROL.sub(" ", str(exc)).split())
        kind = classify_failure(raw)
        detail = f": {raw[:DETAIL_CHARS]}" if self.reason_detail and raw else ""
        head = f"NotebookLM browser automation failed at '{what}' ({type(exc).__name__})"
        if kind == QUOTA:
            return QuotaExceeded(
                f"quota: NotebookLM refused '{what}' because a usage limit is reached{detail}"
            )
        if kind == AUTH:
            return AuthExpired(
                f"auth: NotebookLM needs a new login (run 'nlm login'), failed at '{what}'{detail}"
            )
        err = BackendUnavailable(
            f"{head}: the NotebookLM page may have changed or the browser session expired{detail}"
            if kind != TRANSIENT
            else f"{head}: transient error{detail}"
        )
        err.kind = kind  # type: ignore[attr-defined]
        return err

    def _once(self, what: str, fn, *args):
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
            err = BackendUnavailable(
                f"NotebookLM browser automation timed out at '{what}' after {self.call_timeout_s:g} s: "
                "the NotebookLM page may have changed or the browser hangs"
            )
            err.kind = "timeout"  # type: ignore[attr-defined]  # a hung browser is not retried: threads pile up
            raise err
        exc = box.get("error")
        if exc is None:
            return box.get("value")
        if isinstance(exc, BackendUnavailable):
            raise exc
        raise self._failure(what, exc) from None

    def _call(self, what: str, fn, *args, retry: bool = True):
        """:meth:`_once` plus a bounded retry with backoff of a transient failure (never of quota, login or
        a changed page: waiting does not help those)."""
        attempt = 0
        while True:
            try:
                return self._once(what, fn, *args)
            except (QuotaExceeded, AuthExpired):
                raise
            except BackendUnavailable as exc:
                transient = getattr(exc, "kind", None) == TRANSIENT
                if not (retry and transient) or attempt >= self.retries:
                    raise
                self.sleep(self.backoff_s[min(attempt, len(self.backoff_s) - 1)])
                attempt += 1

    # -- quota -----------------------------------------------------------------------------------------------
    def check_quota(self) -> None:
        """Raise :class:`QuotaExceeded` (with the reset time) when a window is exhausted. A driver without
        ``usage()`` or a failing call never blocks: the real call is classified instead."""
        usage = getattr(self.driver, "usage", None)
        if not callable(usage):
            return
        try:
            data = self._once("quota check", usage)
        except BackendUnavailable:
            return
        until = quota_wait(
            data if isinstance(data, dict) else None, min_remaining_percent=self.min_remaining_percent
        )
        if until is not None and until > time.time():
            raise QuotaExceeded(
                f"quota: NotebookLM usage limit reached, waiting until {until_text(until)}", retry_after=until
            )

    def _quota_hit(self, exc: QuotaExceeded, nb_id: str | None) -> QuotaExceeded:
        """Attach the reset time (from ``usage()``) and the notebook that already exists."""
        until = exc.retry_after
        if until is None:
            usage = getattr(self.driver, "usage", None)
            if callable(usage):
                try:
                    data = self._once("quota check", usage)
                    until = quota_reset_guess(data if isinstance(data, dict) else None)
                except BackendUnavailable:
                    until = None
        suffix = f", waiting until {until_text(until)}" if until else ""
        return QuotaExceeded(f"{str(exc).rstrip('.')}{suffix}", retry_after=until, nb_id=nb_id)

    # -- the backend interface ------------------------------------------------------------------------------
    def create(self, url: str, template: str, item_id: str) -> BackendHandle:
        self.check_quota()
        nb = self._call("create notebook", self.driver.create_notebook, f"pkpipe {item_id}".strip())
        try:
            self._call("add source", self.driver.add_url_source, nb, url)
            artifact = self._call(
                "start audio overview", self.driver.start_audio_overview, nb, template, retry=False
            )
        except QuotaExceeded as exc:
            raise self._quota_hit(exc, str(nb)) from None
        return BackendHandle(nb_id=str(nb), job_id=f"nlm-{nb}-{artifact}", ref=str(artifact))

    def resume(self, nb_id: str, template: str) -> BackendHandle:
        """Start the audio overview of a notebook that already holds its source (after a quota wait)."""
        self.check_quota()
        try:
            artifact = self._call(
                "start audio overview", self.driver.start_audio_overview, nb_id, template, retry=False
            )
        except QuotaExceeded as exc:
            raise self._quota_hit(exc, nb_id) from None
        return BackendHandle(nb_id=nb_id, job_id=f"nlm-{nb_id}-{artifact}", ref=str(artifact))

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
