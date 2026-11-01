"""Spec 013 FR-004 (JSON + Markdown reports, eval.run_id on every span) and US3 (cassettes via the runner)."""

from __future__ import annotations

import json

import pytest

from pkpipe.engine.registry import load_effective
from pkpipe.evals.models import Status
from pkpipe.evals.report import to_json, to_markdown, write_reports
from pkpipe.evals.runner import EvalRunner, profile_identities
from pkpipe.ports import ModelUnavailable
from pkpipe.telemetry import attrs
from tests.fakes import FakeModel
from tests.support.evals import IDS, mini_evals, triage_case

CASES = [triage_case("a", "zz-cli", "tool"), triage_case("b", "zz-ad", "marketing")]


def _reply(system, user):
    cat = "tool" if "zz-cli" in user else "marketing"
    return {"category": cat, "confidence": 0.9, "reason": "r"}


@pytest.fixture
def snap(pack_dir):
    return load_effective(pack_dir, None).snapshot()


def test_every_eval_span_carries_the_run_id(tmp_path, snap, otel):
    """FR-004: run, suite and case spans carry eval.run_id (and the config version)."""
    paths = mini_evals(tmp_path, {"datasets/triage.jsonl": CASES})
    run = EvalRunner(snap, paths=paths, model=FakeModel(_reply), identities=IDS).run(["triage"])
    spans = otel.spans("eval")
    names = {s.name for s in spans}
    assert {"eval run", "eval triage", "eval.case a", "eval.case b"} <= names
    for s in spans:
        assert s.attributes[attrs.EVAL_RUN_ID] == run.run_id
        assert s.attributes[attrs.CONFIG_VERSION] == snap.version
    suite = next(s for s in spans if s.name == "eval triage")
    assert suite.attributes[attrs.EVAL_LEVEL] == 2 and suite.attributes["eval.status"] == "passed"
    assert suite.attributes["eval.metric.accuracy"] == 1.0
    case = next(s for s in spans if s.name == "eval.case a")
    assert case.parent.span_id == suite.context.span_id and case.attributes[attrs.EVAL_CASE_ID] == "a"


def test_reports_as_json_and_markdown(tmp_path, snap):
    """FR-004 / US1: the report shows accuracy, per-category F1, the confusion matrix and the gate."""
    paths = mini_evals(tmp_path, {"datasets/triage.jsonl": CASES})
    run = EvalRunner(snap, paths=paths, model=FakeModel(_reply), identities=IDS).run(["triage"])
    md = to_markdown(run)
    assert "Confusion matrix" in md and "| tool | 1.0 | 1.0 | 1.0 | 1 |" in md
    assert "| accuracy | 1 | >= 0.9 | pass |" in md and "**PASSED**" in md
    data = json.loads(to_json(run))
    assert data["ok"] is True and data["run_id"] == run.run_id
    assert data["suites"][0]["metrics"]["confusion"]["tool"]["tool"] == 1
    js, mdp = write_reports(run, tmp_path / "out")
    assert js.read_text() == to_json(run) and mdp.read_text() == md and run.run_id[:8] in js.name


def test_record_then_replay_through_the_runner_without_a_model(tmp_path, snap):
    """US3: a recorded suite replays with model=None (nothing could reach the network)."""
    paths = mini_evals(tmp_path, {"datasets/triage.jsonl": CASES})
    recorded = EvalRunner(snap, paths=paths, model=FakeModel(_reply), mode="record", identities=IDS).run(
        ["triage"]
    )
    assert recorded.ok and paths.cassette("triage").is_file()
    replayed = EvalRunner(snap, paths=paths, model=None, mode="replay", identities=IDS).run(["triage"])
    assert replayed.ok and replayed.suite("triage").metrics == recorded.suite("triage").metrics
    assert any("2 replayed, 0 recorded, 0 missing" in n for n in replayed.suite("triage").notes)


def test_replay_of_a_changed_prompt_is_an_error_not_a_pass(tmp_path, snap, pack_dir, overlay_dir):
    paths = mini_evals(tmp_path, {"datasets/triage.jsonl": CASES})
    EvalRunner(snap, paths=paths, model=FakeModel(_reply), mode="record", identities=IDS).run(["triage"])
    (overlay_dir / "instructions/triage").mkdir(parents=True)
    (overlay_dir / "instructions/triage/categorize.md").write_text("A different instruction.")
    changed = load_effective(pack_dir, overlay_dir).snapshot()
    run = EvalRunner(changed, paths=paths, model=None, mode="replay", identities=IDS).run(["triage"])
    report = run.suite("triage")
    assert report.status is Status.ERROR and "CassetteMiss" in report.reason and not run.ok


def test_unreachable_model_is_an_error(tmp_path, snap):
    paths = mini_evals(tmp_path, {"datasets/triage.jsonl": CASES})
    model = FakeModel([ModelUnavailable("down"), ModelUnavailable("down")])
    report = EvalRunner(snap, paths=paths, model=model, identities=IDS).run(["triage"]).suite("triage")
    assert report.status is Status.ERROR and "ModelUnavailable" in report.reason


def test_parallel_workers_keep_case_order(tmp_path, snap):
    rows = [
        triage_case(f"c{i}", "zz-cli" if i % 2 else "zz-ad", "tool" if i % 2 else "marketing")
        for i in range(12)
    ]
    paths = mini_evals(tmp_path, {"datasets/triage.jsonl": rows})
    run = EvalRunner(snap, paths=paths, model=FakeModel(_reply), identities=IDS, workers=4).run(["triage"])
    assert [c.case_id for c in run.suite("triage").cases] == [r["id"] for r in rows]
    assert run.suite("triage").metrics["accuracy"] == 1.0


def test_limit_takes_the_first_cases(tmp_path, snap):
    paths = mini_evals(tmp_path, {"datasets/triage.jsonl": CASES})
    run = EvalRunner(snap, paths=paths, model=FakeModel(_reply), identities=IDS, limit=1).run(["triage"])
    assert run.suite("triage").metrics["n"] == 1


def test_profile_identities_resolve_from_the_environment(snap):
    ids = profile_identities(snap, {"PKPIPE_MODEL_NAME": "m1", "PKPIPE_JUDGE_MODEL": "j1"})
    assert ids["default"].endswith("|m1") and ids["judge"].endswith("|j1")
    same = profile_identities(snap, {"PKPIPE_MODEL_NAME": "m1", "PKPIPE_JUDGE_MODEL": "m1"})
    assert same["default"] == same["judge"]


def test_modes_are_validated(snap):
    with pytest.raises(ValueError):
        EvalRunner(snap, mode="bogus")
    with pytest.raises(ValueError):
        EvalRunner(snap, mode="record")  # nothing to record from
