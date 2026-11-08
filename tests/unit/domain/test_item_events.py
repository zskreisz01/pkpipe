"""Spec 030: the item event log keeps ids, numbers and codes only, never breaks a run, and reads as one
timeline together with the owner's audited actions."""

from __future__ import annotations

from pkpipe.adapters.app_store_sqlite import AppStore
from pkpipe.domain.item_events import describe, emit, safe_detail, timeline


class Boom:
    def record(self, *a, **k):
        raise RuntimeError("db is locked")


def test_a_private_flow_records_no_detail_at_all():
    assert safe_detail({"url": "https://x", "ms": 5}, private=True) == {}
    assert safe_detail({"url": "https://x", "none": None}, private=False) == {"url": "https://x"}


def test_long_text_is_cut_and_objects_become_short_strings():
    out = safe_detail({"a": "x" * 1000, "o": object()}, private=False)
    assert len(out["a"]) == 300 and out["o"].startswith("<object")


def test_a_broken_log_never_stops_the_run():
    emit(Boom(), "i1", "step")  # does not raise
    emit(None, "i1", "step")
    emit(Boom(), None, "step")


def test_events_are_stored_per_item_and_the_timeline_merges_the_audit(tmp_path):
    store = AppStore(tmp_path / "app.db")
    store.record("i1", "step", run_id="r1", flow_id="f", step="fetch", outcome="done", detail={"ms": 12})
    store.record("i1", "verdict", run_id="r1", outcome="quarantined", detail={"flags": ["classifier"]})
    store.record("i2", "step", step="x")
    store.audit("ui", "release_quarantine", "i1", state="pending")
    rows = timeline(store.events_of("i1"), store.audit_of("i1"))
    assert [r["kind"] for r in rows] == ["step", "verdict", "owner"]
    assert rows[0]["text"] == "fetch (?): done in 12 ms" or "fetch" in rows[0]["text"]
    assert "flags: classifier" in rows[1]["text"] and rows[2]["detail"]["actor"] == "ui"
    assert store.recent_item_ids(5)[0] == "i2" and len(store.events_of("i2")) == 1


def test_describe_has_a_sentence_for_every_kind():
    for kind in ("step", "verdict", "source", "quarantined", "held", "owner", "other"):
        assert describe({"kind": kind, "outcome": "x", "detail": {}})


def test_codes_stay_in_a_private_flow_but_detail_does_not():
    from tests.fakes import FakeItemEvents

    port = FakeItemEvents()
    emit(
        port,
        "i1",
        "quarantined",
        detail={"url": "https://x.example/p"},
        codes={"reasons": "classifier", "deny_listed": True},
        data_classes=["image"],
    )
    assert port.events_of("i1")[0]["detail"] == {"reasons": "classifier", "deny_listed": True}
