"""scripts/lmstudio-test-setup.sh (chore test-speed, tests/README.md): keeps the model under test and the
judge loaded with no idle TTL and parallel predictions, and touches no other model. Runs the real script
with a stub ``lms`` that logs its calls (the real LM Studio is never changed by this test)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO

SCRIPT = REPO / "scripts" / "lmstudio-test-setup.sh"


def _model(identifier: str, ttl: int | None, parallel: int, status: str = "idle") -> dict:
    return {
        "identifier": identifier,
        "modelKey": identifier,
        "ttlMs": ttl,
        "parallel": parallel,
        "status": status,
    }


@pytest.fixture
def lms(tmp_path: Path):
    """A stub ``lms``: ``ps --json`` prints ``ps.json``, ``server status`` says running; calls are logged."""
    log, ps = tmp_path / "calls.log", tmp_path / "ps.json"
    stub = tmp_path / "lms"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        'if [[ "$1 $2" == "ps --json" ]]; then cat "' + str(ps) + '"; exit 0; fi\n'
        'if [[ "$1 $2" == "server status" ]]; then echo "The server is running on port 1234."; fi\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    def run(models: list[dict], *args: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        ps.write_text(json.dumps(models), encoding="utf-8")
        log.write_text("", encoding="utf-8")
        env = {**os.environ, "LMS": str(stub)}
        for name in (
            "PKPIPE_MODEL_NAME",
            "PKPIPE_JUDGE_MODEL",
            "PKPIPE_LMSTUDIO_PARALLEL",
            "PKPIPE_LMSTUDIO_CONTEXT",
        ):
            env.pop(name, None)
        result = subprocess.run(
            ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30
        )
        return result, log.read_text(encoding="utf-8").splitlines()

    return run


def _changes(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith(("load", "unload"))]


def test_reloads_only_the_test_models_that_have_a_ttl_or_too_few_parallel_slots(lms):
    """The model under test (1 h TTL) and the judge (parallel 1) are reloaded without TTL and with parallel 4;
    an unrelated loaded model is never unloaded."""
    result, calls = lms(
        [
            _model("qwen3.5-9b-mlx", 3_600_000, 4),
            _model("google/gemma-4-12b", None, 1),
            _model("qwen3-4b", 600_000, 1),
        ]
    )
    assert result.returncode == 0, result.stderr
    assert _changes(calls) == [
        "unload qwen3.5-9b-mlx",
        "load qwen3.5-9b-mlx --identifier qwen3.5-9b-mlx --parallel 4 --yes",
        "unload google/gemma-4-12b",
        "load google/gemma-4-12b --identifier google/gemma-4-12b --parallel 4 --yes",
    ]
    assert not any("qwen3-4b" in c or "--ttl" in c for c in calls)


def test_loads_a_missing_model_and_leaves_a_correct_one_alone(lms):
    result, calls = lms([_model("google/gemma-4-12b", None, 4)])
    assert result.returncode == 0, result.stderr
    assert _changes(calls) == ["load qwen3.5-9b-mlx --identifier qwen3.5-9b-mlx --parallel 4 --yes"]
    assert "google/gemma-4-12b: ok" in result.stdout


def test_check_only_reports_and_a_busy_model_is_not_reloaded_without_force(lms):
    """``--check`` changes nothing and fails; a model that is generating (a running test) is not reloaded."""
    models = [_model("qwen3.5-9b-mlx", 3_600_000, 4, "generating"), _model("google/gemma-4-12b", None, 4)]
    result, calls = lms(models, "--check")
    assert result.returncode == 1 and _changes(calls) == []
    result, calls = lms(models)
    assert result.returncode == 1 and _changes(calls) == [] and "rerun when idle" in result.stderr
    result, calls = lms(models, "--force")
    assert result.returncode == 0 and _changes(calls)[0] == "unload qwen3.5-9b-mlx"
