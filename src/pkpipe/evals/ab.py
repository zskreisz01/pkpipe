"""A/B comparison of two configs on the eval suites (spec 016 US2, FR-002; builds on spec 013).

A spike of a tool candidate lives in a second config: the same pack with the tool behind its adapter (an
overlay directory or a pack copy). :func:`compare` runs the same suites on both snapshots, adds the pairwise
judge comparison of every session whose prompt-relevant config differs, and :func:`decide` turns both
runs into one verdict (constitution V: code decides, not the model):

* ``better``       the candidate's gate is green (its own gates and the current config's, the stricter
                   threshold of each metric: a spike config cannot lower a gate), no gated metric is
                   worse than the baseline's beyond ``tolerance``, no pairwise comparison is lost, and at
                   least one gated metric improved or the judge prefers the candidate (pairwise score >= 0.5
                   + ``margin``)
* ``worse``        the candidate's gate is red, a gated metric dropped or a pairwise comparison was lost
* ``equal``        measured and not worse, but nothing beats the current component
* ``inconclusive`` a suite could not be measured on one side (skipped, error): never a win

Only ``better`` allows adoption (:func:`pkpipe.domain.tool_candidate.adoption_error`); ``worse`` is a reason
to reject the candidate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from pkpipe.engine.config_models import Threshold
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.evals.datasets import EvalPaths
from pkpipe.evals.judge import JudgeUnavailable
from pkpipe.evals.models import EvalRun, Status, SuiteReport, _fmt
from pkpipe.evals.runner import EvalRunner, evals_config
from pkpipe.evals.scoring import check_gate
from pkpipe.evals.suites import SessionsSuite, run_pairwise, session_targets
from pkpipe.ports import ModelError, ModelPort

Verdict = Literal["better", "equal", "worse", "inconclusive"]
#: Suites compared when none are named: the ones a swapped component can change.
DEFAULT_SUITES = ("sessions", "triage", "flows", "security")
DEFAULT_TOLERANCE = 0.01
DEFAULT_MARGIN = 0.1


class MetricDelta(BaseModel):
    suite: str
    metric: str
    direction: Literal["higher", "lower"]
    baseline: float
    candidate: float
    verdict: Literal["better", "worse", "equal"]


class PairwiseResult(BaseModel):
    session: str
    score: float
    outcomes: dict[str, int] = Field(default_factory=dict)


class AbReport(BaseModel):
    """The evidence for an adoption decision: stored as JSON, checked by ``pkpipe candidates set``."""

    tool: str = ""
    verdict: Verdict
    gate_green: bool
    baseline_version: str
    candidate_version: str
    suites: list[str]
    reasons: list[str] = Field(default_factory=list)
    metrics: list[MetricDelta] = Field(default_factory=list)
    pairwise: list[PairwiseResult] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    tolerance: float = DEFAULT_TOLERANCE
    margin: float = DEFAULT_MARGIN
    created: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def summary(self) -> str:
        wins = sum(m.verdict == "better" for m in self.metrics)
        losses = sum(m.verdict == "worse" for m in self.metrics)
        pair = ", ".join(f"{p.session} {p.score}" for p in self.pairwise) or "no pairwise"
        gate = "gate green" if self.gate_green else "gate red"
        return f"{self.verdict} ({wins} metrics better, {losses} worse, {pair}, {gate})"

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2) + "\n"


@dataclass
class AbInputs:
    """The two measured sides of a comparison."""

    baseline: EvalRun
    candidate: EvalRun
    candidate_gates: dict[str, dict[str, Threshold]]
    #: The current config's gates. A spike config may not lower them (constitution IX: thresholds only move
    #: upwards): the candidate is measured against the stricter of both.
    baseline_gates: dict[str, dict[str, Threshold]] = field(default_factory=dict)
    pairwise: list[dict[str, Any]] = field(default_factory=list)
    pairwise_errors: list[str] = field(default_factory=list)
    baseline_version: str = ""
    candidate_version: str = ""


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def metric_deltas(
    baseline: SuiteReport, candidate: SuiteReport, gate: dict[str, Threshold], tolerance: float
) -> list[MetricDelta]:
    """Gated metrics of a suite on both sides. A ``min`` gate means higher is better, a ``max`` gate lower."""
    out = []
    for metric, threshold in sorted(gate.items()):
        b, c = _number(baseline.metrics.get(metric)), _number(candidate.metrics.get(metric))
        if b is None or c is None:
            continue
        higher = threshold.min is not None
        change = (c - b) if higher else (b - c)  # > 0: the candidate is better
        verdict = "better" if change > tolerance else "worse" if change < -tolerance else "equal"
        out.append(
            MetricDelta(
                suite=candidate.suite,
                metric=metric,
                direction="higher" if higher else "lower",
                baseline=round(b, 4),
                candidate=round(c, 4),
                verdict=verdict,
            )
        )
    return out


def _stricter(a: Threshold, b: Threshold) -> Threshold:
    if a.min is not None and b.min is not None:
        return Threshold(min=max(a.min, b.min))
    if a.max is not None and b.max is not None:
        return Threshold(max=min(a.max, b.max))
    return a  # opposite directions: the current config's gate decides


def strictest_gates(
    baseline: dict[str, dict[str, Threshold]], candidate: dict[str, dict[str, Threshold]]
) -> dict[str, dict[str, Threshold]]:
    """Per suite and metric the stricter threshold of both configs; a metric gated on one side only stays."""
    out: dict[str, dict[str, Threshold]] = {}
    for suite in sorted(set(baseline) | set(candidate)):
        b, c = baseline.get(suite, {}), candidate.get(suite, {})
        out[suite] = {
            m: _stricter(b[m], c[m]) if m in b and m in c else (b.get(m) or c[m])
            for m in sorted(set(b) | set(c))
        }
    return out


def _current_gate_failures(suite: SuiteReport, gate: dict[str, Threshold]) -> list[str]:
    """The current config's gated metrics the candidate misses (a spike that lowered or dropped a gate)."""
    return [
        f"{suite.suite}: {g.metric} = {_fmt(g.value)} (current gate {g.op} {_fmt(g.threshold)})"
        for g in check_gate(suite.metrics, gate)
        if not g.passed
    ]


