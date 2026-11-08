"""Spec 029: an item held after a classifier warning is shown with its evidence and accepted by the owner
only, one by one; a quarantined item is shown with its address and scores."""

from __future__ import annotations

import pytest

from pkpipe.obsidian import lists
from tests.integration.api.test_retry_failed_items import PROCESSING, fail, rows
from tests.support.app_stack import api_client, seed_triage

EVIDENCE = {
    "threshold": 0.5,
    "max_score": 0.99,
    "reading": "injection_detector_only",
    "members": [
        {
            "model": "Qwen3Guard-Gen-0.6B",
            "kind": "safety",
            "max_score": 0.004,
            "chunks": 2,
            "over": False,
            "flagged_chunks": [],
            "flagged_chunk_count": 0,
            "label": "Safe",
            "categories": [],
        },
        {
            "model": "deberta-v3-base-prompt-injection-v2",
            "kind": "injection",
            "max_score": 0.99,
            "chunks": 4,
            "over": True,
            "flagged_chunks": [1, 2],
            "flagged_chunk_count": 2,
        },
    ],
}


@pytest.fixture
def held(stack):
    ids = seed_triage(stack, h="tool", f="tool")
    fail(stack, ids["h"])
    fail(stack, ids["f"])
    stack.services.store.hold(ids["h"], "https://example.org/repo", EVIDENCE)
    return ids


def test_a_held_item_shows_its_address_and_evidence_and_is_left_out_of_the_bulk_retry(stack, held):
    client = api_client(stack)
    assert client.get("/api/triage/failed").json()["held"] == 1
    by_id = {r["item_id"]: r for r in client.get("/api/triage?state=processed").json()["rows"]}
    review = by_id[held["h"]]["review"]
    assert review["url"] == "https://example.org/repo" and not review["waived"]
    assert any("deberta" in s and "chunk(s) 1, 2 of 4" in s for s in review["lines"])
    assert "review" not in by_id[held["f"]]
    res = client.post("/api/triage/retry-failed", json={"confirm": True}).json()
    assert res["item_ids"] == [held["f"]] and res["skipped"] == 1  # the held one waits for its own decision
    assert rows(stack)[held["h"]]["state"] == "processed"


def test_accepting_the_warning_releases_the_item_and_records_the_waiver(stack, held):
    client = api_client(stack)
    out = client.post(f"/api/triage/{held['h']}/accept-warning").json()
    assert out["accepted"] is True and rows(stack)[held["h"]]["state"] == "released"
    assert stack.services.store.waived(held["h"]) is True
    line = next(r for r in lists.read_processing_list(stack.vault / PROCESSING) if r.item_id == held["h"])
    assert line.status == "unprocessed"
    assert any(a["action"] == "accept_classifier_warning" for a in stack.services.store.audit_log())


def test_an_item_that_was_not_held_cannot_be_accepted(stack, held):
    client = api_client(stack)
    r = client.post(f"/api/triage/{held['f']}/accept-warning")
    assert r.status_code >= 400 and stack.services.store.waived(held["f"]) is False


def test_a_quarantined_item_shows_where_it_came_from_and_why(stack):
    ids = seed_triage(stack, q="tool")
    vault = stack.services.vault
    flags = [
        {"detector": "classifier", "reason_code": "classifier_threshold", "score": 0.99, "detail": EVIDENCE}
    ]
    vault.quarantine_note(ids["q"], None, flags, "run-1", "2026-11-08T08:00:00", "https://example.org/repo")
    vault.update_triage([{"item_id": ids["q"], "state": "quarantined"}])
    row = api_client(stack).get("/api/triage?state=quarantined").json()["rows"][0]["quarantine"]
    assert row["source_url"] == "https://example.org/repo" and row["scores"] == {"classifier": 0.99}
    assert row["classifier"]["reading"] == "injection_detector_only"
    assert any("false positive" in s for s in row["lines"])
    note = (stack.vault / "0-INBOX/_quarantine" / f"{ids['q']}.md").read_text()
    assert "Source: `https://example.org/repo`" in note and "## Why it was flagged" in note
