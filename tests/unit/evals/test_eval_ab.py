"""Spec 016 US2 / FR-002 / SC-001: the A/B comparison runner on top of the spec 013 suites.

Two configs (the current pack and a spike overlay) are measured on the same synthetic datasets with a fake
model and judge; code turns both runs into the verdict that decides whether a candidate may be adopted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pkpipe.engine.config_models import Threshold
from pkpipe.engine.registry import load_effective
from pkpipe.evals import ab
from pkpipe.evals.models import EvalRun, Level, Status, SuiteReport
from tests.fakes import FakeModel
from tests.support.evals import IDS, ProfileModel, mini_evals

CASES = [
    {
        "id": f"s{i}",
        "input": {"extract": f"minikv is a key-value store {i}."},
        "expected": {"must_include": ["minikv"]},
    }  # fmt: skip
    for i in range(3)
]
GOOD = "Name the tool in every note."  # an instruction of the spike that the fake model follows


def _overlay(root: Path, instruction: str) -> Path:
    p = root / "instructions/marketing/synthesize.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(instruction, encoding="utf-8")
    return root


def _model(*, good_marker=GOOD, bad_marker=None, winner=("B", "A"), score=5):
    """Subject: the note names the tool only when the prompt carries ``good_marker`` (the spike), unless the
    prompt carries ``bad_marker`` (a regression). Judge: fixed answers."""

    def subject(system, user):
        names = good_marker in user and not (bad_marker and bad_marker in user)
        return {"title": "t", "note": "minikv, a key-value store." if names else "A key-value store."}

    winners = list(winner) * 20

    def judge(system, user):
        if "Which output is better" in user:
            return {"reasoning": "", "winner": winners.pop(0)}
        return {"reasoning": "", "score": score}

    return ProfileModel(default=FakeModel(subject), judge=FakeModel(judge))


@pytest.fixture
def paths(tmp_path):
    return mini_evals(tmp_path / "evals", {"datasets/sessions/marketing/synthesize.jsonl": CASES})


def _snaps(pack_dir, tmp_path, instruction=GOOD, base_instruction="Write the note."):
    base = load_effective(pack_dir, _overlay(tmp_path / "base", base_instruction)).snapshot()
    cand = load_effective(pack_dir, _overlay(tmp_path / "cand", instruction)).snapshot()
    return base, cand


def _compare(base, cand, paths, model, **kw):
    return ab.compare(
        base, cand, paths=paths, model=model, suites=["sessions"], identities=IDS, tool="minikv", **kw
    )


def test_a_candidate_that_fixes_the_checks_and_wins_the_judge_is_better(pack_dir, paths, tmp_path):
    """US2: only a candidate that beats the current component can be adopted."""
    base, cand = _snaps(pack_dir, tmp_path)
    report = _compare(base, cand, paths, _model())
    assert report.verdict == "better" and report.gate_green and report.tool == "minikv"
    check = next(m for m in report.metrics if m.metric == "check_pass_rate")
    assert (check.baseline, check.candidate, check.verdict) == (0.0, 1.0, "better")
    [pw] = report.pairwise
    assert pw.session == "marketing.synthesize" and pw.score == 1.0 and pw.outcomes["candidate"] == 3
    assert report.baseline_version != report.candidate_version and report.suites == ["sessions"]
    assert report.summary().startswith("better (")


def test_a_candidate_worse_than_the_current_component_is_worse_with_the_reasons(pack_dir, paths, tmp_path):
    """US2-AS1: the A/B is worse -> the candidate is rejected (the verdict carries the reasons)."""
    base, cand = _snaps(pack_dir, tmp_path, instruction="Write the note badly.", base_instruction=GOOD)
    report = _compare(base, cand, paths, _model(winner=("A", "B")))  # the judge prefers the baseline
    assert report.verdict == "worse" and not report.gate_green
    assert any(r.startswith("candidate gate: sessions: check_pass_rate") for r in report.reasons)
    assert any("check_pass_rate 1.0 -> 0.0" in r for r in report.reasons)
    assert any(r.startswith("pairwise marketing.synthesize: score 0.0") for r in report.reasons)


def test_no_difference_is_equal_and_cannot_be_adopted(pack_dir, paths, tmp_path):
    """Equal is not a win: the candidate must beat the current component."""
    base, cand = _snaps(pack_dir, tmp_path, instruction=GOOD + " ", base_instruction=GOOD)
    report = _compare(base, cand, paths, _model(winner=("A", "A")))  # inconsistent -> tie
    assert report.verdict == "equal" and report.gate_green
    assert report.pairwise[0].score == 0.5 and report.reasons == [
        "no gated metric improved and the judge sees no difference"
    ]
    from pkpipe.domain.tool_candidate import AbEvidence, ToolCandidate, adoption_error

    row = ToolCandidate(id="minikv", tool="minikv", evaluation_note="n.md", component="App DB",
                        benefit="b", effort="S", status="trial")  # fmt: skip
    evidence = AbEvidence(tool="minikv", verdict=report.verdict, gate_green=report.gate_green)
    assert "needs 'better'" in adoption_error(row, adr_exists=True, ab=evidence)


def test_an_unusable_judge_makes_the_comparison_inconclusive_never_a_win(pack_dir, paths, tmp_path):
    base, cand = _snaps(pack_dir, tmp_path)
    same = {
        "default": "http://h|m",
        "judge": "http://h|m",
    }  # the judge is the model under test (FR-002 of 013)
    report = ab.compare(
        base, cand, paths=paths, model=_model(), suites=["sessions"], identities=same, tool="minikv"
    )
    assert report.verdict == "inconclusive" and not report.gate_green
    assert any("not measured" in r for r in report.reasons) and any(
        "judge unavailable" in r for r in report.reasons
    )


def test_a_threshold_set_by_the_spike_config_decides_its_gate(pack_dir, paths, tmp_path):
    """The candidate's own gates (evals.json of the spike) decide whether its gate is green."""
    base, cand_snap = _snaps(pack_dir, tmp_path)
    (tmp_path / "cand/evals.json").write_text(
        json.dumps(
            {"suites": {"sessions": {"gate": {"check_pass_rate": {"min": 1.0}, "judge_mean": {"min": 4.9}}}}}
        ),
        encoding="utf-8",
    )
    cand = load_effective(pack_dir, tmp_path / "cand").snapshot()
    report = _compare(base, cand, paths, _model(score=4))  # judge mean 4 < 4.9
    assert report.verdict == "worse" and not report.gate_green
    assert any("judge_mean" in f for f in report.failures)


