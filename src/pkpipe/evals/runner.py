"""Eval runner: runs suites against a pinned config, applies the configured gates, traces everything with
``eval.run_id`` (spec 013 FR-004) and handles cassettes (record / replay) per suite.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from opentelemetry.trace import Status as OtelStatus
from opentelemetry.trace import StatusCode

from pkpipe import telemetry
from pkpipe.engine.config_models import EvalsConfig
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.evals.cassette import CassetteModel
from pkpipe.evals.datasets import DatasetError, EvalPaths
from pkpipe.evals.judge import Judge, JudgeUnavailable
from pkpipe.evals.models import EvalRun, Level, Status, SuiteReport
from pkpipe.evals.scoring import check_gate
from pkpipe.evals.suites import SUITES, SuiteContext
from pkpipe.ports import ModelPort
from pkpipe.telemetry import attrs

MODES = ("live", "replay", "record", "auto")


def evals_config(snapshot: ConfigSnapshot) -> EvalsConfig:
    return EvalsConfig.model_validate(snapshot.data.get("evals") or {})


def llm_profiles(snapshot: ConfigSnapshot) -> set[str]:
    """Model profiles used by LLM steps (the models under test)."""
    used = set()
    for flow in snapshot.flows().values():
        for node in flow.nodes:
            if node.type in ("llm", "llm_tools"):
                used.add(node.config.get("profile", "default"))
    return used


def profile_identities(snapshot: ConfigSnapshot, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """profile -> ``base_url|model`` as resolved from the environment (unresolvable profiles are left out)."""
    import os

    from pkpipe.adapters.model_openai import ProfileError, resolve_profile
    from pkpipe.engine.config_models import ModelsConfig

    models = ModelsConfig.model_validate(snapshot.data.get("models") or {})
    env = os.environ if environ is None else environ
    out = {}
    for name in models.root:
        try:
            p = resolve_profile(name, models, env)
        except ProfileError:
            continue
        out[name] = f"{p.base_url}|{p.model}"
    return out


class EvalRunner:
    """Runs suites on one config snapshot.

    ``model`` is the real ``ModelPort`` (it serves every profile, judge included); it may be ``None`` in
    ``replay`` mode, where only cassettes answer and nothing touches the network.
    """

    def __init__(
        self,
        snapshot: ConfigSnapshot,
        *,
        paths: EvalPaths | None = None,
        model: ModelPort | None = None,
        mode: str = "live",
        identities: dict[str, str] | None = None,
        workers: int = 1,
        limit: int | None = None,
        options: dict[str, Any] | None = None,
    ):
        if mode not in MODES:
            raise ValueError(f"unknown mode '{mode}' (use one of {', '.join(MODES)})")
        if mode in ("record", "auto") and model is None:
            raise ValueError(f"mode '{mode}' needs a model")
        self.snapshot = snapshot
        self.paths = paths or EvalPaths.create()
        self.model = model
        self.mode = mode
        self.identities = identities if identities is not None else profile_identities(snapshot)
        self.workers = workers
        self.limit = limit
        self.options = options or {}
        self.config = evals_config(snapshot)
        self.cassettes: dict[str, CassetteModel] = {}

    # -- model / judge per suite -----------------------------------------------------------------------------
    def model_for(self, suite: str) -> ModelPort | None:
        if self.mode == "live":
            return self.model
        if suite not in self.cassettes:
            self.cassettes[suite] = CassetteModel(
                self.paths.cassette(suite),
                self.mode,  # type: ignore[arg-type]
                None if self.mode == "replay" else self.model,
                identities=self.identities,
            )
        return self.cassettes[suite]

    def judge_for(self, model: ModelPort | None) -> tuple[Judge | None, str | None]:
        profile = self.config.judge_profile
        if model is None:
            return None, "no model"
        if profile not in (self.snapshot.data.get("models") or {}):
            return None, f"judge profile '{profile}' is not in models.json"
        identity = self.identities.get(profile)
        if identity is None:
            return None, f"judge profile '{profile}' cannot be resolved (environment)"
        subjects = [self.identities[p] for p in llm_profiles(self.snapshot) if p in self.identities]
        try:
            return Judge(model, profile, identity=identity, subject_identities=subjects), None
        except JudgeUnavailable as exc:
            return None, str(exc)

    # -- running ---------------------------------------------------------------------------------------------
    def run(self, names: list[str], *, run_id: str | None = None) -> EvalRun:
        run = EvalRun(
            run_id=run_id or str(uuid.uuid4()),
            config_version=self.snapshot.version,
            mode=self.mode,
            model=",".join(sorted({self.identities.get(p, p) for p in llm_profiles(self.snapshot)})) or None,
            judge=self.identities.get(self.config.judge_profile),
        )
        tracer = telemetry.get_telemetry().tracer
        with tracer.start_as_current_span("eval run") as span:
            span.set_attribute(attrs.SPAN_KIND, "eval")
            span.set_attribute(attrs.EVAL_RUN_ID, run.run_id)
            span.set_attribute(attrs.CONFIG_VERSION, self.snapshot.version)
            span.set_attribute("eval.mode", self.mode)
            for name in names:
                run.suites.append(self._run_suite(run.run_id, name))
            span.set_attribute("eval.passed", run.ok)
            if not run.ok:
                span.set_status(OtelStatus(StatusCode.ERROR))
        run.ended_at = datetime.now(UTC)
        return run

    def _run_suite(self, run_id: str, name: str) -> SuiteReport:
        suite = SUITES[name]
        tracer = telemetry.get_telemetry().tracer

        @contextlib.contextmanager
        def case_span(case_id: str) -> Iterator[Any]:
            with tracer.start_as_current_span(f"eval.case {case_id}") as s:
                s.set_attribute(attrs.SPAN_KIND, "eval")
                s.set_attribute(attrs.EVAL_RUN_ID, run_id)
                s.set_attribute(attrs.EVAL_SUITE, name)
                s.set_attribute(attrs.EVAL_CASE_ID, case_id)
                s.set_attribute(attrs.CONFIG_VERSION, self.snapshot.version)
                yield s

        with tracer.start_as_current_span(f"eval {name}") as span:
            span.set_attribute(attrs.SPAN_KIND, "eval")
            span.set_attribute(attrs.EVAL_RUN_ID, run_id)
            span.set_attribute(attrs.EVAL_SUITE, name)
            span.set_attribute(attrs.EVAL_LEVEL, int(suite.level))
            span.set_attribute(attrs.CONFIG_VERSION, self.snapshot.version)
            model = self.model_for(name) if (suite.uses_model or suite.uses_judge) else None
            judge, judge_error = self.judge_for(model) if suite.uses_judge else (None, None)
            ctx = SuiteContext(
                snapshot=self.snapshot,
                paths=self.paths,
                model=model,
                judge=judge,
                judge_error=judge_error,
                workers=self.workers,
                limit=self.limit,
                case_span=case_span,
                options=self.options,
            )
            try:
                report = suite.run(ctx)
            except DatasetError as exc:
                report = SuiteReport(suite=name, level=suite.level, status=Status.SKIPPED, reason=str(exc))
            except Exception as exc:  # noqa: BLE001 - reported, never a silent pass
                report = SuiteReport(
                    suite=name, level=suite.level, status=Status.ERROR, reason=f"{type(exc).__name__}: {exc}"
                )
            self._finish(name, report)
            span.set_attribute("eval.status", report.status.value)
            for metric in ("accuracy", "macro_f1", "pass_rate", "judge_mean", "detection_rate"):
                if isinstance(report.metrics.get(metric), int | float):
                    span.set_attribute(f"eval.metric.{metric}", float(report.metrics[metric]))
            if not report.ok:
                span.set_status(OtelStatus(StatusCode.ERROR))
        return report

    def _finish(self, name: str, report: SuiteReport) -> None:
        cassette = self.cassettes.get(name)
        if cassette is not None:
            report.notes.append(
                f"cassette {cassette.path.name}: {cassette.hits} replayed, {cassette.recorded} recorded, "
                f"{cassette.misses} missing"
            )
            if self.mode in ("record", "auto"):
                full = self.mode == "record" and self.limit is None and report.status is not Status.ERROR
                cassette.save(prune_to=cassette.used if full else None)
        crashed = [c for c in report.cases if c.details.get("crashed")]
        if report.cases and len(crashed) == len(report.cases) and report.status is Status.PASSED:
            report.status, report.reason = Status.ERROR, f"no case could run: {crashed[0].error}"
        gate = self.config.suites.get(name)
        if gate is None or not gate.gate:
            if report.status is Status.PASSED:
                report.notes.append("no gate configured for this suite")
            return
        report.gate = check_gate(report.metrics, gate.gate)
        per_session = report.metrics.get("per_session")
        if isinstance(per_session, dict) and len(per_session) > 1:
            # each session must pass on its own: a good session must not hide a bad one in the mean
            for session, metrics in per_session.items():
                for check in check_gate(metrics, gate.gate):
                    report.gate.append(check.model_copy(update={"metric": f"{session}.{check.metric}"}))
        if report.status is Status.PASSED and not all(g.passed for g in report.gate):
            report.status = Status.FAILED


def level_of(name: str) -> Level:
    return SUITES[name].level
