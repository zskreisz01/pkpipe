"""Spec 014 US1 (runs view) / FR-002: starting and inspecting runs through the API."""

from __future__ import annotations

import threading

from pkpipe.obsidian import lists
from tests.support.app_stack import api_client


def test_start_a_flow_from_the_api_and_read_the_run(stack):
    """The flow runs in the background on the real run service; the run is recorded with trigger 'ui'."""
    client = api_client(stack)
    assert "triage" in client.get("/api/flows").json()["flows"]
    resp = client.post("/api/runs", json={"flow": "triage"})
    assert resp.status_code == 202
    run = resp.json()
    assert run["flow_id"] == "triage" and run["trigger"] == "ui"
    stack.services.launcher.wait(run["run_id"])
    done = client.get(f"/api/runs/{run['run_id']}").json()
    assert done["status"] == "done" and done["config_version"]
    assert "steps_done" in done and done["steps_done"]
    rows = lists.read_triage(stack.vault / "0-INBOX/Inbox-Triage.md")["rows"]
    assert {r["ai_suggested_category"] for r in rows} == {"tool", "learning-material"}
    listed = client.get("/api/runs", params={"flow": "triage"}).json()["runs"]
    assert [r["run_id"] for r in listed] == [run["run_id"]]
    assert client.get("/api/runs", params={"status": "failed"}).json()["runs"] == []


def test_unknown_flow_and_unknown_run_are_404_and_bad_inputs_are_rejected(stack):
    client = api_client(stack)
    assert client.post("/api/runs", json={"flow": "no_such_flow"}).status_code == 404
    assert client.get("/api/runs/does-not-exist").status_code == 404
    assert client.post("/api/runs", json={"flow": "triage", "inputs": "text"}).status_code == 422
    assert client.post("/api/runs", json={}).status_code == 422


def test_a_flow_with_an_active_run_is_not_started_twice(stack, monkeypatch):
    """One active run per flow (like the scheduler's max_instances=1); a finished run frees the flow."""
    started, release = threading.Event(), threading.Event()
    store = stack.services.runs.store

    def slow_start(flow_id, *, trigger, inputs, run_id):
        store.create_run(
            {
                "run_id": run_id,
                "flow_id": flow_id,
                "config_version": "v",
                "trigger": trigger,
                "status": "running",
                "started_at": "2026-01-01T00:00:00+00:00",
            }
        )
        started.set()
        release.wait(20)
        store.update_run(run_id, status="done")

    monkeypatch.setattr(stack.services.runs, "start", slow_start)
    client = api_client(stack)
    first = client.post("/api/runs", json={"flow": "triage"})
    assert first.status_code == 202 and started.wait(5)
    second = client.post("/api/runs", json={"flow": "triage"})
    assert second.status_code == 409 and "already has an active run" in second.json()["error"]
    release.set()
    stack.services.launcher.wait(first.json()["run_id"])
    third = client.post("/api/runs", json={"flow": "triage"})
    assert third.status_code == 202
    stack.services.launcher.wait(third.json()["run_id"])


def test_run_detail_never_exposes_step_output_values(stack):
    """Constitution IV: raw external content must not reach a chat agent or a casual viewer."""
    client = api_client(stack)
    run = client.post("/api/runs", json={"flow": "triage"}).json()
    stack.services.launcher.wait(run["run_id"])
    text = client.get(f"/api/runs/{run['run_id']}").text
    assert "synthetic note" not in text and "fictional CLI" not in text


def test_a_scheduled_tick_skips_a_flow_that_already_runs_from_the_ui(stack, monkeypatch):
    """Edge case across channels: the cron trigger of `pkpipe serve` follows the same one-active-run rule,
    so a schedule never starts a second run next to a UI- or chat-started one."""
    started, release = threading.Event(), threading.Event()
    store, calls = stack.services.runs.store, []

    def slow_start(flow_id, *, trigger, inputs=None, run_id=None):
        calls.append(trigger)
        if trigger == "schedule":
            return None
        store.create_run(
            {
                "run_id": run_id,
                "flow_id": flow_id,
                "config_version": "v",
                "trigger": trigger,
                "status": "running",
                "started_at": "2026-01-01T00:00:00+00:00",
            }
        )
        started.set()
        release.wait(20)
        store.update_run(run_id, status="done")

    monkeypatch.setattr(stack.services.runs, "start", slow_start)
    launcher = stack.services.launcher
    first = api_client(stack).post("/api/runs", json={"flow": "triage"})
    assert first.status_code == 202 and started.wait(5)
    assert launcher.run_scheduled("triage") is False and calls == ["ui"]
    release.set()
    launcher.wait(first.json()["run_id"])
    assert launcher.run_scheduled("triage") is True and calls == ["ui", "schedule"]
