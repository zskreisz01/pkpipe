"""Spec 014 US1 with the real publish gate: a changed triage instruction is published from the API after the
affected evals (triage + security smoke) ran against the real model (live) or its recorded cassettes (CI).

Live: ``PKPIPE_REQUIRE_LIVE=1 uv run pytest tests/integration/api/test_publish_real_model.py``; with
``PKPIPE_UPDATE_CASSETTES=1`` the recording is written to ``evals/cassettes/ui_publish`` (commit it).
"""

from __future__ import annotations

import os
import shutil

import pytest

from pkpipe import app
from pkpipe.evals import EvalPaths, PublishGate
from tests.conftest import REPO
from tests.support import live
from tests.support.app_stack import api_client, git, make_stack

CASSETTES = REPO / "evals" / "cassettes" / "ui_publish"
INSTRUCTION = "instructions/triage/categorize.md"
SUFFIX = " When two categories fit, prefer the one that matches what the owner would do with the resource.\n"


def _stack(tmp_path, synthetic_vault, pack_dir, mode, cassette_dir):
    def gate(snapshot):
        model = None if mode == "replay" else app.make_model(snapshot)
        return PublishGate(
            model=model,
            paths=EvalPaths.create(REPO / "evals", None, cassette_dir),
            mode=mode,
            concurrency=live.eval_concurrency(),
        )

    return make_stack(tmp_path, synthetic_vault, pack_dir, gate_factory=gate)


def _publish(stack, pack_dir):
    client = api_client(stack)
    draft = client.post("/api/drafts").json()
    original = (pack_dir / INSTRUCTION).read_text(encoding="utf-8")
    body = {"path": INSTRUCTION, "content": original.rstrip("\n") + SUFFIX, "rev": draft["rev"]}
    assert client.put(f"/api/drafts/{draft['draft_id']}/files", json=body).status_code == 200
    return (
        client,
        draft,
        client.post(f"/api/drafts/{draft['draft_id']}/publish", json={"summary": "tune triage"}),
    )


def _assert_published(stack, resp):
    body = resp.json()
    assert resp.status_code == 200 and body["published"], body.get("failures")
    assert body["status"] == "passed" and {"triage", "security"} <= set(body["affected"])
    assert git(stack.overlay, "log", "-1", "--format=%s") == "config: tune triage"
    return body


def test_publish_gate_replays_the_recorded_real_model_responses(tmp_path, synthetic_vault, pack_dir):
    """US1 / SC-001 in CI: the recorded qwen3.5 responses pass the triage and security gates offline."""
    if not CASSETTES.is_dir():
        pytest.skip("no recorded cassettes yet (run the live test with PKPIPE_UPDATE_CASSETTES=1)")
    stack = _stack(tmp_path, synthetic_vault, pack_dir, "replay", CASSETTES)
    _, _, resp = _publish(stack, pack_dir)
    body = _assert_published(stack, resp)
    assert (
        body["metrics"]["triage"]["accuracy"]["value"] >= body["metrics"]["triage"]["accuracy"]["threshold"]
    )


@pytest.mark.live("lmstudio", "judge")
def test_publish_gate_with_the_live_model_and_cassette_recording(
    tmp_path, synthetic_vault, pack_dir, lmstudio, monkeypatch, live_eval_cassettes
):
    """US1 live (qwen3.5-9b-mlx): triage accuracy/F1 and the security suite gate a real publish. The changed
    triage instruction makes every triage and injection request new (live); the secure-fetch Reader pages and
    classifier texts it shares with the other live eval tests are sent once per session."""
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", lmstudio["base_url"])
    monkeypatch.setenv("PKPIPE_MODEL_NAME", lmstudio["model"])
    monkeypatch.setenv("PKPIPE_JUDGE_MODEL", live.judge_model_name())
    if os.environ.get("PKPIPE_UPDATE_CASSETTES") == "1":  # a full, pruned recording of this test only
        recording, mode = tmp_path / "cassettes", "record"
    else:  # reuse identical live requests of the other live eval tests (tests/conftest.py)
        recording, mode = live_eval_cassettes, "auto"
    stack = _stack(tmp_path, synthetic_vault, pack_dir, mode, recording)
    _, _, resp = _publish(stack, pack_dir)
    body = _assert_published(stack, resp)
    assert (recording / "triage.json").is_file() and (recording / "security.json").is_file()
    print("LIVE GATE", {k: body[k] for k in ("status", "affected", "failures", "metrics")})
    for suite in ("triage", "security"):  # every gated metric was measured, none missing
        checks = {m: c for m, c in body["metrics"][suite].items() if m != "status"}
        assert checks and all(c["value"] is not None for c in checks.values()), suite
    if os.environ.get("PKPIPE_UPDATE_CASSETTES") == "1":
        shutil.rmtree(CASSETTES, ignore_errors=True)
        shutil.copytree(recording, CASSETTES)
