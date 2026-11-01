"""Spec 015: the deterministic ``code`` functions of the improvement flows."""

from __future__ import annotations

from datetime import datetime

import pytest

from pkpipe.steps import CODE, RunInfo, StepContext, StepError, StepResult
from tests.fakes import FakeClock
from tests.support.flows import make_ports, make_snapshot

SRC = "2-Areas/Journal/2026-10-30.md"


def _ctx(inputs=None, clock=None):
    snap = make_snapshot()

    def resolver(ref):
        from pkpipe.engine.expr import MISSING, lookup

        return lookup({"input": inputs or {}}, ref) if ref.startswith("input.") else MISSING

    return StepContext(
        flow=None,
        node=None,
        run=RunInfo("r", "f", "manual", snap.version),
        snapshot=snap,
        ports=make_ports(clock=clock),
        item=None,
        inputs={},
        resolver=resolver,
    )


def test_journal_is_located_by_argument_then_run_input_then_the_clock():
    clock = FakeClock(datetime(2026, 11, 1, 21, 30).timestamp())
    locate = CODE["improvement.locate_journal"]
    assert locate(_ctx(clock=clock), folder="2-Areas/Journal/") == {
        "path": "2-Areas/Journal/2026-11-01.md",
        "date": "2026-11-01",
    }
    assert locate(_ctx({"date": "2026-10-30"}, clock), folder="J")["path"] == "J/2026-10-30.md"
    assert locate(_ctx({"date": "2026-10-30"}, clock), folder="J", date="2026-10-29")["date"] == "2026-10-29"
    with pytest.raises(StepError, match="YYYY-MM-DD"):
        locate(_ctx({"date": "yesterday"}, clock), folder="J")


def test_section_found_flag_decides_whether_a_session_runs():
    section = CODE["improvement.section"]
    note = {"body": "## Improvement\n- Walk daily.\n## Other\nprivate"}
    out = section(_ctx(), note=note, path=SRC, date="2026-10-30")
    assert out == {"found": True, "text": "- Walk daily.", "path": SRC, "date": "2026-10-30"}
    assert section(_ctx(), note={"body": "private only"}, path=SRC, date="d")["found"] is False
    assert section(_ctx(), note=None, path=SRC, date="d")["found"] is False


def test_new_rows_skip_known_items_and_report_drops():
    rows = CODE["improvement.new_rows"]
    section = {"text": "- Walk daily.\n- Read more.", "path": SRC, "date": "2026-10-30"}
    from pkpipe.domain.improvement import item_id_for

    known = item_id_for(SRC, "Walk daily.")
    out = rows(
        _ctx(),
        section=section,
        extracted={
            "items": [
                {"statement": "Walk daily.", "area": "personal", "type": "habit", "success_criterion": ""},
                {"statement": "Read more.", "area": "learning", "type": "habit", "success_criterion": ""},
                {"statement": "Invented.", "area": "work", "type": "habit", "success_criterion": ""},
            ]
        },
        backlog={"rows": [{"id": known}]},
    )
    assert [r["statement"] for r in out["rows"]] == ["Read more."] and out["count"] == 1
    assert out["dropped"] == {"duplicate": 1, "not_in_journal": 1}
    assert rows(_ctx(), section=section, extracted=None, backlog=None)["count"] == 0


def test_select_review_collects_active_items_journal_paths_and_kept_system_items():
    rows = [
        {"id": "a", "status": "active", "date": "2026-10-30", "statement": "s", "area": "work"},
        {"id": "k", "status": "kept", "area": "system", "statement": "S", "source_note": SRC, "proposal": ""},
        {
            "id": "k2",
            "status": "kept",
            "area": "system",
            "statement": "S2",
            "source_note": SRC,
            "proposal": "x.md",
        },
        {"id": "p", "status": "kept", "area": "personal", "statement": "P", "source_note": SRC},
    ]
    out = CODE["improvement.select_review"](_ctx(), backlog={"rows": rows}, date="2026-11-01", max_days=28)
    assert [i["id"] for i in out["items"]] == ["a"] and out["count"] == 1
    assert out["journal_paths"] == [
        "2-Areas/Journal/2026-10-31.md",
        "2-Areas/Journal/2026-11-01.md",
    ]
    assert [p["id"] for p in out["proposals"]] == ["k"]
    assert out["proposals"][0]["path"] == "2-Areas/Self-Improvement/System-Changes/k.md"
    assert "eval gate" in out["proposals"][0]["body"]


def test_attach_journals_maps_note_paths_to_days():
    out = CODE["improvement.attach_journals"](
        _ctx(),
        items=[{"id": "a", "date": "2026-10-30"}],
        notes={"notes": [{"path": "2-Areas/Journal/2026-10-31.md", "body": "I walked."}], "missing": []},
        today="2026-11-01",
    )
    [item] = out["items"]
    assert item["journal_days"] == [{"date": "2026-10-31", "text": "I walked."}]
    assert "## 2026-10-31\nI walked." in item["journal"]


def test_propose_and_no_evidence_set_the_review_on_the_item_and_drop_the_journal():
    ev = [{"date": "2026-10-31", "quote": "I walked.", "signal": "supports"}]
    res = CODE["improvement.propose"](
        _ctx(), verdict={"verdict": "keep", "reason": "r", "next_experiment": "n"}, evidence=ev
    )
    assert isinstance(res, StepResult) and res.item["review"]["verdict"] == "keep"
    assert res.item["journal"] is None and res.item["journal_days"] is None
    none = CODE["improvement.no_evidence"](_ctx())
    assert none.item["review"]["verdict"] == "keep" and none.item["review"]["evidence"] == ""
    with pytest.raises(StepError, match="no valid verdict"):
        CODE["improvement.propose"](_ctx(), verdict={"verdict": "explode"}, evidence=ev)


def test_review_rows_leave_failed_items_alone_and_count_verdicts():
    ok = {"verdict": "adjust", "evidence": "e", "reason": "r", "next_experiment": "n"}
    out = CODE["improvement.review_rows"](
        _ctx(),
        results=[
            {"status": "done", "item": {"id": "a", "review": ok}},
            {"status": "failed", "item": {"id": "b"}},
            {"status": "done", "item": {"id": "c"}},
        ],
    )
    assert [r["id"] for r in out["rows"]] == ["a"] and out["failed"] == 2
    assert (out["keep"], out["adjust"], out["drop"]) == (0, 1, 0)


def test_proposal_rows_link_only_written_notes():
    out = CODE["improvement.proposal_rows"](
        _ctx(),
        results=[
            {"status": "done", "item": {"id": "a", "path": "p/a.md"}},
            {"status": "failed", "item": {"id": "b", "path": "p/b.md"}},
        ],
    )
    assert out["rows"] == [{"id": "a", "proposal": "p/a.md"}] and out["failed"] == 1


def test_summary_has_counts_and_location_but_no_statements():
    review = {"count": 2, "keep": 1, "adjust": 1, "drop": 0}
    out = CODE["improvement.summary"](_ctx(), review=review, proposals={"count": 1}, location="L.md")
    assert out["total"] == 3
    assert out["message"] == (
        "Weekly improvement review ready: 2 item(s) with evidence and a verdict proposal "
        "(1 keep, 1 adjust, 0 drop); 1 system change proposal(s) waiting for the eval gate. "
        "Confirm or change the status in L.md."
    )
    quiet = CODE["improvement.summary"](
        _ctx(), review={"count": 0, "keep": 0, "adjust": 0, "drop": 0}, proposals={"count": 0}
    )
    assert quiet == {"total": 0, "message": "", "reviewed": 0, "proposals": 0}
