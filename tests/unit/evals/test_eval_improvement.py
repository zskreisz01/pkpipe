"""Spec 015 SC-001 and spec 013 FR-001/constitution IX: the improvement extraction eval, the structured
session checks and the publish gate's coverage of the new LLM steps."""

from __future__ import annotations

import json

import pytest

from pkpipe.engine.registry import load_effective
from pkpipe.evals.datasets import EvalPaths, load_jsonl
from pkpipe.evals.gate import affected_suites, unevaluated_steps
from pkpipe.evals.models import Status
from pkpipe.evals.runner import EvalRunner
from pkpipe.evals.scoring import text_checks
from pkpipe.ports import SchemaError
from tests.conftest import REPO
from tests.fakes import FakeModel
from tests.support.evals import IDS, mini_evals

DATASET = "datasets/improvement_extract.jsonl"


def _case(cid, section, items):
    return {"id": cid, "input": {"section": section}, "expected": {"items": items}}


def _want(statement, area="personal", kind="habit", criterion=None):
    return {
        "statement": statement,
        "area": area,
        "type": kind,
        **({"success_criterion": criterion} if criterion else {}),
    }


CASES = [
    _case("one", "- Walk daily.", [_want("Walk daily.")]),
    _case("two", "- Read more.\n- Sleep early.", [_want("Read more.", "learning"), _want("Sleep early.")]),
    _case("none", "Fine day.", []),
]


@pytest.fixture
def snap(pack_dir):
    return load_effective(pack_dir, None).snapshot()


def _model(reply_for):
    def reply(system, user):
        out = reply_for(user)
        if isinstance(out, Exception):
            raise out
        return {"items": out}

    return FakeModel(reply, schema_check=True)


def _it(statement, area="personal", kind="habit", criterion=""):
    return {"statement": statement, "area": area, "type": kind, "success_criterion": criterion}


def _run(snap, tmp_path, model, cases=CASES):
    paths = mini_evals(tmp_path / "evals", {DATASET: cases})
    return (
        EvalRunner(snap, paths=paths, model=model, identities=IDS).run(["improvement"]).suite("improvement")
    )


def test_perfect_extraction_scores_one(snap, tmp_path):
    def reply(user):
        return (
            [_it("Walk daily.")]
            if "Walk" in user
            else ([_it("Read more.", "learning"), _it("Sleep early.")] if "Sleep" in user else [])
        )

    report = _run(snap, tmp_path, _model(reply))
    assert report.status is Status.PASSED, report
    assert report.metrics["precision"] == 1.0 and report.metrics["recall"] == 1.0
    assert report.metrics["area_accuracy"] == 1.0 and report.metrics["empty_clean_rate"] == 1.0
    assert [c.passed for c in report.cases] == [True, True, True]


def test_invented_items_are_dropped_by_the_flow_code_and_counted(snap, tmp_path):
    """SC-001: the measured precision is the backlog's, after code grounding; the raw session is reported
    through invented_rate."""

    def reply(user):
        return (
            [_it("Walk daily.")] + [_it("Climb Everest.", "personal", "experiment")] if "Walk" in user else []
        )

    report = _run(snap, tmp_path, _model(reply), CASES[:1])
    assert report.metrics["precision"] == 1.0 and report.metrics["invented_rate"] == 0.5


def test_a_real_but_wrong_line_lowers_precision_and_a_miss_lowers_recall(snap, tmp_path):
    cases = [_case("c", "Slept badly.\n- Stop coffee after 14:00.", [_want("Stop coffee after 14:00.")])]
    report = _run(snap, tmp_path, _model(lambda user: [_it("Slept badly.")]), cases)
    assert report.metrics["precision"] == 0.0 and report.metrics["recall"] == 0.0
    assert report.status is Status.FAILED  # the pack's gate (precision >= 0.9) is red
    assert not report.cases[0].passed


def test_area_type_and_invented_criterion_are_measured(snap, tmp_path):
    cases = [_case("c", "- Walk daily. Aim: 5 km.", [_want("Walk daily. Aim: 5 km.")])]
    report = _run(
        snap,
        tmp_path,
        _model(lambda u: [_it("Walk daily. Aim: 5 km.", "work", "experiment", "Aim: 5 km.")]),
        cases,
    )
    assert report.metrics["area_accuracy"] == 0.0 and report.metrics["type_accuracy"] == 0.0
    assert report.metrics["criterion_invented_rate"] == 1.0  # the dataset says: no criterion was written


def test_an_unanswerable_case_fails_schema_and_the_run_does_not_crash(snap, tmp_path):
    def reply(user):
        return SchemaError("not json") if "Walk" in user else []

    report = _run(snap, tmp_path, _model(reply), [CASES[0], CASES[2]])
    assert report.metrics["schema_valid_rate"] == 0.5 and report.status is Status.FAILED


