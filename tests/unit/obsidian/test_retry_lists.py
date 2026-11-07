"""Spec 027: the file side of the owner's retry (triage row + processing list line)."""

from __future__ import annotations

import pytest

from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists
from pkpipe.obsidian.errors import InvalidRows


@pytest.fixture
def files(tmp_path):
    triage, processing = tmp_path / "triage.md", tmp_path / "processing.md"
    ids = {}
    for name, state, status in (
        ("failed", "processed", "failed"),
        ("done", "processed", "done"),
        ("open", "released", None),
        ("quarantined", "quarantined", "failed"),
    ):
        resource = f"0-INBOX/{name}.md"
        ids[name] = item_id_for(resource)
        lists.update_triage(
            triage, [{"resource": resource, "ai_suggested_category": "tool", "state": "suggested"}]
        )
        if state != "suggested":
            _walk(triage, ids[name], state)
        if status:
            lists.upsert_processing_list(
                processing,
                [{"item_id": ids[name], "category": "tool", "obsidian_link": resource, "status": status}],
            )
    return triage, processing, ids


def _walk(triage, item_id, state):
    """suggested -> (owner confirms) -> released -> processed, or quarantined."""
    if state == "quarantined":
        lists.update_triage(triage, [{"item_id": item_id, "state": "quarantined"}])
        return
    lists.owner_decide(triage, item_id, confirm=True)
    lists.update_triage(triage, [{"item_id": item_id, "state": "released"}])
    if state == "processed":
        lists.update_triage(triage, [{"item_id": item_id, "state": "processed"}])


def row(triage, item_id):
    return next(r for r in lists.read_triage(triage)["rows"] if r["item_id"] == item_id)


def line(processing, item_id):
    return next(r for r in lists.read_processing_list(processing) if r.item_id == item_id)


def test_failed_lines_are_listed_with_their_category(files):
    _, processing, ids = files
    assert lists.failed_processing_items(processing) == {ids["failed"]: "tool", ids["quarantined"]: "tool"}


def test_a_retry_releases_the_row_keeps_the_decision_and_resets_the_failed_line(files):
    triage, processing, ids = files
    out = lists.retry_item(triage, processing, ids["failed"], retries=0, max_retries=3)
    r = row(triage, ids["failed"])
    assert r["state"] == "released" and r["confirm"] is True and out["processing_line_reset"] is True
    assert line(processing, ids["failed"]).status == "unprocessed"
    assert line(processing, ids["done"]).status == "done"  # other lines are untouched


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("done", "did not fail"),
        ("open", "only a processed item"),
        ("quarantined", "quarantine"),
    ],
)
def test_a_retry_that_the_rules_refuse_changes_nothing(files, name, message):
    triage, processing, ids = files
    before = (triage.read_text(), processing.read_text())
    with pytest.raises(InvalidRows, match=message):
        lists.retry_item(triage, processing, ids[name], retries=0, max_retries=3)
    assert (triage.read_text(), processing.read_text()) == before


def test_the_limit_and_unknown_ids_are_refused(files):
    triage, processing, ids = files
    with pytest.raises(InvalidRows, match="limit"):
        lists.retry_item(triage, processing, ids["failed"], retries=3, max_retries=3)
    with pytest.raises(InvalidRows, match="unknown"):
        lists.retry_item(triage, processing, "nope", retries=0, max_retries=3)


def test_the_pipeline_path_still_cannot_leave_processed(files):
    triage, _, ids = files
    for target in ("released", "pending"):
        with pytest.raises(InvalidRows, match="not allowed"):
            lists.update_triage(triage, [{"item_id": ids["failed"], "state": target}])


def test_the_retry_limit_is_a_validated_config_value():
    """Spec 027 FR-006: ``triage.retry_max`` defaults to 3, at least 1."""
    from pydantic import ValidationError

    from pkpipe.engine.config_models import TriageConfig

    assert TriageConfig().retry_max == 3 and TriageConfig(retry_max=5).retry_max == 5
    for bad in (0, -1, 21):
        with pytest.raises(ValidationError):
            TriageConfig(retry_max=bad)
