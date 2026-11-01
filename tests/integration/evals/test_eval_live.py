"""Spec 013 live: suites end-to-end against the real local model (LM Studio, qwen3.5-9b-mlx) and the judge
model (a different model, FR-002), checking the configured gates."""

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
    runner = EvalRunner(snap, paths=EvalPaths.create(REPO / "evals"), model=app.make_model(snap), workers=4)
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
    runner = EvalRunner(snap, paths=EvalPaths.create(REPO / "evals"), model=app.make_model(snap), workers=2)
    report = runner.run(["improvement"]).suite("improvement")
    assert report.metrics["n"] >= 20 and report.metrics["schema_valid_rate"] == 1.0
    assert report.metrics["precision"] >= 0.9
    assert {g.metric for g in report.gate} == set(evals_config(snap).suites["improvement"].gate)
    assert report.status is Status.PASSED, report.failures()


@pytest.mark.live("lmstudio", "judge")
def test_sessions_and_security_live_with_a_different_judge(live_env, pack_dir):
    """FR-002 live: the session eval is judged by another model; the security suite's injection cases go
    through the real triage session with a canary in the system prompt."""
    snap = load_effective(pack_dir, None).snapshot()
    runner = EvalRunner(snap, paths=EvalPaths.create(REPO / "evals"), model=app.make_model(snap), workers=4)
    run = runner.run(["sessions", "security"])
    assert run.judge.endswith(live.judge_model_name()) and live.model_name() in run.model
    sessions, security = run.suite("sessions"), run.suite("security")
    assert sessions.metrics["judged"] == sessions.metrics["n"] >= 10
    assert sessions.status is Status.PASSED, sessions.failures()
    assert security.metrics["canary_leaks"] == 0
    assert security.status is Status.PASSED, security.failures()


@pytest.mark.live("lmstudio", "judge")
def test_publish_gate_live_on_a_changed_session_instruction(live_env, pack_dir, overlay_dir):
    """US2 + FR-003 live: a changed summarize instruction runs the session eval, the security smoke test and
    the pairwise A/B comparison against the published instruction with the real models."""
    (overlay_dir / "instructions/other").mkdir(parents=True)
    original = (pack_dir / "instructions/other/summarize.md").read_text(encoding="utf-8")
    (overlay_dir / "instructions/other/summarize.md").write_text(
        original.strip() + " Start the note with what the resource is in one sentence.\n", encoding="utf-8"
    )
    current = load_effective(pack_dir, None).snapshot()
    draft = load_effective(pack_dir, overlay_dir).snapshot()
    gate = PublishGate(
        model=app.make_model(draft), paths=EvalPaths.create(REPO / "evals"), workers=4, limit=6
    )
    decision = gate.check(current, draft)
    assert {"sessions", "security"} <= set(decision.affected)
    [pairwise] = decision.pairwise
    assert pairwise["session"] == "other_resource.summarize" and len(pairwise["outcomes"]) == 6
    assert 0.0 <= pairwise["score"] <= 1.0
    if decision.allowed:
        assert decision.status == "passed" and not decision.failures
    else:  # a real regression is possible; then the gate must say why
        assert decision.failures
