"""Spec 013 US1 / US3 end to end: ``pkpipe eval`` as a subprocess, replaying cassettes without a model."""

from __future__ import annotations

import json

import pytest

from tests.e2e.test_cli_validate import UV, pkpipe

pytestmark = pytest.mark.skipif(UV is None, reason="uv not installed")
NO_MODEL = {"PKPIPE_MODEL_BASE_URL": "http://127.0.0.1:9/v1"}  # nothing listens there


def test_eval_triage_replay_reports_accuracy_f1_and_confusion(tmp_path):
    """US1: `pkpipe eval --suite triage` reports accuracy, per-category F1 and a confusion matrix (US3: from
    cassettes, no model reachable)."""
    out = tmp_path / "reports"
    r = pkpipe("eval", "--suite", "triage", "--cassette", "replay", "--out", str(out), env_extra=NO_MODEL)
    assert r.returncode == 0, r.stderr
    assert "Confusion matrix" in r.stdout and "| Category | Precision | Recall | F1 | Support |" in r.stdout
    [js] = list(out.glob("eval-*.json"))
    [md] = list(out.glob("eval-*.md"))
    data = json.loads(js.read_text())
    triage = data["suites"][0]
    assert data["ok"] and triage["status"] == "passed" and triage["metrics"]["accuracy"] >= 0.85
    assert set(triage["metrics"]["per_category"]) == {"learning-material", "tool", "marketing", "misc"}
    assert md.read_text().startswith(f"# Eval run {data['run_id']}")


def test_eval_json_output_and_code_only_suites(tmp_path):
    r = pkpipe("eval", "--suite", "deterministic,flows", "--json", "--out", str(tmp_path), env_extra=NO_MODEL)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert [s["suite"] for s in data["suites"]] == ["deterministic", "flows"] and data["ok"]


def test_changed_prompt_without_recording_fails_the_gate(tmp_path, overlay_dir):
    """US3: a prompt change is never silently 'green' in CI: the replay misses and the command exits 1."""
    (overlay_dir / "instructions/triage").mkdir(parents=True)
    (overlay_dir / "instructions/triage/categorize.md").write_text("Choose a category id.\n")
    r = pkpipe(
        "eval",
        "--suite",
        "triage",
        "--cassette",
        "replay",
        "--overlay",
        str(overlay_dir),
        "--out",
        str(tmp_path),
        env_extra=NO_MODEL,
    )
    assert r.returncode == 1 and "no recording" in r.stdout + r.stderr
    assert "gate: triage: error" in r.stderr


def test_production_suite_alerts_from_a_run_db(tmp_path):
    from pkpipe.adapters.state_sqlite import SqliteRunStore

    db = tmp_path / "app.db"
    store = SqliteRunStore(db)
    for i, status in enumerate(["failed", "failed", "done"]):
        store.create_run(
            {
                "run_id": f"r{i}",
                "flow_id": "triage",
                "config_version": "v",
                "trigger": "schedule",
                "status": status,
                "started_at": "2026-10-01T20:00:00+00:00",
                "ended_at": "2026-10-01T20:00:05+00:00",
                "error": None,
            }
        )
    r = pkpipe("eval", "--suite", "production", "--db", str(db), "--out", str(tmp_path / "r"), "--json")
    data = json.loads(r.stdout)
    assert r.returncode == 1 and data["suites"][0]["metrics"]["failed_run_rate"] == round(2 / 3, 4)
    assert "gate: production: failed_run_rate" in r.stderr


def test_unknown_suite_is_a_usage_error():
    r = pkpipe("eval", "--suite", "triage,bogus")
    assert r.returncode == 2 and "unknown suite(s) bogus" in r.stderr
