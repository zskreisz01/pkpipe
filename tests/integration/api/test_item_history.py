"""Spec 030: the history of one item (API, UI page, MCP tool) and the process buttons of the overview."""

from __future__ import annotations

from pkpipe.mcp.server import OWNER_TOOLS
from tests.support.app_stack import api_client, seed_triage


def test_the_history_of_an_item_lists_its_events_and_the_owner_actions(stack):
    ids = seed_triage(stack, a="tool")
    store = stack.services.store
    store.record(ids["a"], "step", run_id="run-1", flow_id="triage", step="categorize", outcome="done")
    store.record(ids["a"], "verdict", run_id="run-1", outcome="clean", detail={"flags": []})
    client = api_client(stack)
    client.post(f"/api/triage/{ids['a']}/confirm")
    h = client.get(f"/api/items/{ids['a']}/history").json()
    kinds = [e["kind"] for e in h["timeline"]]
    assert kinds[:2] == ["step", "verdict"] and "owner" in kinds
    assert h["row"]["item_id"] == ids["a"]


def test_an_unknown_item_is_not_found(stack):
    assert api_client(stack).get("/api/items/ffffffffffff/history").status_code == 404


def test_the_item_page_and_the_search_work_and_the_triage_row_links_to_it(stack):
    ids = seed_triage(stack, a="tool")
    stack.services.store.record(ids["a"], "source", outcome="no_results", detail={"reason": "none"})
    client = api_client(stack)
    page = client.get(f"/items/{ids['a']}")
    assert page.status_code == 200 and "source search: no_results" in page.text
    assert client.get(f"/items?q={ids['a']}").status_code == 303
    assert "no item" in client.get("/items?q=nope").text
    assert f"/items/{ids['a']}" in client.get("/triage").text


def test_the_overview_offers_the_process_start_buttons(stack):
    html = api_client(stack).get("/").text
    assert 'data-testid="launch-triage"' in html and 'data-testid="launch-process_released"' in html
    assert 'data-testid="launch-secure_fetch"' not in html  # a subflow needs an item


def test_the_mcp_tool_is_an_owner_tool():
    assert "item_history" in OWNER_TOOLS


def test_an_auto_passed_item_is_marked_in_the_triage_and_its_history(stack):
    ids = seed_triage(stack, a="tool", b="tool")
    stack.services.store.record(
        ids["a"],
        "auto_passed",
        outcome="passed",
        detail={"classifier": {"reading": "injection_detector_only"}},
    )
    client = api_client(stack)
    rows = {r["item_id"]: r for r in client.get("/api/triage").json()["rows"]}
    assert rows[ids["a"]]["auto_passed"] is True and "auto_passed" not in rows[ids["b"]]
    assert 'data-testid="auto-passed"' in client.get("/triage").text
    history = client.get(f"/api/items/{ids['a']}/history").json()["timeline"]
    assert "auto-passed" in history[0]["text"] and "auto_pass rule" in history[0]["text"]
