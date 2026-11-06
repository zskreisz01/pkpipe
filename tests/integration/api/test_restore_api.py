"""Spec 019 FR-004: ``restore_run`` as an owner action of the JSON API, the UI and the app MCP server (owner
token only, audit-logged, note paths datamarked for a chat agent)."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client

from tests.support.app_stack import AGENT_TOKEN, OWNER_TOKEN, api_client, running


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def ran(stack):
    """A triage run done through the API: it created the triage file and snapshot."""
    before = tree(stack.vault)
    client = api_client(stack)
    run = client.post("/api/runs", json={"flow": "triage"}).json()
    stack.services.launcher.wait(run["run_id"])
    assert tree(stack.vault) != before
    return run["run_id"], before, client


async def call(url, token, tool, **args):
    async with Client(f"{url}/mcp", auth=token) as c:
        res = await c.call_tool(tool, args, raise_on_error=False)
        return res.is_error, (res.content[0].text if res.is_error else res.data)


def test_the_api_restore_is_a_dry_run_unless_apply_and_is_audited(stack, ran):
    run_id, before, client = ran
    changed = tree(stack.vault)
    plan = client.post(f"/api/runs/{run_id}/restore", json={}).json()
    assert plan["applied"] is False and plan["counts"]["remove"] >= 1
    assert tree(stack.vault) == changed
    assert not [a for a in stack.services.store.audit_log() if a["action"] == "restore_run"]

    done = client.post(f"/api/runs/{run_id}/restore", json={"apply": True}).json()
    assert done["applied"] is True and done["safety_snapshot"].startswith(f"restore-{run_id}")
    assert tree(stack.vault) == before
    (entry,) = [a for a in stack.services.store.audit_log() if a["action"] == "restore_run"]
    assert entry["actor"] == "ui" and entry["target"] == run_id and entry["detail"]["remove"] >= 1
    assert "Inbox-Triage" not in str(entry["detail"])  # no paths in the audit log
    snaps = client.get("/api/snapshots").json()["snapshots"]
    assert {s["run_id"]: s["status"] for s in snaps}[run_id] == "restored"


def test_unknown_run_is_404_and_an_active_run_is_refused(stack, ran):
    run_id, _, client = ran
    assert client.post("/api/runs/nope/restore", json={}).status_code == 404
    stack.services.runs.store.update_run(run_id, status="running", ended_at=None)
    resp = client.post(f"/api/runs/{run_id}/restore", json={"apply": True})
    assert resp.status_code == 409 and "after it ended" in resp.json()["error"]
    refused = [a for a in stack.services.store.audit_log() if a["action"] == "restore_run_refused"]
    assert len(refused) == 1


def test_the_agent_token_cannot_reach_the_restore_api(stack, ran):
    run_id, _, _ = ran
    agent = api_client(stack, AGENT_TOKEN)
    assert agent.post(f"/api/runs/{run_id}/restore", json={"apply": True}).status_code in (401, 403)
    assert agent.get("/api/snapshots").status_code in (401, 403)


def test_mcp_restore_run_is_owner_only_and_datamarks_paths(stack, ran):
    run_id, before, _ = ran
    with running(stack) as server:
        err, msg = asyncio.run(call(server.url, AGENT_TOKEN, "restore_run", run_id=run_id, apply=True))
        assert err and "owner action" in msg
        assert tree(stack.vault) != before  # nothing happened
        err, plan = asyncio.run(call(server.url, OWNER_TOKEN, "restore_run", run_id=run_id))
        assert not err and plan["applied"] is False
        assert plan["untrusted_fields"] == ["path", "from", "to", "reason"]
        assert plan["steps"] and all(s["path"].startswith("<<DATA:") for s in plan["steps"])
        err, plan = asyncio.run(call(server.url, OWNER_TOKEN, "restore_run", run_id=run_id, apply=True))
        assert not err and plan["applied"] is True
    assert tree(stack.vault) == before
    audit = stack.services.store.audit_log()
    assert [a["actor"] for a in audit if a["action"] == "restore_run_refused"] == ["mcp:agent"]
    assert [a["actor"] for a in audit if a["action"] == "restore_run"] == ["mcp:owner"]


def test_the_run_page_shows_the_snapshot_and_restores_from_the_ui(stack, ran):
    run_id, before, _ = ran
    ui = api_client(stack, None)
    assert ui.post("/login", data={"token": OWNER_TOKEN}).status_code == 303
    ui.headers["X-CSRF-Token"] = stack.web.state.sessions.csrf(ui.cookies["pkpipe_session"])
    ui.headers["HX-Request"] = "true"
    page = ui.get(f"/runs/{run_id}").text
    assert 'data-testid="restore-panel"' in page and "Apply restore" in page
    plan = ui.post(f"/ui/runs/{run_id}/restore", data={}).text
    assert "Dry run: nothing changed" in plan and "Inbox-Triage.md" in plan
    assert tree(stack.vault) != before
    done = ui.post(f"/ui/runs/{run_id}/restore", data={"apply": "1"}).text
    assert "Applied" in done and "reverted" in done
    assert tree(stack.vault) == before


def test_a_run_without_a_snapshot_says_so(stack):
    stack.services.runs.store.create_run(
        {
            "run_id": "read-only-run",
            "flow_id": "secure_fetch",
            "config_version": "v",
            "trigger": "manual",
            "status": "done",
            "started_at": "2026-11-04T10:00:00+00:00",
            "ended_at": "2026-11-04T10:00:01+00:00",
            "error": None,
        }
    )
    ui = api_client(stack, None)
    ui.post("/login", data={"token": OWNER_TOKEN})
    assert "took no vault snapshot" in ui.get("/runs/read-only-run").text
    assert api_client(stack).post("/api/runs/read-only-run/restore", json={}).status_code == 404
