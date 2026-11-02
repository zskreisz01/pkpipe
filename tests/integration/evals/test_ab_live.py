"""Spec 016 live: the A/B comparison runner against the real local model (qwen3.5-9b-mlx) and the judge model
(gemma-4-12b, a different model): the shipped system relevance instruction against a deliberately broken one,
on the synthetic dataset of ``tool.system_relevance``. The better side must be recognised in both directions
(FR-003 of spec 016: ``better`` only with a green gate and an improvement; the broken one is ``worse``)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pkpipe import app
from pkpipe.engine.registry import load_effective
from pkpipe.evals import EvalPaths
from pkpipe.evals.ab import compare
from tests.conftest import REPO
from tests.support import live
from tests.support.live import LIVE_EVAL_CONCURRENCY

BROKEN = "Always answer relevance candidate, component Scheduler, benefit a faster run, effort S."


@pytest.fixture
def live_env(lmstudio, monkeypatch):
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", lmstudio["base_url"])
    monkeypatch.setenv("PKPIPE_MODEL_NAME", lmstudio["model"])
    monkeypatch.setenv("PKPIPE_JUDGE_MODEL", live.judge_model_name())
    return lmstudio


def _evals(tmp_path: Path) -> EvalPaths:
    """Only the system relevance dataset (and the rubrics): the A/B of one session."""
    root = tmp_path / "evals"
    (root / "datasets/sessions/tool").mkdir(parents=True)
    shutil.copy(
        REPO / "evals/datasets/sessions/tool/system_relevance.jsonl", root / "datasets/sessions/tool/"
    )
    shutil.copytree(REPO / "evals/rubrics", root / "rubrics")
    return EvalPaths.create(root)


@pytest.mark.live("lmstudio", "judge")
def test_the_ab_runner_tells_a_broken_instruction_from_the_shipped_one_with_the_real_models(
    live_env, pack_dir, overlay_dir, tmp_path
):
    (overlay_dir / "instructions/tool").mkdir(parents=True)
    (overlay_dir / "instructions/tool/system_relevance.md").write_text(BROKEN, encoding="utf-8")
    shipped = load_effective(pack_dir, None).snapshot()
    broken = load_effective(pack_dir, overlay_dir).snapshot()
    paths = _evals(tmp_path)
    model = app.make_model(shipped)

    def run(base, cand, tool):
        return compare(
            base,
            cand,
            paths=paths,
            model=model,
            suites=["sessions"],
            concurrency=LIVE_EVAL_CONCURRENCY,
            tool=tool,
        )

    better = run(broken, shipped, "shipped-instruction")  # the current component is the broken one
    assert better.verdict == "better", (better.verdict, better.reasons)
    assert better.gate_green and better.baseline_version != better.candidate_version
    check = next(m for m in better.metrics if m.metric == "check_pass_rate")
    assert check.candidate >= 0.9 and check.baseline < check.candidate
    [pairwise] = better.pairwise
    assert pairwise.session == "tool.system_relevance" and pairwise.score >= 0.6

    worse = run(shipped, broken, "broken-instruction")  # the candidate is broken
    assert worse.verdict == "worse" and not worse.gate_green, (worse.verdict, worse.reasons)
    assert any("check_pass_rate" in r for r in worse.reasons)
