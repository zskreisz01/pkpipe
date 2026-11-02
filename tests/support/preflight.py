"""The system preflight of a live test session (tests/README.md "How live tests adapt").

The first live test that needs a model (``lmstudio``, ``judge``, ``open_notebook``, ``guard``) and whose
services are reachable starts it: one snapshot and decision (``pkpipe.domain.system.decide`` with the pack's
``evals.preflight`` thresholds and ``LIVE_EVAL_CONCURRENCY`` as the ceiling), recorded as a
``system.preflight`` span and written to ``$PKPIPE_TEST_DATA_DIR/system-preflight.json``, then a background
``SystemSampler`` re-samples every ``sample_interval_s`` (``PKPIPE_LIVE_SAMPLE_S`` overrides it).

Each decision is applied at once: :func:`tests.support.live.eval_concurrency` returns its concurrency (the
eval tests pass it to ``EvalRunner`` / ``PublishGate``) and ``PKPIPE_MODEL_TIMEOUT_MULTIPLIER`` scales every
model timeout, also in CLI subprocesses. A decision blocked for a *system* reason (critical memory pressure
or thermal state, almost no memory left) skips the remaining live tests (fails them with
``PKPIPE_REQUIRE_LIVE=1``); an unreachable server or a missing model is left to the service probes in
``tests/support/live.py``. ``PKPIPE_LIVE_PREFLIGHT=0`` turns all of this off.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pkpipe.adapters.model_openai import TIMEOUT_MULTIPLIER_ENV
from pkpipe.domain.system import PreflightDecision
from pkpipe.ports import SystemProbePort
from pkpipe.system import Preflight, SamplerSummary, SystemSampler, run_preflight
from pkpipe.telemetry import Telemetry, TelemetryConfig
from tests.support import live

#: live services whose tests drive a model on this machine and so get the preflight
PREFLIGHT_SERVICES = frozenset({"lmstudio", "judge", "open_notebook", "guard"})
ENV_SWITCH = "PKPIPE_LIVE_PREFLIGHT"
ENV_INTERVAL = "PKPIPE_LIVE_SAMPLE_S"


def _default_probe() -> SystemProbePort:
    from pkpipe.adapters.system_probe import make_system_probe

    return make_system_probe(live.model_base_url())


#: replaced by tests of this module (a fake probe)
probe_factory: Callable[[], SystemProbePort] = _default_probe


class LiveSession:
    """Preflight + sampler + what was applied, for one pytest session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.preflight: Preflight | None = None
        self.sampler: SystemSampler | None = None
        self.summary: SamplerSummary | None = None
        self.last: PreflightDecision | None = None
        self.telemetry: Telemetry | None = None
        self._saved_env: str | None = None
        self._env_set = False
        self.error: str | None = None

    @property
    def started(self) -> bool:
        return self.preflight is not None

    @property
    def decision(self) -> PreflightDecision | None:
        if self.sampler is not None and self.sampler.decision is not None:
            return self.sampler.decision
        if self.last is not None:
            return self.last
        return self.preflight.decision if self.preflight else None

    def _apply(self, decision: PreflightDecision) -> None:
        if not self._env_set:
            self._saved_env, self._env_set = os.environ.get(TIMEOUT_MULTIPLIER_ENV), True
        os.environ[TIMEOUT_MULTIPLIER_ENV] = f"{decision.timeout_multiplier:g}"

    def start(self, settings: Any, required: tuple[str, ...], out_dir: Path | None) -> Preflight:
        with self._lock:
            if self.preflight is not None:
                return self.preflight
            self.telemetry = Telemetry.create(TelemetryConfig.from_env())  # OTLP only if OTEL_* is set
            probe = probe_factory()
            pre = run_preflight(
                probe,
                settings,
                required_models=required,
                max_concurrency=live.LIVE_EVAL_CONCURRENCY,
                telemetry=self.telemetry,
            )
            self.preflight = pre
            self._apply(pre.decision)
            interval = float(os.environ.get(ENV_INTERVAL) or settings.sample_interval_s)
            self.sampler = SystemSampler(
                probe,
                settings,
                required_models=required,
                max_concurrency=live.LIVE_EVAL_CONCURRENCY,
                interval_s=interval,
                telemetry=self.telemetry,
                on_change=self._apply,
                initial=pre,
            ).start()
            if out_dir is not None:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "system-preflight.json").write_text(json.dumps(pre.to_dict(), indent=2), "utf-8")
            return pre

    def stop(self) -> None:
        with self._lock:
            if self.sampler is not None:
                self.summary = self.sampler.stop()
                self.last = self.sampler.decision
                self.sampler = None
            if self._env_set:
                if self._saved_env is None:
                    os.environ.pop(TIMEOUT_MULTIPLIER_ENV, None)
                else:
                    os.environ[TIMEOUT_MULTIPLIER_ENV] = self._saved_env
                self._env_set = False
            if self.telemetry is not None:
                self.telemetry.shutdown()
                self.telemetry = None

    def report_lines(self) -> list[str]:
        if self.preflight is None:
            return [f"preflight failed: {self.error}"] if self.error else []
        first = self.preflight.decision
        lines = [f"preflight: {first.summary()}"]
        current = self.decision
        if current is not None and current != first:
            lines.append(f"last decision: {current.summary()}")
        if self.summary is not None:
            lines.extend(self.summary.lines())
        return lines


#: the session state (a pytest session runs one; ``reset`` between in-process inner runs)
session = LiveSession()


def reset() -> None:
    global session
    session.stop()
    session = LiveSession()


def enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "1") != "0"


def needs_preflight(services: tuple[str, ...]) -> bool:
    return enabled() and bool(set(services or ("lmstudio",)) & PREFLIGHT_SERVICES)


def ensure_started() -> PreflightDecision | None:
    """Start the session preflight once; the current decision (``None`` if the probe itself failed)."""
    if not session.started and session.error is None:
        from pkpipe.engine.registry import load_effective
        from pkpipe.system.preflight import preflight_settings

        try:
            settings = preflight_settings(load_effective(live.PACK_DIR, None).snapshot())
            required = tuple(dict.fromkeys((live.model_name(), live.judge_model_name())))
            out = os.environ.get("PKPIPE_TEST_DATA_DIR")
            session.start(settings, required, Path(out) if out else None)
        except Exception as exc:  # noqa: BLE001 - a broken probe must not stop the live run; it is reported
            session.error = f"{type(exc).__name__}: {exc}"
            return None
    return session.decision


def blocked_reason() -> str | None:
    """Why live tests must not run now (a system-scope block), or None."""
    decision = session.decision
    if decision is None:
        return None
    reasons = decision.blocking("system")
    return "; ".join(r.message for r in reasons) if reasons else None
