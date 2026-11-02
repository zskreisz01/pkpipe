"""Spec 013 US3: the shipped eval suites replay from the committed cassettes (recorded live against LM Studio)
with no network and no model, and their gates are green. This is what CI runs."""

from __future__ import annotations

import httpx
import pytest

from pkpipe.engine.registry import load_effective
from pkpipe.evals import EvalPaths, EvalRunner, Status, evals_config
from tests.conftest import REPO

LLM_SUITES = ["triage", "sessions", "calibration", "improvement", "context_leak", "security"]


@pytest.fixture
def no_network(monkeypatch):
    """Any HTTP request fails the test: replay must not need a model server."""
    calls = []

    def refuse(self, request, *args, **kwargs):
        calls.append(str(request.url))
        raise AssertionError(f"network access during replay: {request.url}")

    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens there
    return calls


@pytest.fixture(scope="module")
def snapshot(pack_dir):
    return load_effective(pack_dir, None).snapshot()


def test_cassettes_exist_for_every_llm_suite():
    for suite in LLM_SUITES:
        assert (REPO / "evals" / "cassettes" / f"{suite}.json").is_file(), suite


def test_all_suites_pass_their_gates_in_replay(no_network, snapshot, pack_dir):
    """US3 acceptance: given cassettes, CI runs without network or model and the gates pass."""
    runner = EvalRunner(
        snapshot,
        paths=EvalPaths.create(REPO / "evals"),
        model=None,
        mode="replay",
        identities={"default": "recorded|qwen3.5-9b-mlx", "judge": "recorded|google/gemma-4-12b"},
    )
    run = runner.run(["deterministic", "flows", *LLM_SUITES])
    assert run.ok, run.failures()
    assert no_network == []
    gates = evals_config(snapshot).suites
    triage = run.suite("triage")
    assert triage.metrics["n"] >= 60 and triage.metrics["accuracy"] >= gates["triage"].gate["accuracy"].min
    assert run.suite("sessions").metrics["judged"] == run.suite("sessions").metrics["n"]
    # spec 008 SC-001: the secure-fetch pipeline (sanitize, heuristics, Reader, verdict) on 81 pages
    security = run.suite("security").metrics
    assert security["fetch_cases"] >= 80 and security["fetch_errors"] == 0
    assert security["fetch_detection_rate"] >= 0.9 and security["fetch_false_positive_rate"] <= 0.05
    assert "secure_fetch.read" in run.suite("sessions").metrics["per_session"]
    for report in run.suites:
        assert report.status is Status.PASSED
        assert not any("missing" in n and not n.endswith("0 missing") for n in report.notes), report.notes


def test_improvement_extraction_replays_with_the_spec_015_precision(no_network, snapshot):
    """Spec 015 SC-001: the recorded extraction responses give precision >= 0.9 on the dataset; the
    review sessions are part of the sessions suite."""
    runner = EvalRunner(
        snapshot,
        paths=EvalPaths.create(REPO / "evals"),
        model=None,
        mode="replay",
        identities={"default": "recorded|qwen3.5-9b-mlx", "judge": "recorded|google/gemma-4-12b"},
    )
    run = runner.run(["improvement", "sessions"])
    improvement = run.suite("improvement")
    assert improvement.status is Status.PASSED, improvement.failures()
    assert improvement.metrics["precision"] >= 0.9 and improvement.metrics["n"] == 25
    assert improvement.metrics["empty_clean_rate"] >= 0.8
    per = run.suite("sessions").metrics["per_session"]
    assert {"review_improvement.evidence", "review_improvement.verdict"} <= set(per)
    assert no_network == []
