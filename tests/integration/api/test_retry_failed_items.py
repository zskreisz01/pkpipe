"""Spec 027: the owner sends failed items through the release again (API, UI, app MCP, CLI) on a real vault.

The state machine stays strict: only these owner channels leave ``processed``, only for an item whose
processing list line says ``failed``, at most ``triage.retry_max`` times per item."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastmcp import Client

from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists
from tests.integration.api.test_web_triage_surface import login
from tests.support.app_stack import AGENT_TOKEN, OWNER_TOKEN, api_client, running, seed_triage

TRIAGE_FILE = "0-INBOX/Inbox-Triage.md"
PROCESSING = "0-INBOX/Manual-Resource-Processing-List.md"


def rows(stack):
    return {r["item_id"]: r for r in lists.read_triage(stack.vault / TRIAGE_FILE)["rows"]}


def line(stack, item_id):
    return next(r for r in lists.read_processing_list(stack.vault / PROCESSING) if r.item_id == item_id)


def fail(stack, item_id, status="failed"):
    """What a release run does to an item: it ends ``processed`` with a (failed) line in the list."""
    vault = stack.services.vault
    state = rows(stack)[item_id]["state"]
    if state in ("suggested", "pending"):
        vault.owner_decide(item_id, confirm=True)
    if state != "released":
        vault.update_triage([{"item_id": item_id, "state": "released"}])
    vault.update_triage([{"item_id": item_id, "state": "processed"}])
    vault.upsert_processing_list(
        [
            {
                "item_id": item_id,
                "category": "tool",
                "obsidian_link": "the page could not be fetched",
                "status": status,
            }
        ]
    )


@pytest.fixture
def failed(stack):
    ids = seed_triage(stack, a="tool", b="tool", ok="tool", q="tool")
    fail(stack, ids["a"])
    fail(stack, ids["b"])
    fail(stack, ids["ok"], status="done")
    stack.services.vault.update_triage([{"item_id": ids["q"], "state": "quarantined"}])
    stack.services.vault.upsert_processing_list(
        [{"item_id": ids["q"], "category": "tool", "obsidian_link": "quarantined", "status": "failed"}]
    )
    return ids


def test_the_summary_counts_failed_items_and_the_rows_say_which_can_be_retried(stack, failed):
    client = api_client(stack)
    summary = client.get("/api/triage/failed").json()
    assert summary == {"failed": 3, "retryable": 2, "capped": 1, "max": 3}  # the quarantined one is capped
    by_id = {r["item_id"]: r for r in client.get("/api/triage?state=processed").json()["rows"]}
    assert by_id[failed["a"]]["failed"] is True and by_id[failed["a"]]["retryable"] is True
    assert "failed" not in by_id[failed["ok"]]


def test_a_retry_releases_the_item_keeps_the_decision_resets_the_line_and_is_audited(stack, failed):
    client = api_client(stack)
    resp = client.post(f"/api/triage/{failed['a']}/retry")
    assert resp.status_code == 200 and resp.json()["state"] == "released" and resp.json()["retries"] == 1
    r = rows(stack)[failed["a"]]
    assert r["state"] == "released" and r["confirm"] is True
    assert line(stack, failed["a"]).status == "unprocessed"
    audit = client.get("/api/audit").json()["audit"]
    assert audit[0]["action"] == "retry_item" and audit[0]["target"] == failed["a"]
    assert audit[0]["actor"] == "ui" and "resource" not in json.dumps(audit[0])


def test_what_cannot_be_retried_is_refused_and_changes_nothing(stack, failed):
    client = api_client(stack)
    before = (stack.vault / TRIAGE_FILE).read_text(), (stack.vault / PROCESSING).read_text()
    for item, message in ((failed["ok"], "did not fail"), (failed["q"], "quarantine")):
        resp = client.post(f"/api/triage/{item}/retry")
        assert resp.status_code == 409 and message in resp.json()["error"]
    assert client.post("/api/triage/ffffffffffff/retry").status_code == 409
    assert ((stack.vault / TRIAGE_FILE).read_text(), (stack.vault / PROCESSING).read_text()) == before
    refused = [a for a in client.get("/api/audit").json()["audit"] if a["action"] == "retry_item_refused"]
    assert len(refused) == 3


def test_an_item_can_be_retried_three_times_then_the_owner_has_to_fix_the_cause(stack, failed):
    client = api_client(stack)
    for n in (1, 2, 3):
        assert client.post(f"/api/triage/{failed['a']}/retry").json()["retries"] == n
        fail(stack, failed["a"])  # the next run failed again
    resp = client.post(f"/api/triage/{failed['a']}/retry")
    assert resp.status_code == 409 and "limit 3" in resp.json()["error"]
    assert rows(stack)[failed["a"]]["state"] == "processed"
    row = {r["item_id"]: r for r in client.get("/api/triage?state=processed").json()["rows"]}[failed["a"]]
    assert row["retries"] == 3 and row["retryable"] is False
    page = login(stack).get("/triage?state=processed").text
    assert "retry limit reached" in page


def test_the_bulk_retry_needs_the_confirm_and_skips_what_it_cannot_do(stack, failed):
    client = api_client(stack)
    assert client.post("/api/triage/retry-failed", json={}).status_code == 409
    assert rows(stack)[failed["a"]]["state"] == "processed"
    out = client.post("/api/triage/retry-failed", json={"confirm": True}).json()
    assert out["retried"] == 2 and set(out["item_ids"]) == {failed["a"], failed["b"]}
    assert out["skipped"] == 1  # the quarantined one: a failed line, but never retryable
    assert rows(stack)[failed["q"]]["state"] == "quarantined"
    audit = client.get("/api/audit").json()["audit"]
    assert next(a for a in audit if a["action"] == "bulk_retry")["detail"]["retried"] == 2


def test_the_ui_shows_the_failed_result_a_retry_button_and_a_confirmed_bulk_action(stack, failed):
    ui = login(stack)
    page = ui.get("/triage?state=processed").text
    assert 'data-testid="failed-result"' in page and 'data-testid="retry"' in page
    assert "3 item(s) failed to process, 2 can be retried" in page and 'data-testid="retry-failed"' in page
    assert "hx-confirm" in page
    assert (
        ui.post("/ui/triage/retry-failed", data={}).status_code == 409
    )  # the confirm step is server side too
    row = ui.post(f"/ui/triage/{failed['a']}/retry")
    assert row.status_code == 200 and "released again for the next run" in row.text
    done = ui.post("/ui/triage/retry-failed", data={"confirm": "yes"})
    assert done.status_code == 200 and done.headers["HX-Redirect"].startswith("/triage?")
    assert rows(stack)[failed["b"]]["state"] == "released"
    assert "1+failed+item" in done.headers["HX-Redirect"]


def test_an_agent_token_cannot_retry_anything(stack, failed):
    agent = api_client(stack, AGENT_TOKEN)
    for path in (f"/api/triage/{failed['a']}/retry", "/api/triage/retry-failed", "/api/triage/failed"):
        assert agent.post(path, json={"confirm": True}).status_code == 403, path
    assert rows(stack)[failed["a"]]["state"] == "processed"


def run(coro):
    return asyncio.run(coro)


async def call(url, token, tool, **args):
    async with Client(f"{url}/mcp", auth=token) as c:
        res = await c.call_tool(tool, args, raise_on_error=False)
        return res.is_error, (res.content[0].text if res.is_error else res.data)


def test_retry_items_is_an_owner_tool_and_the_agent_is_refused_and_audited(stack, failed):
    with running(stack) as srv:
        err, msg = run(call(srv.url, AGENT_TOKEN, "retry_items"))
        assert err and "owner" in msg
        assert rows(stack)[failed["a"]]["state"] == "processed"
        err, data = run(call(srv.url, OWNER_TOKEN, "retry_items", item_ids=[failed["a"]]))
        assert not err and data["retried"] == 1 and data["item_ids"] == [failed["a"]]
    assert (
        rows(stack)[failed["a"]]["state"] == "released" and rows(stack)[failed["b"]]["state"] == "processed"
    )
    actions = [a["action"] for a in stack.services.store.audit_log()]
    assert "retry_items_refused" in actions and "retry_item" in actions


def test_the_cli_lists_by_default_and_retries_on_request(stack, failed, capsys):
    from pkpipe.retry_cli import retry_command

    args = SimpleNamespace(
        items=[],
        all=False,
        pack=str(stack.pack),
        overlay=str(stack.overlay),
        db=str(stack.db),
        vault=str(stack.vault),
        json=True,
    )
    assert retry_command(args) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["failed"] == 3 and {i["item_id"] for i in listed["items"]} == {failed["a"], failed["b"]}
    assert rows(stack)[failed["a"]]["state"] == "processed"  # a dry run
    args.all = True
    assert retry_command(args) == 0
    assert json.loads(capsys.readouterr().out)["retried"] == 2
    assert rows(stack)[failed["a"]]["state"] == "released"
    args.items = [failed["b"]]
    assert retry_command(args) == 2  # --all with item ids is a usage error


def test_the_failed_items_tool_is_read_only_and_returns_no_text(stack, failed):
    out = stack.services.vault.failed_items()
    assert out["count"] == 3 and all(set(i) == {"item_id", "category"} for i in out["items"])
    assert item_id_for("0-INBOX/a.md") in {i["item_id"] for i in out["items"]}
