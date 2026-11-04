"""Spec 020 FR-005 / US3: which list entries leave the list, and what is reported when one cannot."""

from __future__ import annotations

from pkpipe.domain.inbox_list import parse_list
from pkpipe.domain.triage import item_id_for
from pkpipe.engine.expr import MISSING
from pkpipe.steps import CODE, RunInfo, StepContext
from tests.support.flows import make_ports, make_snapshot

LIST = "# Inbox\n---\n- https://example.org/a\n- https://example.org/a?utm_source=x\n- a plain thought\n"
A = item_id_for("https://example.org/a")
THOUGHT = item_id_for("a plain thought")


def _ctx(outputs=None):
    snap = make_snapshot()
    outputs = outputs or {}
    return StepContext(
        flow=None,
        node=None,
        run=RunInfo("r", "f", "manual", snap.version),
        snapshot=snap,
        ports=make_ports(),
        item=None,
        inputs={},
        resolver=lambda ref: outputs.get(ref, MISSING),
    )


def _done(item_id, **item):
    return {"status": "done", "item": {"id": item_id, "category": "misc", "source": "list", **item}}


def test_only_processed_list_items_are_consumed():
    """A quarantined and a failed item, and a note, never reach ``consume_ids``: their raw entry stays."""
    results = [
        _done("p", resource="r/p"),
        _done("q", flagged=True),
        _done("s", state="quarantined"),
        {"status": "failed", "item": {"id": "f", "source": "list"}},
        {"status": "done", "item": {"id": "n", "category": "misc", "resource": "0-INBOX/n.md"}},
    ]
    out = CODE["triage.finalize"](_ctx(), results=results)
    assert out["consume_ids"] == ["p"]
    assert (out["processed"], out["quarantined"], out["failed"]) == (2, 2, 1)


def test_an_item_whose_processing_failed_keeps_its_entry():
    """The item is processed (its row says ``failed`` and why), but Clarification 3: failed entries stay."""
    out = CODE["triage.finalize"](
        _ctx(), results=[_done("p", processing_status="failed", failure_reason="x")]
    )
    assert out["consume_ids"] == [] and out["processed"] == 1
    assert out["processing_rows"][0]["status"] == "failed"


def test_nothing_is_consumed_when_every_item_is_quarantined_or_failed():
    results = [_done("q", flagged=True), {"status": "failed", "item": {"id": "f", "source": "list"}}]
    out = CODE["triage.finalize"](_ctx(), results=results)
    assert out["consume_ids"] == [] and out["has_consumed"] is False


def test_consume_returns_all_duplicate_entries_of_the_processed_ids_only():
    out = CODE["inbox.consume"](_ctx(), list_note={"body": LIST}, ids=[A])
    entries = parse_list(LIST)
    assert out["entries"] == [entries[0].raw, entries[1].raw]
    assert out["entry_ids"] == [A, A] and out["count"] == 2 and out["missing"] == []
    assert THOUGHT not in out["entry_ids"]  # an entry that was not processed stays in the list


def test_consume_reports_an_id_that_is_no_longer_in_the_list():
    out = CODE["inbox.consume"](_ctx(), list_note={"body": LIST}, ids=[A, "gone"])
    assert out["missing"] == ["gone"] and out["count"] == 2
    assert CODE["inbox.consume"](_ctx(), list_note={"body": ""}, ids=["x"])["entries"] == []


def test_report_is_quiet_when_everything_was_removed():
    out = CODE["inbox.report"](
        _ctx(
            {
                "consume": {"entries": ["- a"], "entry_ids": ["i1"], "missing": []},
                "remove_entries": {"removed": ["- a"], "not_found": []},
            }
        )
    )
    assert out["not_found"] == [] and out["count_not_found"] == 0 and out["note"] == ""
    assert out["remove_failed"] is False
    assert CODE["inbox.report"](_ctx())["note"] == ""  # no list item was processed at all


def test_report_names_the_items_whose_entry_is_gone_or_changed():
    out = CODE["inbox.report"](
        _ctx(
            {
                "consume": {"entries": ["- a", "- b"], "entry_ids": ["i1", "i2"], "missing": ["i3"]},
                "remove_entries": {"removed": ["- a"], "not_found": ["- b"]},
            }
        )
    )
    assert out["not_found"] == ["i2", "i3"] and out["count_not_found"] == 2
    assert "2 processed item(s) no longer in the INBOX list" in out["note"]


def test_report_flags_a_failed_removal():
    out = CODE["inbox.report"](
        _ctx(
            {
                "consume": {"entries": ["- a"], "entry_ids": ["i1"], "missing": []},
                "remove_entries": {"error": "x"},
            }
        )
    )
    assert out["remove_failed"] is True and "failed" in out["note"]