def decide(
    data: AbInputs, *, tolerance: float = DEFAULT_TOLERANCE, margin: float = DEFAULT_MARGIN, min_score: float
) -> AbReport:
    """The verdict of an A/B (see the module docstring). Pure: no model, no I/O."""
    reasons: list[str] = []
    metrics: list[MetricDelta] = []
    unmeasured: list[str] = []
    base = {s.suite: s for s in data.baseline.suites}
    cand = {s.suite: s for s in data.candidate.suites}
    gates = strictest_gates(data.baseline_gates, data.candidate_gates)
    below_current: list[str] = []  # gates of the current config the candidate's own (lowered) gate let pass
    for name, c in cand.items():
        b = base.get(name)
        if (
            c.status in (Status.SKIPPED, Status.ERROR)
            or b is None
            or b.status in (Status.SKIPPED, Status.ERROR)
        ):
            unmeasured.append(f"{name}: not measured ({c.reason or (b.reason if b else 'missing')})")
            continue
        metrics += metric_deltas(b, c, gates.get(name, {}), tolerance)
        below_current += _current_gate_failures(c, data.baseline_gates.get(name, {}))
    failures = data.candidate.failures()
    seen = {f.split(" = ")[0] for f in failures}  # "suite: metric"
    failures += [f for f in below_current if f.split(" = ")[0] not in seen]
    gate_green = data.candidate.ok and not failures
    pairwise = [
        PairwiseResult(
            session=p["session"],
            score=p["score"],
            outcomes={
                o: sum(1 for x in p["outcomes"] if x["outcome"] == o)
                for o in ("candidate", "tie", "baseline")
            },
        )
        for p in data.pairwise
    ]
    lost = [p for p in pairwise if p.score < min_score]
    won = [p for p in pairwise if p.score >= 0.5 + margin]
    losses = [m for m in metrics if m.verdict == "worse"]
    gains = [m for m in metrics if m.verdict == "better"]
    if unmeasured or data.pairwise_errors:
        reasons += unmeasured + data.pairwise_errors
        verdict: Verdict = "inconclusive"
    elif not gate_green or losses or lost:
        reasons += [f"candidate gate: {f}" for f in failures]
        reasons += [f"{m.suite}.{m.metric} {m.baseline} -> {m.candidate}" for m in losses]
        reasons += [f"pairwise {p.session}: score {p.score} < {min_score}" for p in lost]
        verdict = "worse"
    elif gains or won:
        reasons += [f"{m.suite}.{m.metric} {m.baseline} -> {m.candidate}" for m in gains]
        reasons += [f"pairwise {p.session}: score {p.score}" for p in won]
        verdict = "better"
    else:
        reasons.append("no gated metric improved and the judge sees no difference")
        verdict = "equal"
    return AbReport(
        verdict=verdict,
        gate_green=gate_green,
        baseline_version=data.baseline_version,
        candidate_version=data.candidate_version,
        suites=sorted(cand),
        reasons=reasons,
        metrics=metrics,
        pairwise=pairwise,
        failures=failures,
        tolerance=tolerance,
        margin=margin,
    )


