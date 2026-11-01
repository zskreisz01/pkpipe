"""Spec 015 FR-001, SC-001: the improvement rules in code (status, grounding, evidence, verdict)."""

from __future__ import annotations

from datetime import date

import pytest

from pkpipe.domain import improvement as imp

DAY = date(2026, 10, 30)
SECTION = (
    "- Stretch for ten minutes every morning.\n- Go to bed before 23:00. I will check it in my sleep log."
)


def _item(statement, area="personal", kind="habit", criterion=""):
    return {"statement": statement, "area": area, "type": kind, "success_criterion": criterion}


# -- FR-001: only the owner changes a status ----------------------------------------------------------------
@pytest.mark.parametrize("new", [s for s in imp.Status if s is not imp.Status.PROPOSED])
def test_the_pipeline_never_changes_a_status(new):
    """FR-001: every status change needs the owner, whatever the pair."""
    for old in imp.Status:
        reason = imp.check_transition(old, new, owner_decided=False)
        if old is not new:
            assert reason, f"{old} -> {new} must need the owner"


def test_owner_transitions_follow_the_lifecycle():
    ok = imp.check_transition
    assert ok(imp.Status.PROPOSED, imp.Status.ACTIVE, owner_decided=True) is None
    assert ok(imp.Status.ACTIVE, imp.Status.REVIEWING, owner_decided=True) is None
    assert ok(imp.Status.REVIEWING, imp.Status.KEPT, owner_decided=True) is None
    assert ok(imp.Status.ADJUSTED, imp.Status.ACTIVE, owner_decided=True) is None
    assert "not allowed" in ok(imp.Status.PROPOSED, imp.Status.KEPT, owner_decided=True)
    assert "not allowed" in ok(imp.Status.ACTIVE, imp.Status.PROPOSED, owner_decided=True)
    assert "not allowed" in ok(imp.Status.DROPPED, imp.Status.ACTIVE, owner_decided=True)
    assert ok(imp.Status.ACTIVE, imp.Status.ACTIVE, owner_decided=False) is None  # repeating is not a change


# -- text helpers -------------------------------------------------------------------------------------------
def test_item_id_is_stable_and_ignores_markers_whitespace_and_case():
    a = imp.item_id_for("2-Areas/Journal/2026-10-30.md", "- Stretch  for ten minutes.")
    assert a == imp.item_id_for("2-Areas/Journal/2026-10-30.md", "stretch for ten minutes.")
    assert a != imp.item_id_for("2-Areas/Journal/2026-10-31.md", "stretch for ten minutes.")
    assert len(a) == 12


def test_extract_section_stops_at_the_next_heading_of_the_same_or_higher_level():
    body = "# Day\n\nintro\n\n## Improvement\n\n- a\n### Sub\n- b\n\n## Gratitude\n- c\n"
    assert imp.extract_section(body) == "- a\n### Sub\n- b"
    assert imp.extract_section(body, "Gratitude") == "- c"


@pytest.mark.parametrize(
    "body",
    [
        "",
        "no headings",
        "## Improvements\n\n\n## Next",
        "## Improvement ideas\n- x",
        "```\n## Improvement\n- x\n```",
    ],
)
def test_extract_section_is_empty_without_a_real_section(body):
    assert imp.extract_section(body) == ""


def test_extract_section_accepts_plural_and_any_level():
    assert imp.extract_section("### Improvements\n- x\n") == "- x"


# -- SC-001: no invented items ------------------------------------------------------------------------------
def test_only_verbatim_statements_become_items():
    out = imp.ground_items(
        [
            _item("Stretch for ten minutes every morning."),
            _item("Meditate daily."),  # invented
            _item("Stretch ten min each morning."),  # paraphrased
        ],
        section=SECTION,
        source_note="2-Areas/Journal/2026-10-30.md",
        day=DAY,
    )
    assert [i["statement"] for i in out.items] == ["Stretch for ten minutes every morning."]
    assert out.dropped == {"not_in_journal": 2}
    assert out.items[0]["status"] == "proposed" and out.items[0]["date"] == "2026-10-30"


def test_a_statement_is_recognised_across_markers_and_case():
    out = imp.ground_items(
        [_item("- stretch FOR ten minutes   every morning.")], section=SECTION, source_note="n.md", day=DAY
    )
    assert out.items[0]["statement"] == "stretch FOR ten minutes every morning."


def test_success_criterion_is_kept_only_when_verbatim():
    out = imp.ground_items(
        [
            _item("Go to bed before 23:00.", criterion="I will check it in my sleep log."),
            _item("Stretch for ten minutes every morning.", criterion="feel 20% better"),
        ],
        section=SECTION,
        source_note="n.md",
        day=DAY,
    )
    assert [i["success_criterion"] for i in out.items] == ["I will check it in my sleep log.", ""]


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ({"statement": "", "area": "work", "type": "habit"}, "bad_statement"),
        (
            {"statement": "Stretch for ten minutes every morning.", "area": "hobby", "type": "habit"},
            "bad_enum",
        ),
        ({"statement": "Stretch for ten minutes every morning.", "area": "work", "type": "plan"}, "bad_enum"),
        ("Stretch for ten minutes every morning.", "malformed"),
    ],
)
def test_malformed_proposals_are_dropped_and_counted(raw, reason):
    out = imp.ground_items([raw], section=SECTION, source_note="n.md", day=DAY)
    assert out.items == [] and out.dropped == {reason: 1}


