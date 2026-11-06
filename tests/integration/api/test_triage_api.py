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


def test_override_must_name_a_configured_category_or_a_valid_new_name(stack):
    ids = seed_triage(stack, a="tool")
    client = api_client(stack)
    bad = client.post(f"/api/triage/{ids['a']}/override", json={"category": "<b>no such</b>"})
    assert bad.status_code == 409 and "unknown category" in bad.json()["error"]
    assert _rows(stack)[ids["a"]]["override"] is None
    new = client.post(f"/api/triage/{ids['a']}/override", json={"category": "Children Books"})
    assert new.status_code == 200 and _rows(stack)[ids["a"]]["override"] == "children-books"  # spec 023
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


def _quarantined(stack, name="odd"):
    """A note the handle_security_flag flow moved into quarantine (the vault tool the flow calls)."""
    from pkpipe.domain.triage import item_id_for

    note = "---\nsource: https://example.invalid/odd\n---\nA false positive about prompt injection talks.\n"
    (stack.vault / "0-INBOX" / f"{name}.md").write_text(note, encoding="utf-8")
    resource = f"0-INBOX/{name}.md"
    item = item_id_for(resource)
    stack.services.vault.update_triage(
        [{"resource": resource, "ai_suggested_category": "none", "state": "quarantined"}]
    )
    flags = [
        {"flag": "injection_or_jailbreak", "detector": "heuristic", "reason_code": "heuristic:prompt_leak"}
    ]
    stack.services.vault.quarantine_note(item, resource, flags, run_id="r1", flagged_at="2026-11-01T06:00")
    return item, resource, note


def test_release_restores_the_note_requeues_it_and_exports_an_eval_candidate(stack):
    """Spec 009 US3: the owner releases a false positive; the note is back in the INBOX exactly as it was,
    the row is pending (the next triage suggests a category again) and the text is a security eval
    candidate in the overlay. The response never carries the note text."""
    import json

    item, resource, note = _quarantined(stack)
    client = api_client(stack)
    resp = client.post(f"/api/triage/{item}/release-quarantine")
    body = resp.json()
    assert resp.status_code == 200, body
    assert body["state"] == "pending" and body["requeued"] is True and body["restored"] == resource
    assert "content" not in body and "prompt injection talks" not in json.dumps(body)
    assert (stack.vault / resource).read_text(encoding="utf-8") == note
    assert not (stack.vault / "0-INBOX/_quarantine" / f"{item}.md").exists()
    cand = stack.overlay / "evals" / "candidates" / "security.jsonl"
    case = json.loads(cand.read_text(encoding="utf-8").splitlines()[0])
    assert case["id"] == body["eval_candidate"] and case["source"] == "release"
    assert case["expected"] == {
        "label": "benign",
        "type": "released_false_positive",
        "flags": ["injection_or_jailbreak"],
    }
    assert "prompt injection talks" in case["input"]["text"]
    audit = client.get("/api/audit").json()["audit"][0]
    assert audit["action"] == "release_quarantine" and audit["target"] == item


def test_release_is_refused_when_the_inbox_path_is_taken_again(stack):
    item, resource, _ = _quarantined(stack)
    (stack.vault / resource).write_text("a new note with the same name", encoding="utf-8")
    client = api_client(stack)
    resp = client.post(f"/api/triage/{item}/release-quarantine")
    assert resp.status_code == 409 and "cannot restore" in resp.json()["error"]
    assert _rows(stack)[item]["state"] == "quarantined"
