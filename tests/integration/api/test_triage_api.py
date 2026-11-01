"""Spec 014 FR-002 / US2: owner decisions on triage items through the API, on a real vault file."""

from __future__ import annotations

from pkpipe.obsidian import lists
from tests.support.app_stack import api_client, seed_triage

TRIAGE_FILE = "0-INBOX/Inbox-Triage.md"


def _rows(stack):
    return {r["item_id"]: r for r in lists.read_triage(stack.vault / TRIAGE_FILE)["rows"]}


def test_confirm_writes_the_triage_file_and_is_audited(stack):
    """US2 scenario 1: confirm updates the triage file; the owner action is recorded."""
    ids = seed_triage(stack, a="tool", b="marketing")
    client = api_client(stack)
    listing = client.get("/api/triage").json()["rows"]
    assert {r["item_id"] for r in listing} == set(ids.values())
    resp = client.post(f"/api/triage/{ids['a']}/confirm")
    assert resp.status_code == 200 and resp.json()["confirm"] is True
    rows = _rows(stack)
    assert rows[ids["a"]]["confirm"] is True and rows[ids["b"]]["confirm"] is False
    audit = client.get("/api/audit").json()["audit"]
    assert (
        audit[0]["action"] == "confirm_item" and audit[0]["target"] == ids["a"] and audit[0]["actor"] == "ui"
    )


def test_override_must_name_a_configured_category(stack):
    ids = seed_triage(stack, a="tool")
    client = api_client(stack)
    bad = client.post(f"/api/triage/{ids['a']}/override", json={"category": "no-such-category"})
    assert bad.status_code == 409 and "unknown category" in bad.json()["error"]
    assert _rows(stack)[ids["a"]]["override"] is None
    assert client.post(f"/api/triage/{ids['a']}/override", json={"category": ""}).status_code == 409
    ok = client.post(f"/api/triage/{ids['a']}/override", json={"category": "marketing"})
    assert ok.status_code == 200 and _rows(stack)[ids["a"]]["override"] == "marketing"
    refused = [a for a in client.get("/api/audit").json()["audit"] if a["action"].endswith("_refused")]
    assert refused and refused[0]["action"] == "override_item_refused"


def test_unknown_item_and_quarantined_items_cannot_be_confirmed(stack):
    ids = seed_triage(stack, a="tool", q="tool")
    stack.services.vault.update_triage([{"item_id": ids["q"], "state": "quarantined"}])
    client = api_client(stack)
    assert client.post("/api/triage/ffffffffffff/confirm").status_code == 409  # unknown item id
    q = client.post(f"/api/triage/{ids['q']}/confirm")
    assert q.status_code == 409 and "quarantined" in q.json()["error"]
    assert _rows(stack)[ids["q"]]["confirm"] is False


def test_release_quarantine_returns_the_item_to_pending_and_only_for_quarantined(stack):
    ids = seed_triage(stack, a="tool", q="tool")
    stack.services.vault.update_triage([{"item_id": ids["q"], "state": "quarantined"}])
    client = api_client(stack)
    not_q = client.post(f"/api/triage/{ids['a']}/release-quarantine")
    assert not_q.status_code == 409 and "not quarantined" in not_q.json()["error"]
    ok = client.post(f"/api/triage/{ids['q']}/release-quarantine")
    assert ok.status_code == 200 and ok.json()["state"] == "pending"
    assert _rows(stack)[ids["q"]]["state"] == "pending"
    # the item is a normal pending item again: the owner can now confirm it
    assert client.post(f"/api/triage/{ids['q']}/confirm").status_code == 200


def test_the_vault_server_path_still_refuses_owner_fields(stack):
    """Constitution VI: the pipeline's update_triage keeps refusing confirm/override; only the owner path
    (UI, authenticated MCP) can set them."""
    import pytest

    from pkpipe.obsidian.errors import InvalidRows

    ids = seed_triage(stack, a="tool")
    with pytest.raises(InvalidRows, match="owner"):
        stack.services.vault.update_triage([{"item_id": ids["a"], "confirm": True}])


def test_triage_resource_text_is_length_capped(stack):
    long_name = "x" * 600
    stack.services.vault.update_triage(
        [{"resource": f"0-INBOX/{long_name}.md", "ai_suggested_category": "tool"}]
    )
    row = api_client(stack).get("/api/triage").json()["rows"][0]
    assert len(row["resource"]) == 300


def test_triage_requires_a_configured_vault(stack):
    stack.services.vault = None
    resp = api_client(stack).get("/api/triage")
    assert resp.status_code == 409 and "no vault" in resp.json()["error"]