def changed_sessions(baseline: ConfigSnapshot, candidate: ConfigSnapshot, paths: EvalPaths):
    old = SessionsSuite().node_fingerprints(baseline, paths)
    new = SessionsSuite().node_fingerprints(candidate, paths)
    return [t for t in session_targets(paths) if old.get(t.name) != new.get(t.name)]


def compare(
    baseline: ConfigSnapshot,
    candidate: ConfigSnapshot,
    *,
    paths: EvalPaths,
    model: ModelPort | None,
    suites: list[str] | None = None,
    mode: str = "live",
    identities: dict[str, str] | None = None,
    concurrency: int | None = None,
    limit: int | None = None,
    options: dict[str, Any] | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    margin: float = DEFAULT_MARGIN,
    tool: str = "",
) -> AbReport:
    """Run ``suites`` on both configs with the same model and judge and decide (spec 016 FR-002).

    The candidate's gate is green when it passes its own gates (``evals.json`` of its config) and the
    current config's gates (a spike cannot lower a threshold, constitution IX); the
    baseline is measured with the same datasets, so the metrics are comparable. With a cassette ``mode``
    both sides share one recording per suite (a request is recorded once)."""
    names = list(suites or DEFAULT_SUITES)

    def runner(snap: ConfigSnapshot) -> EvalRunner:
        return EvalRunner(
            snap,
            paths=paths,
            model=model,
            mode=mode,
            identities=identities,
            concurrency=concurrency,
            limit=limit,
            options=options,
        )

    base_runner, cand_runner = runner(baseline), runner(candidate)
    base_run = base_runner.run(names)
    cand_runner.cassettes = base_runner.cassettes  # one recording per suite, shared by both sides
    cand_run = cand_runner.run(names)
    pairwise: list[dict[str, Any]] = []
    errors: list[str] = []
    changed = changed_sessions(baseline, candidate, paths) if "sessions" in names else []
    if changed:
        sessions_model = base_runner.model_for("sessions")
        judge, error = base_runner.judge_for(sessions_model)
        for target in changed:
            if judge is None:
                errors.append(f"pairwise {target.name}: judge unavailable ({error})")
                continue
            try:
                pairwise.append(
                    run_pairwise(
                        baseline,
                        candidate,
                        target,
                        paths=paths,
                        model=sessions_model,
                        judge=judge,
                        limit=limit,
                        concurrency=base_runner.concurrency,
                    )
                )
            except KeyError:
                continue  # the session exists on one side only: the absolute eval decides
            except (JudgeUnavailable, ModelError) as exc:
                errors.append(f"pairwise {target.name}: {type(exc).__name__}: {exc}")
        if mode in ("record", "auto") and "sessions" in base_runner.cassettes:
            base_runner.cassettes["sessions"].save()
    cfg = evals_config(candidate)
    report = decide(
        AbInputs(
            baseline=base_run,
            candidate=cand_run,
            candidate_gates={n: g.gate for n, g in cfg.suites.items()},
            baseline_gates={n: g.gate for n, g in evals_config(baseline).suites.items()},
            pairwise=pairwise,
            pairwise_errors=errors,
            baseline_version=baseline.version,
            candidate_version=candidate.version,
        ),
        tolerance=tolerance,
        margin=margin,
        min_score=cfg.pairwise.min_score,
    )
    report.tool = tool
    return report
