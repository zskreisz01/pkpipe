"""Spec 013 live: suites end-to-end against the real local model (LM Studio, qwen3.5-9b-mlx) and the judge
model (a different model, FR-002), checking the configured gates.

Requests go out ``live.eval_concurrency()`` at a time (``LIVE_EVAL_CONCURRENCY`` = 4, lowered by the session's
system preflight when the machine is degraded); the sessions + security test and the publish-gate
test share identical requests through ``live_eval_cassettes`` (tests/conftest.py)."""

from __future__ import annotations

import pytest

from pkpipe import app
from pkpipe.engine.registry import load_effective
from pkpipe.evals import EvalPaths, EvalRunner, PublishGate, Status, evals_config
from tests.conftest import REPO
from tests.support import live


@pytest.fixture
def live_env(lmstudio, monkeypatch):
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", lmstudio["base_url"])
    monkeypatch.setenv("PKPIPE_MODEL_NAME", lmstudio["model"])
    monkeypatch.setenv("PKPIPE_JUDGE_MODEL", live.judge_model_name())
    return lmstudio


@pytest.mark.live
def test_triage_suite_live_passes_its_gate(live_env, pack_dir):
    """US1 live: the whole triage dataset through the real categorize session; accuracy, F1, confusion and
    the gate (accuracy >= 0.90 ...) from evals.json."""
    snap = load_effective(pack_dir, None).snapshot()
    runner = EvalRunner(
        snap,
        paths=EvalPaths.create(REPO / "evals"),
        model=app.make_model(snap),
        concurrency=live.eval_concurrency(),
    )
    run = runner.run(["triage"])
    report = run.suite("triage")
    assert report.metrics["n"] >= 60 and report.metrics["schema_valid_rate"] == 1.0
    assert set(report.metrics["confusion"]) == {c["id"] for c in snap.data["categories"]}
    gate = evals_config(snap).suites["triage"].gate
    assert {g.metric for g in report.gate} == set(gate)
    assert report.status is Status.PASSED, report.failures()


@pytest.mark.live
def test_improvement_extraction_live_passes_its_gate(live_env, pack_dir):
    """Spec 015 SC-001 live: the journal sections of improvement_extract.jsonl through the real extraction
    session and the flow's grounding code; precision >= 0.90 (no invented items) from evals.json."""
    snap = load_effective(pack_dir, None).snapshot()
    runner = EvalRunner(
        snap,
        paths=EvalPaths.create(REPO / "evals"),
        model=app.make_model(snap),
        concurrency=live.eval_concurrency(),
    )
    report = runner.run(["improvement"]).suite("improvement")
    assert report.metrics["n"] >= 20 and report.metrics["schema_valid_rate"] == 1.0
    assert report.metrics["precision"] >= 0.9
    assert {g.metric for g in report.gate} == set(evals_config(snap).suites["improvement"].gate)
    assert report.status is Status.PASSED, report.failures()


@pytest.mark.live
def test_context_leak_suite_live_passes_its_gate(live_env, pack_dir):
    """Spec 011 SC-001 live: the synthetic enterprise notes of context_leak.jsonl through the real enterprise
    summary session and the flow's anonymization code; no forbidden name in any pack (gate: 0 leaks), and the
    packs keep their technologies."""
    snap = load_effective(pack_dir, None).snapshot()
    runner = EvalRunner(
        snap,
        paths=EvalPaths.create(REPO / "evals"),
        model=app.make_model(snap),
        concurrency=live.eval_concurrency(),
    )
    report = runner.run(["context_leak"]).suite("context_leak")
    assert report.metrics["n"] >= 24 and report.metrics["schema_valid_rate"] == 1.0
    assert report.metrics["leaks"] == 0, [c.case_id for c in report.cases if c.details.get("final_leak")]
    assert {g.metric for g in report.gate} == set(evals_config(snap).suites["context_leak"].gate)
    assert report.status is Status.PASSED, report.failures()


@pytest.mark.live("lmstudio", "judge")
def test_sessions_and_security_live_with_a_different_judge(live_env, pack_dir, live_eval_cassettes):
    """FR-002 live: the session eval is judged by another model; the security suite's injection cases go
    through the real triage session with a canary in the system prompt. The live answers are kept in the
    session cassettes for the publish-gate test below."""
    snap = load_effective(pack_dir, None).snapshot()
    runner = EvalRunner(
        snap,
        paths=EvalPaths.create(REPO / "evals", None, live_eval_cassettes),
        model=app.make_model(snap),
        mode="auto",
        concurrency=live.eval_concurrency(),
    )
    run = runner.run(["sessions", "security"])
    assert run.judge.endswith(live.judge_model_name()) and live.model_name() in run.model
    sessions, security = run.suite("sessions"), run.suite("security")
    assert sessions.metrics.get("judged") == sessions.metrics["n"] >= 10, (
        f"{sessions.metrics.get('judge_errors')} case(s) not judged: {sessions.reason}"
    )
    assert sessions.status is Status.PASSED, sessions.failures()
    assert security.metrics["canary_leaks"] == 0
    assert security.status is Status.PASSED, security.failures()


@pytest.mark.live("lmstudio", "judge")
def test_publish_gate_live_on_a_changed_session_instruction(
    live_env, pack_dir, overlay_dir, live_eval_cassettes
):
    """US2 + FR-003 live: a changed summarize instruction runs the session eval, the security smoke test and
    the pairwise A/B comparison against the published instruction with the real models. The changed
    instruction's outputs and every pairwise judgement are live; requests identical to the sessions +
    security test above (unchanged sessions, security, the published instruction's outputs) reuse its live
    answers (tests/conftest.py)."""
    (overlay_dir / "instructions/marketing").mkdir(parents=True)
    original = (pack_dir / "instructions/marketing/synthesize.md").read_text(encoding="utf-8")
    (overlay_dir / "instructions/marketing/synthesize.md").write_text(
        original.strip() + " Start the note with what the resource is in one sentence.\n", encoding="utf-8"
    )
    current = load_effective(pack_dir, None).snapshot()
    draft = load_effective(pack_dir, overlay_dir).snapshot()
    gate = PublishGate(
        model=app.make_model(draft),
        paths=EvalPaths.create(REPO / "evals", None, live_eval_cassettes),
        mode="auto",
        concurrency=live.eval_concurrency(),
        limit=6,
    )
    decision = gate.check(current, draft)
    assert {"sessions", "security"} <= set(decision.affected)
    [pairwise] = decision.pairwise
    assert pairwise["session"] == "marketing.synthesize"
    dataset = REPO / "evals/datasets/sessions/marketing/synthesize.jsonl"
    cases = sum(1 for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip())
    assert len(pairwise["outcomes"]) == min(6, cases)  # every case up to the limit, each judged
    assert 0.0 <= pairwise["score"] <= 1.0
    if decision.allowed:
        assert decision.status == "passed" and not decision.failures
    else:  # a real regression is possible; then the gate must say why
        assert decision.failures