def test_duplicates_existing_items_and_the_limit():
    first = imp.item_id_for("n.md", "Stretch for ten minutes every morning.")
    out = imp.ground_items(
        [_item("Stretch for ten minutes every morning."), _item("Go to bed before 23:00.")],
        section=SECTION,
        source_note="n.md",
        day=DAY,
        existing_ids={first},
    )
    assert [i["statement"] for i in out.items] == ["Go to bed before 23:00."]
    assert out.dropped == {"duplicate": 1}
    twice = imp.ground_items(
        [_item("Stretch for ten minutes every morning.")] * 2, section=SECTION, source_note="n.md", day=DAY
    )
    assert len(twice.items) == 1 and twice.dropped == {"duplicate": 1}
    capped = imp.ground_items(
        [_item("Stretch for ten minutes every morning."), _item("Go to bed before 23:00.")],
        section=SECTION,
        source_note="n.md",
        day=DAY,
        max_items=1,
    )
    assert len(capped.items) == 1 and capped.dropped == {"over_limit": 1}


def test_non_list_reply_yields_nothing():
    assert imp.ground_items(None, section=SECTION, source_note="n.md", day=DAY).items == []  # type: ignore[arg-type]


# -- weekly review ------------------------------------------------------------------------------------------
def test_review_selects_only_active_and_reviewing_items_and_a_bounded_window():
    rows = [
        {"id": "a", "status": "active", "date": "2026-10-20"},
        {"id": "b", "status": "reviewing", "date": "2026-01-01"},
        {"id": "c", "status": "proposed", "date": "2026-10-29"},
        {"id": "d", "status": "kept", "date": "2026-10-29"},
    ]
    sel = imp.select_review(rows, date(2026, 11, 1), max_days=7)
    assert [r["id"] for r in sel["items"]] == ["a", "b"]
    assert sel["days"][0] == date(2026, 10, 26) and sel["days"][-1] == date(2026, 11, 1)  # b capped to 7 days
    assert imp.select_review([rows[0]], date(2026, 11, 1))["days"][0] == date(2026, 10, 21)  # day after item
    assert imp.select_review([{**rows[0], "date": "2026-11-01"}], date(2026, 11, 1))["days"] == []
    assert imp.select_review(rows[2:], date(2026, 11, 1))["days"] == []  # nothing to review: nothing to read


def test_journals_are_attached_after_the_item_was_written_newest_kept_when_too_long():
    item = {"id": "a", "date": "2026-10-30"}
    notes = {
        "2026-10-29": "before it started",
        "2026-10-30": "start",
        "2026-10-31": "x" * 20000,
        "2026-11-01": "newest",
    }
    [out] = imp.attach_journals([item], notes, date(2026, 11, 1))
    days = [d["date"] for d in out["journal_days"]]
    assert (
        "2026-10-29" not in days and "2026-10-30" not in days
    )  # the item's own day holds only the statement
    assert days[-1] == "2026-11-01"
    assert len(out["journal"]) < imp.ITEM_JOURNAL_CHARS + 200
    assert out["journal"].startswith("## ") and "## 2026-11-01\nnewest" in out["journal"]


def test_evidence_must_be_verbatim_in_a_journal_day_and_gets_that_day():
    days = [
        {"date": "2026-10-30", "text": "I stretched today.\nIt felt good."},
        {"date": "2026-10-31", "text": "Skipped it."},
    ]
    out = imp.verify_evidence(
        [
            {"quote": "I stretched today.", "signal": "supports"},
            {"quote": "skipped   it.", "signal": "contradicts"},
            {"quote": "I ran a marathon.", "signal": "supports"},
            {"quote": "It felt good.", "signal": "great"},
            {"quote": "I stretched today.", "signal": "supports"},
            "junk",
        ],
        days,
    )
    assert out["evidence"] == [
        {"date": "2026-10-30", "quote": "I stretched today.", "signal": "supports"},
        {"date": "2026-10-31", "quote": "skipped it.", "signal": "contradicts"},
    ]
    assert out["count"] == 2 and out["dropped"] == 4


def test_without_evidence_the_verdict_is_keep_whatever_the_model_says():
    fields = imp.verdict_fields({"verdict": "drop", "reason": "x", "next_experiment": "y"}, [])
    assert fields["verdict"] == "keep" and fields["evidence"] == ""


def test_verdict_fields_and_invalid_verdict():
    ev = [{"date": "2026-10-30", "quote": "q", "signal": "supports"}]
    fields = imp.verdict_fields({"verdict": "Adjust", "reason": " a  b ", "next_experiment": "c"}, ev)
    assert fields == {
        "evidence": '2026-10-30 (+) "q"',
        "verdict": "adjust",
        "reason": "a b",
        "next_experiment": "c",
    }
    with pytest.raises(ValueError):
        imp.verdict_fields({"verdict": "delete"}, ev)


def test_proposal_note_names_the_eval_gate_and_links_the_source():
    note = imp.proposal_note(
        {
            "id": "abc",
            "statement": "Shorten the triage instruction.",
            "source_note": "2-Areas/Journal/d.md",
            "area": "system",
            "type": "system-change",
            "evidence": "e",
            "next_experiment": "n",
        }
    )
    assert "eval gate" in note and "[[2-Areas/Journal/d]]" in note and "Shorten the triage" in note


def test_item_model_blank_optional_cells():
    item = imp.ImprovementItem(
        id="a",
        source_note="n.md",
        date="2026-10-30",
        statement="s",
        area="work",
        type="habit",
        review_date="",
        verdict="",
    )
    assert item.review_date is None and item.verdict is None and item.status is imp.Status.PROPOSED