def test_without_a_model_or_dataset_the_suite_never_passes(snap, tmp_path):
    paths = mini_evals(tmp_path / "evals", {DATASET: CASES})
    skipped = EvalRunner(snap, paths=paths, model=None, mode="live", identities=IDS).run(["improvement"])
    assert skipped.suite("improvement").status is Status.SKIPPED
    empty = mini_evals(tmp_path / "none")
    missing = EvalRunner(snap, paths=empty, model=FakeModel(), identities=IDS).run(["improvement"])
    assert missing.suite("improvement").status is Status.SKIPPED
    assert "improvement_extract.jsonl" in missing.suite("improvement").reason


def test_a_config_without_the_flow_has_nothing_to_evaluate(tmp_path):
    from tests.support.flows import make_snapshot

    paths = mini_evals(tmp_path / "evals")
    report = (
        EvalRunner(make_snapshot(), paths=paths, model=FakeModel(), identities=IDS)
        .run(["improvement"])
        .suite("improvement")
    )
    assert report.status is Status.PASSED and "nothing to evaluate" in report.notes[0]


# -- the shipped dataset ------------------------------------------------------------------------------------
def test_shipped_dataset_is_well_formed_and_synthetic():
    cases = load_jsonl(REPO / "evals" / DATASET, "improvement")
    assert len(cases) >= 20
    wanted = [i for c in cases for i in c.expected["items"]]
    assert len(wanted) >= 20 and any(not c.expected["items"] for c in cases)
    for c in cases:
        for item in c.expected["items"]:  # every expected statement is verbatim in its section
            assert item["statement"] in c.input["section"], c.id
            assert item["area"] in ("personal", "learning", "work", "system")
            assert item["type"] in ("habit", "experiment", "system-change")
        assert c.source.value == "synthetic"
    assert {"injection", "injection-only"} <= {c.id for c in cases}


# -- structured session checks ------------------------------------------------------------------------------
def test_text_checks_fields_and_item_counts():
    out = {"verdict": "keep", "evidence": [{"quote": "a"}, {"quote": "b"}]}
    assert text_checks(out, {"fields": {"verdict": "keep"}, "min_items": {"evidence": 2}}) == {}
    assert text_checks(out, {"fields": {"verdict": "drop"}}) == {"fields": ["verdict"]}
    assert text_checks(out, {"min_items": {"evidence": 3}}) == {"min_items": ["evidence"]}
    assert text_checks(out, {"max_items": {"evidence": 1}}) == {"max_items": ["evidence"]}
    assert text_checks("text", {"fields": {"verdict": "keep"}}) == {"fields": ["verdict"]}


def test_text_checks_quotes_must_come_from_the_input():
    spec = {"field": "evidence", "key": "quote", "source": "item.journal"}
    inputs = {"item.journal": "## 2026-10-30\nI stretched  today.\nIt felt good."}
    ok = {"evidence": [{"quote": "i stretched today."}, {"quote": "It felt good."}]}
    assert text_checks(ok, {"quotes_from": spec}, inputs) == {}
    bad = {"evidence": [{"quote": "I stretched today."}, {"quote": "I cured my back."}]}
    assert text_checks(bad, {"quotes_from": spec}, inputs) == {"quotes_from": ["I cured my back."]}
    assert text_checks(bad, {"quotes_from": spec}) == {
        "quotes_from": ["I stretched today.", "I cured my back."]
    }


def test_shipped_review_datasets_exist_with_rubrics():
    paths = EvalPaths.create(REPO / "evals")
    for node in ("evidence", "verdict"):
        cases = load_jsonl(paths.find(f"datasets/sessions/review_improvement/{node}.jsonl"), node)
        assert len(cases) >= 8
        assert paths.rubric(f"improvement_{node}")
    verdicts = {
        c.expected["fields"]["verdict"]
        for c in load_jsonl(paths.find("datasets/sessions/review_improvement/verdict.jsonl"), "v")
    }
    assert verdicts == {"keep", "adjust", "drop"}


# -- publish gate (spec 013) --------------------------------------------------------------------------------
def _overlay(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return root


def test_every_shipped_improvement_llm_step_has_an_eval_dataset(snap):
    paths = EvalPaths.create(REPO / "evals")
    assert unevaluated_steps(None, snap, paths) == []


def test_removing_the_extraction_dataset_blocks_publishing_the_step(snap, tmp_path):
    """Constitution IX: without improvement_extract.jsonl the extraction step cannot be measured."""
    paths = mini_evals(tmp_path / "evals")
    names = unevaluated_steps(None, snap, paths)
    assert "improvement_extract.extract" in names
    assert {"review_improvement.evidence", "review_improvement.verdict"} <= set(names)


def test_changing_the_extraction_instruction_affects_the_improvement_suite_and_security(pack_dir, tmp_path):
    paths = EvalPaths.create(REPO / "evals")
    overlay = _overlay(
        tmp_path / "ov", {"instructions/improvement/extract.md": "List improvements verbatim."}
    )
    current, draft = load_effective(pack_dir, None).snapshot(), load_effective(pack_dir, overlay).snapshot()
    affected = affected_suites(current, draft, paths)
    assert "improvement" in affected and "security" in affected
    assert "triage" not in affected
