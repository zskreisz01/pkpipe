"""Spec 031: the triage shows how a processed item went instead of the bare state ``processed``."""

from __future__ import annotations

from tests.integration.api.test_classifier_review import EVIDENCE
from tests.integration.api.test_retry_failed_items import fail, rows
from tests.support.app_stack import api_client, seed_triage


def _setup(stack):
    ids = seed_triage(stack, ok="tool", ready="tool", bad="tool", held="tool", open="tool", plain="tool")
    fail(stack, ids["ok"], status="done")
    fail(stack, ids["ready"], status="ready")
    fail(stack, ids["bad"])
    fail(stack, ids["open"], status="unprocessed")
    fail(stack, ids["held"])
    stack.services.store.hold(ids["held"], "https://example.org/repo", EVIDENCE)
    stack.services.vault.owner_decide(ids["plain"], confirm=True)
    stack.services.vault.update_triage([{"item_id": ids["plain"], "state": "released"}])
    stack.services.vault.update_triage([{"item_id": ids["plain"], "state": "processed"}])
    return ids


def test_each_processed_row_carries_its_outcome_and_a_label_that_is_not_just_processed(stack):
    ids = _setup(stack)
    by_id = {r["item_id"]: r for r in api_client(stack).get("/api/triage?state=processed").json()["rows"]}
    got = {name: (by_id[i]["outcome"], by_id[i]["outcome_label"]) for name, i in ids.items()}
    assert got["ok"] == ("done", "done") and got["ready"] == ("ready", "ready")
    assert got["bad"] == ("failed", "failed") and got["held"][0] == "held"
    assert got["open"] == ("unprocessed", "not finished")
    assert got["plain"][0] == "processed" and "unknown" in got["plain"][1]
    assert all(by_id[i]["state"] == "processed" for i in ids.values())  # the stored state is unchanged
    assert {r["state"] for r in rows(stack).values()} == {"processed"}


def test_other_states_show_as_they_are_and_the_outcome_filter_works(stack):
    ids = _setup(stack)
    client = api_client(stack)
    suggested = seed_triage(stack, new="tool")["new"]
    row = next(r for r in client.get("/api/triage").json()["rows"] if r["item_id"] == suggested)
    assert row["outcome"] == row["state"] and row["outcome_label"] == row["state"]
    failed = client.get("/api/triage?outcome=failed").json()["rows"]
    assert [r["item_id"] for r in failed] == [ids["bad"]]
    assert {r["item_id"] for r in client.get("/api/triage?outcome=done").json()["rows"]} == {ids["ok"]}


def test_the_page_shows_the_label_and_offers_the_result_filter(stack):
    ids = _setup(stack)
    client = api_client(stack)
    page = client.get("/triage?state=processed")
    assert page.status_code == 200 and 'data-testid="outcome-filter"' in page.text
    assert ">not finished<" in page.text and ">held for you<" in page.text and ">done<" in page.text
    only = client.get("/triage?outcome=failed")
    assert ids["bad"] in only.text and ids["ok"] not in only.text