def test_both_sides_share_one_recording_and_replay_without_a_model(pack_dir, tmp_path):
    """013 cassettes: a recorded A/B replays offline to the same verdict (CI needs no model)."""
    cassettes = tmp_path / "cassettes"
    from pkpipe.evals.datasets import EvalPaths

    root = tmp_path / "evals"
    mini_evals(root, {"datasets/sessions/marketing/synthesize.jsonl": CASES})
    paths = EvalPaths.create(root, None, cassettes)
    base, cand = _snaps(pack_dir, tmp_path)
    recorded = _compare(base, cand, paths, _model(), mode="record")
    assert recorded.verdict == "better" and (cassettes / "sessions.json").is_file()
    replayed = _compare(base, cand, paths, None, mode="replay")
    assert replayed.verdict == "better" and replayed.pairwise[0].score == recorded.pairwise[0].score
    assert [m.model_dump() for m in replayed.metrics] == [m.model_dump() for m in recorded.metrics]


# -- decide(): the rules on hand-made runs -----------------------------------------------------------------
def _run(*suites: SuiteReport) -> EvalRun:
    return EvalRun(run_id="r", config_version="v", mode="live", suites=list(suites))


def _suite(name="sessions", status=Status.PASSED, **metrics) -> SuiteReport:
    return SuiteReport(suite=name, level=Level.STEP, status=status, metrics=metrics)


GATES = {"sessions": {"judge_mean": Threshold(min=4.0), "canary_leaks": Threshold(max=0)}}


def _decide(base, cand, pairwise=(), errors=(), **kw):
    return ab.decide(
        ab.AbInputs(
            baseline=_run(base),
            candidate=_run(cand),
            candidate_gates=GATES,
            pairwise=list(pairwise),
            pairwise_errors=list(errors),
        ),
        min_score=0.4,
        **kw,
    )


def _pw(*outcomes):
    from pkpipe.evals.judge import pairwise_score

    return {"session": "s.x", "score": pairwise_score(outcomes),
            "outcomes": [{"case_id": str(i), "outcome": o} for i, o in enumerate(outcomes)]}  # fmt: skip


def test_a_better_metric_alone_wins_and_a_lower_is_better_metric_is_read_the_right_way():
    report = _decide(_suite(judge_mean=4.2, canary_leaks=1), _suite(judge_mean=4.2, canary_leaks=0))
    # canary_leaks has a max gate: 1 -> 0 is an improvement even though the number fell
    [m] = [m for m in report.metrics if m.metric == "canary_leaks"]
    assert m.direction == "lower" and m.verdict == "better" and report.verdict == "better"


def test_a_regression_beyond_the_tolerance_makes_it_worse_even_with_a_gain_elsewhere():
    report = _decide(_suite(judge_mean=4.5, canary_leaks=0), _suite(judge_mean=4.2, canary_leaks=0))
    assert report.verdict == "worse" and report.reasons == ["sessions.judge_mean 4.5 -> 4.2"]
    small = _decide(_suite(judge_mean=4.5), _suite(judge_mean=4.495), tolerance=0.01)
    assert small.verdict == "equal"  # inside the tolerance


def test_a_security_metric_regression_blocks_adoption():
    """A tool must never trade security for quality: a canary leak on the candidate is worse."""
    report = _decide(_suite(judge_mean=4.0, canary_leaks=0), _suite(judge_mean=4.9, canary_leaks=1))
    assert report.verdict == "worse" and any("canary_leaks" in r for r in report.reasons)


def test_the_judge_preference_alone_wins_only_beyond_the_margin():
    flat = _suite(judge_mean=4.5, canary_leaks=0)
    assert _decide(flat, flat, [_pw("candidate", "candidate", "tie")]).verdict == "better"
    slight = _pw("candidate", *["tie"] * 7)  # 0.5625: below 0.5 + margin
    assert _decide(flat, flat, [slight]).verdict == "equal"
    assert _decide(flat, flat, [_pw("baseline", "baseline", "candidate")]).verdict == "worse"


def test_an_unmeasured_side_is_inconclusive():
    flat = _suite(judge_mean=4.5, canary_leaks=0)
    skipped = _suite(status=Status.SKIPPED)
    skipped.reason = "judge unavailable"
    assert _decide(flat, skipped).verdict == "inconclusive"
    assert _decide(skipped, flat).verdict == "inconclusive"
    assert _decide(flat, flat, errors=["pairwise s.x: judge unavailable"]).verdict == "inconclusive"
    assert (
        _decide(flat, SuiteReport(suite="other", level=Level.STEP, status=Status.PASSED)).verdict
        == "inconclusive"
    )


def test_a_failing_candidate_gate_is_never_better():
    cand = _suite(judge_mean=4.9, canary_leaks=0)
    cand.status = Status.FAILED  # e.g. another gated metric is red
    assert _decide(_suite(judge_mean=4.0, canary_leaks=0), cand).verdict == "worse"
