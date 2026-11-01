"""Publish gate (spec 013 US2, SC-001): a config change is published only when the affected evals pass.

Used by the config UI (spec 014) before it commits a draft::

    gate = PublishGate(model=app.make_model(draft))
    decision = gate.check(current_snapshot, draft_snapshot, override_reason=None)
    if not decision.allowed: ...show decision.failures...

* Affected suites are found by fingerprint: a suite re-runs when anything it depends on (the LLM node's
  config, assets and model profile, the categories, flows, guards, ...) differs between the published and
  the draft config. Any affected LLM suite adds the security suite as a smoke test.
* A changed session instruction (or any prompt-relevant part of a session) is also compared pairwise (A/B,
  FR-003): old and new outputs side by side, judged twice with the order swapped.
* Failing or skipped evals block; the owner may override with a reason, which is recorded on the decision.
* Lowering a gate threshold (or removing one) is refused outright: thresholds only move upwards
  (constitution IX). No override for that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace

from pkpipe.engine.config_models import EvalsConfig
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.evals.datasets import EvalPaths
from pkpipe.evals.judge import JudgeUnavailable
from pkpipe.evals.models import EvalRun
from pkpipe.evals.runner import EvalRunner, evals_config
from pkpipe.evals.suites import SUITES, SessionsSuite, run_pairwise, session_targets
from pkpipe.ports import ModelError, ModelPort
from pkpipe.telemetry import attrs

#: suites a publish may run, in order (production is monitoring only)
GATED_SUITES = ("deterministic", "triage", "sessions", "calibration", "flows", "security")
LLM_SUITES = ("triage", "sessions")


@dataclass
class GateDecision:
    allowed: bool
    status: str  # passed | blocked | overridden | unchanged
    affected: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    threshold_regressions: list[str] = field(default_factory=list)
    pairwise: list[dict[str, Any]] = field(default_factory=list)
    override_reason: str | None = None
    run: EvalRun | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "affected": self.affected,
            "failures": self.failures,
            "threshold_regressions": self.threshold_regressions,
            "pairwise": self.pairwise,
            "override_reason": self.override_reason,
            "eval_run_id": self.run.run_id if self.run else None,
        }


def threshold_regressions(old: EvalsConfig, new: EvalsConfig) -> list[str]:
    """Gates that were removed or loosened from ``old`` to ``new`` (constitution IX)."""
    out = []
    for suite, gate in old.suites.items():
        new_gate = new.suites.get(suite)
        for metric, t in gate.gate.items():
            nt = new_gate.gate.get(metric) if new_gate else None
            if nt is None:
                out.append(f"{suite}.{metric}: gate removed")
            elif t.min is not None and (nt.min is None or nt.min < t.min):
                out.append(f"{suite}.{metric}: min lowered from {t.min} to {nt.min}")
            elif t.max is not None and (nt.max is None or nt.max > t.max):
                out.append(f"{suite}.{metric}: max raised from {t.max} to {nt.max}")
    if new.pairwise.min_score < old.pairwise.min_score:
        out.append(f"pairwise.min_score lowered from {old.pairwise.min_score} to {new.pairwise.min_score}")
    if new.calibration.max_disagreement_rate > old.calibration.max_disagreement_rate:
        out.append("calibration.max_disagreement_rate raised")
    return out


def affected_suites(current: ConfigSnapshot | None, draft: ConfigSnapshot, paths: EvalPaths) -> list[str]:
    """Suites whose inputs differ between the published config and the draft (all of them for a first
    publish). Stricter gates re-run their suite; any affected LLM suite adds the security smoke test."""
    if current is None:
        return list(GATED_SUITES)
    old_gates, new_gates = evals_config(current).suites, evals_config(draft).suites
    affected = []
    for name in GATED_SUITES:
        suite = SUITES[name]
        if name == "sessions":
            changed = SessionsSuite().node_fingerprints(current, paths) != SessionsSuite().node_fingerprints(
                draft, paths
            )
        else:
            changed = suite.fingerprint(current) != suite.fingerprint(draft)
        if changed or old_gates.get(name) != new_gates.get(name):
            affected.append(name)
    if any(n in affected for n in LLM_SUITES) and "security" not in affected:
        affected.append("security")
    return affected


class PublishGate:
    def __init__(
        self,
        *,
        model: ModelPort | None = None,
        paths: EvalPaths | None = None,
        mode: str = "live",
        identities: dict[str, str] | None = None,
        workers: int = 1,
        limit: int | None = None,
    ):
        self.model = model
        self.paths = paths or EvalPaths.create()
        self.mode = mode
        self.identities = identities
        self.workers = workers
        self.limit = limit

    def runner(self, snapshot: ConfigSnapshot) -> EvalRunner:
        return EvalRunner(
            snapshot,
            paths=self.paths,
            model=self.model,
            mode=self.mode,
            identities=self.identities,
            workers=self.workers,
            limit=self.limit,
        )

    def check(
        self, current: ConfigSnapshot | None, draft: ConfigSnapshot, *, override_reason: str | None = None
    ) -> GateDecision:
        regressions = threshold_regressions(evals_config(current), evals_config(draft)) if current else []
        affected = affected_suites(current, draft, self.paths)
        reason = (override_reason or "").strip() or None
        if not affected and not regressions:
            return GateDecision(allowed=True, status="unchanged")
        runner = self.runner(draft)
        run = runner.run(affected) if affected else None
        failures = run.failures() if run else []
        pairwise = self._pairwise(current, draft, runner, failures) if current is not None else []
        decision = GateDecision(
            allowed=False,
            status="blocked",
            affected=affected,
            failures=failures,
            threshold_regressions=regressions,
            pairwise=pairwise,
            override_reason=reason,
            run=run,
        )
        if regressions:
            decision.failures = failures + [f"threshold regression: {r}" for r in regressions]
        elif not failures:
            decision.allowed, decision.status = True, "passed"
        elif reason:
            decision.allowed, decision.status = True, "overridden"
            span = trace.get_current_span()
            span.add_event("eval.gate_override", {attrs.EVAL_RUN_ID: run.run_id if run else ""})
        return decision

    def _pairwise(
        self, current: ConfigSnapshot, draft: ConfigSnapshot, runner: EvalRunner, failures: list[str]
    ) -> list[dict[str, Any]]:
        """FR-003: A/B for every session whose prompt-relevant config changed and exists in both configs."""
        old_fp = SessionsSuite().node_fingerprints(current, self.paths)
        new_fp = SessionsSuite().node_fingerprints(draft, self.paths)
        changed = [t for t in session_targets(self.paths) if old_fp.get(t.name) != new_fp.get(t.name)]
        if not changed:
            return []
        model = runner.model_for("sessions")
        judge, error = runner.judge_for(model)
        min_score = evals_config(draft).pairwise.min_score
        results = []
        for target in changed:
            if judge is None:
                failures.append(f"pairwise {target.name}: judge unavailable ({error})")
                continue
            try:
                res = run_pairwise(
                    current, draft, target, paths=self.paths, model=model, judge=judge, limit=self.limit
                )
            except KeyError:
                continue  # a new session (absent from the published config): its absolute eval decides
            except (JudgeUnavailable, ModelError) as exc:
                failures.append(f"pairwise {target.name}: {type(exc).__name__}: {exc}")
                continue
            res["min_score"] = min_score
            res["passed"] = res["score"] >= min_score
            if not res["passed"]:
                failures.append(f"pairwise {target.name}: score {res['score']} < {min_score}")
            results.append(res)
        if self.mode in ("record", "auto") and "sessions" in runner.cassettes:
            runner.cassettes["sessions"].save()
        return results
