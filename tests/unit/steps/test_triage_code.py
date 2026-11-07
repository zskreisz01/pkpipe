"""Spec 005 T004: deterministic code functions of the pack's triage flows (constitution V and VI)."""

from __future__ import annotations

from pkpipe.domain.triage import item_id_for
from pkpipe.steps import CODE, RunInfo, StepContext
from tests.support.flows import make_ports, make_snapshot


def _ctx(item=None):
    snap = make_snapshot()
    return StepContext(
        flow=None,
        node=None,
        run=RunInfo("r", "f", "manual", snap.version),
        snapshot=snap,
        ports=make_ports(),
        item=item,
        inputs={},
        resolver=lambda ref: None,
    )


def test_select_new_skips_managed_files_and_already_triaged_items():
    known = item_id_for("0-INBOX/old.md")
    out = CODE["inbox.select_new"](
        _ctx(),
        listing={"notes": ["old.md", "new.md", "Inbox-Triage.md", "Manual-Resource-Processing-List.md"]},
        triage={"rows": [{"item_id": known}]},
    )
    assert out == {
        "items": [
            {"id": item_id_for("0-INBOX/new.md"), "path": "0-INBOX/new.md", "resource": "0-INBOX/new.md"}
        ],
        "image_items": [],
        "owner_items": [],
        "count": 1,
        "image_count": 0,
        "retriage": 0,
        "needs_owner": 0,
    }


def test_check_category_accepts_only_configured_categories():
    """Constitution V: the model proposes, code decides; an invented category falls back to misc."""
    ok = CODE["triage.check_category"](_ctx(), suggestion={"category": "tool"})
    assert ok.item == {"category": "tool"} and ok.output["valid"] is True
    bad = CODE["triage.check_category"](_ctx(), suggestion={"category": "rm -rf"})
    assert bad.item == {"category": "misc"} and bad.output == {
        "category": "misc",
        "proposed": "rm -rf",
        "valid": False,
    }


def test_category_menu_exposes_ids_and_hints_only():
    menu = CODE["triage.category_menu"](_ctx())["categories"]
    assert menu[0] == {"id": "tool", "label": "Tools", "hint": "x"}


def test_suggested_rows_only_for_successful_items():
    results = [
        {"status": "done", "item": {"id": "a", "resource": "0-INBOX/a.md", "category": "tool"}},
        {"status": "failed", "item": {"id": "b", "resource": "0-INBOX/b.md"}},
    ]
    out = CODE["triage.suggested_rows"](_ctx(), results=results)
    assert out["rows"] == [
        {"item_id": "a", "resource": "0-INBOX/a.md", "ai_suggested_category": "tool", "state": "suggested"}
    ]
    assert out["count"] == 1 and out["failed"] == ["b"]


def _row(item_id, cat="tool", confirm=False, override=None, state="suggested"):
    return {
        "item_id": item_id,
        "resource": f"0-INBOX/{item_id}.md",
        "ai_suggested_category": cat,
        "confirm": confirm,
        "override": override,
        "state": state,
    }


def test_release_only_confirmed_or_overridden_items():
    """Constitution VI: unreviewed items stay pending; an override wins over the suggestion."""
    rows = [
        _row("a", confirm=True),
        _row("b", override="misc"),
        _row("c"),
        _row("d", override="no-such-category"),
        _row("e", confirm=True, state="processed"),
    ]
    out = CODE["triage.release"](_ctx(), triage={"rows": rows})
    released = {i["id"]: (i["category"], i["subflow"], i["para_path"]) for i in out["released"]}
    assert released == {
        "a": ("tool", "other", "3-Resources/Tools"),
        "b": ("misc", "other", "3-Resources/Misc"),
    }
    # spec 022: an unknown but plausible override ("no-such-category") is a new-category proposal, not pending
    assert out["pending"] == ["c"] and out["unknown_category"] == []
    assert [p["id"] for p in out["proposals"]] == ["d"]
    assert sorted((r["item_id"], r["state"]) for r in out["rows"]) == [
        ("a", "released"),
        ("b", "released"),
        ("c", "pending"),
    ]


def test_release_returns_a_leftover_released_item_with_a_removed_category_to_pending():
    """Spec 007 edge case: an item left ``released`` by a failed run whose category was removed afterwards
    is neither processed nor left stuck in ``released``; it goes back to the owner as pending."""
    rows = [
        _row("r", cat="removed-cat", confirm=True, state="released"),
        _row("ok", confirm=True, state="released"),
    ]
    out = CODE["triage.release"](_ctx(), triage={"rows": rows})
    assert [i["id"] for i in out["released"]] == ["ok"]
    assert out["pending"] == ["r"] and out["unknown_category"] == ["r"] and out["count_pending"] == 1
    assert out["rows"] == [{"item_id": "r", "state": "pending"}]


def test_finalize_marks_processed_quarantined_and_keeps_failed_released():
    results = [
        {
            "status": "done",
            "item": {"id": "a", "category": "tool", "resource": "r/a", "note": "3-Resources/Tools/a.md"},
        },
        {"status": "done", "item": {"id": "b", "category": "tool", "flagged": True}},
        {"status": "failed", "item": {"id": "c"}},
    ]
    out = CODE["triage.finalize"](_ctx(), results=results)
    assert out["triage_rows"] == [
        {"item_id": "a", "state": "processed"},
        {"item_id": "b", "state": "quarantined"},
    ]
    assert out["processing_rows"] == [
        {
            "item_id": "a",
            "category": "tool",
            "obsidian_link": "3-Resources/Tools/a.md",
            "nb_id": None,
            "link": None,
            "status": "done",
        }
    ]
    assert (out["processed"], out["quarantined"], out["failed"]) == (1, 1, 1)


def test_select_new_dedupes_and_flags_removed_category_for_retriage():
    """Spec 007 T005 / edge cases: one item per resource; an open item whose category was removed from the
    config is selected again, finished or decided items are not."""
    rows = [
        _row("gone1", cat="removed-cat", state="pending"),
        _row("fine", cat="tool", state="pending"),
        _row("done", cat="removed-cat", state="processed"),
        _row("decided", cat="removed-cat", override="misc", state="pending"),
        _row("confirmed", cat="removed-cat", confirm=True, state="pending"),
    ]
    out = CODE["inbox.select_new"](_ctx(), listing={"notes": ["n.md", "n.md"]}, triage={"rows": rows})
    assert [i["id"] for i in out["items"]] == [item_id_for("0-INBOX/n.md"), "gone1"]
    assert out["items"][1]["path"] == "0-INBOX/gone1.md" and out["retriage"] == 1 and out["count"] == 2


def test_backlog_counts_older_unreviewed_items_for_the_notification():
    """Spec 007 T003 / US2-AS1: the next notification mentions what still waits."""
    rows = [
        _row("new1"),
        _row("old1", state="pending"),
        _row("old2"),
        _row("ok", confirm=True, state="pending"),
        _row("ov", override="misc", state="pending"),
        _row("fin", state="processed"),
    ]
    out = CODE["triage.backlog"](_ctx(), triage={"rows": rows}, new_rows=[{"item_id": "new1"}])
    assert (out["new"], out["backlog"], out["invalid"], out["awaiting"]) == (1, 2, 0, 3)
    assert out["notify"] is True
    assert out["message"] == (
        "Triage ready: 1 new item(s) to confirm or override in 0-INBOX/Inbox-Triage.md. "
        "2 older item(s) still wait for your decision."
    )
    quiet = CODE["triage.backlog"](_ctx(), triage={"rows": []}, new_rows=[{"item_id": "x"}])
    assert quiet["backlog"] == 0 and "older" not in quiet["message"]


def test_backlog_reports_decisions_on_an_unconfigured_category_even_without_new_items():
    """Spec 007 edge case: a confirmed removed category or an unknown override is never released, so the
    owner is told to write a configured override; an unchanged unreviewed backlog alone sends nothing."""
    rows = [
        _row("conf", cat="removed-cat", confirm=True, state="pending"),
        _row("ov", override="no such " * 10, state="pending"),  # no plain name: the old dead end
        _row("old", state="pending"),
        _row("fin", cat="removed-cat", confirm=True, state="processed"),
    ]
    out = CODE["triage.backlog"](_ctx(), triage={"rows": rows}, new_rows=[])
    assert (out["new"], out["backlog"], out["invalid"], out["awaiting"]) == (0, 1, 2, 3)
    assert out["notify"] is True
    assert out["message"] == (
        "Triage: no new items in 0-INBOX/Inbox-Triage.md. 1 older item(s) still wait for your decision. "
        "2 item(s) name a category that is no longer configured: write a configured category into override."
    )
    only_old = CODE["triage.backlog"](_ctx(), triage={"rows": rows[2:]}, new_rows=[])
    assert only_old["notify"] is False and only_old["backlog"] == 1


def test_finalize_lists_a_failed_item_with_its_reason_in_the_link_column():
    """Spec 010 edge case: the processing list has no reason column, so a failed item's reason (a session
    error, no URL) is shown in `link` as one clean line; a real link is never replaced."""
    results = [
        {
            "status": "done",
            "item": {
                "id": "a",
                "category": "tool",
                "resource": "0-INBOX/a.md",
                "processing_status": "failed",
                "failure_reason": "session 'synthesize' failed:\nSchemaError " + "x" * 300,
            },
        },
        {
            "status": "done",
            "item": {
                "id": "b",
                "category": "learning-material",
                "resource": "0-INBOX/b.md",
                "processing_status": "failed",
                "failure_reason": "timeout",
                "link": "http://127.0.0.1/nb-b",
            },
        },
        {
            "status": "done",
            "item": {
                "id": "c",
                "category": "tool",
                "resource": "0-INBOX/c.md",
                "note": "3-Resources/Tools/c.md",
                "failure_reason": "stale",
            },
        },
    ]
    rows = {r["item_id"]: r for r in CODE["triage.finalize"](_ctx(), results=results)["processing_rows"]}
    assert rows["a"]["status"] == "failed" and "\n" not in rows["a"]["link"] and len(rows["a"]["link"]) == 200
    assert rows["a"]["link"].startswith("session 'synthesize' failed: SchemaError")
    assert rows["b"]["link"] == "http://127.0.0.1/nb-b"  # an existing link stays
    assert rows["c"]["status"] == "done" and rows["c"]["link"] is None  # a reason only matters when failed


# -- spec 022: normalised overrides and new-category proposals -----------------------------------------------


def test_release_normalises_an_override_that_is_a_variant_of_a_configured_category():
    rows = [_row("a", override="Misc"), _row("b", override=" MISC "), _row("c", override="Tools")]
    out = CODE["triage.release"](_ctx(), triage={"rows": rows})
    assert {i["id"]: i["category"] for i in out["released"]} == {"a": "misc", "b": "misc", "c": "tool"}
    assert out["proposals"] == [] and out["unknown_category"] == []


def test_release_turns_an_unknown_but_plausible_override_into_a_proposal_that_keeps_its_state():
    rows = [_row("d", override="Children"), _row("e", override="no such category at all " * 5)]
    out = CODE["triage.release"](_ctx(), triage={"rows": rows})
    [p] = out["proposals"]
    assert p["id"] == "d" and p["requested_category"] == "children" and p["resource"] == "0-INBOX/d.md"
    assert "category" not in p and "subflow" not in p and "para_path" not in p  # nothing is filed
    assert out["released"] == [] and out["count_proposals"] == 1
    assert out["pending"] == ["e"] and out["unknown_category"] == ["e"]  # no plain name: the old dead end
    assert out["rows"] == [{"item_id": "e", "state": "pending"}]  # the proposal row is written by its flow


def test_a_suggestion_that_was_removed_is_never_a_proposal_only_the_owners_words_are():
    rows = [_row("r", cat="removed-cat", confirm=True)]
    out = CODE["triage.release"](_ctx(), triage={"rows": rows})
    assert out["proposals"] == [] and out["unknown_category"] == ["r"] and out["pending"] == ["r"]


def test_an_item_that_awaits_a_category_is_left_alone_until_the_override_names_a_configured_one():
    rows = [
        _row("w1", override="children", state="awaiting_category"),
        _row("w2", override="misc", state="awaiting_category"),
        _row("w3", override="Misc", state="awaiting_category"),
        _row("w4", state="awaiting_category"),
    ]
    out = CODE["triage.release"](_ctx(), triage={"rows": rows})
    assert out["proposals"] == []  # no second evaluation, no second note
    assert sorted(i["id"] for i in out["released"]) == ["w2", "w3"]
    assert {r["item_id"]: r["state"] for r in out["rows"]} == {"w2": "released", "w3": "released"}
    assert out["count_awaiting"] == 2  # w1 and w4 still wait


def test_backlog_says_which_new_categories_the_owner_proposes_and_keeps_invalid_for_the_rest():
    rows = [
        _row("p1", override="children", state="pending"),
        _row("p2", override="Gyerek Programok", state="awaiting_category"),
        _row("p3", override="children", state="awaiting_category"),
        _row("conf", cat="removed-cat", confirm=True, state="pending"),
        _row("junk", override="x " * 40, state="pending"),
        _row("fine", override="Misc", state="pending"),  # a variant of a configured id: nothing to say
    ]
    out = CODE["triage.backlog"](_ctx(), triage={"rows": rows}, new_rows=[])
    assert out["proposed"] == 3 and out["invalid"] == 2
    assert "3 item(s) propose a new category: children, gyerek-programok" in out["message"]
    assert "2 item(s) name a category that is no longer configured" in out["message"]
    assert out["notify"] is True


def test_the_proposal_list_in_the_message_is_short_plain_slugs_only():
    rows = [_row(f"p{i}", override=f"Cat{i}x", state="pending") for i in range(12)]
    msg = CODE["triage.backlog"](_ctx(), triage={"rows": rows}, new_rows=[])["message"]
    assert "12 item(s) propose a new category: cat0x, cat1x, cat2x, cat3x, cat4x" in msg and "..." in msg
    assert "cat11x" not in msg


# -- spec 023: the comment becomes the declared input owner_note ------------------------------------------
def test_release_hands_the_comment_on_as_a_cleaned_capped_owner_note_for_released_items_and_proposals():
    rows = [
        {
            "item_id": "a1",
            "resource": "0-INBOX/a.md",
            "ai_suggested_category": "tool",
            "confirm": True,
            "state": "suggested",
            "comment": "  focus on\nthe  licence \x00 ​ ",
        },
        {
            "item_id": "b2",
            "resource": "0-INBOX/b.md",
            "ai_suggested_category": "tool",
            "override": "children",
            "state": "suggested",
            "comment": "for the kids " + "x" * 2000,
        },
        {
            "item_id": "c3",
            "resource": "0-INBOX/c.md",
            "ai_suggested_category": "tool",
            "confirm": True,
            "state": "suggested",
            "comment": "   ",
        },
        {
            "item_id": "d4",
            "resource": "0-INBOX/d.md",
            "ai_suggested_category": "tool",
            "state": "suggested",
            "comment": "waiting rows are not released: no note anywhere",
        },
    ]
    out = CODE["triage.release"](_ctx(), triage={"rows": rows})
    released = {r["id"]: r for r in out["released"]}
    assert released["a1"]["owner_note"] == "focus on the licence"
    assert "owner_note" not in released["c3"]  # a blank comment is no note
    [proposal] = out["proposals"]
    assert (
        proposal["id"] == "b2"
        and proposal["owner_note"].startswith("for the kids x")
        and len(proposal["owner_note"]) == 1000
    )
    assert "waiting rows" not in repr(out)  # only released items and proposals carry a note


def test_the_notifications_never_quote_a_comment():
    rows = [
        {
            "item_id": "a1",
            "resource": "0-INBOX/a.md",
            "ai_suggested_category": "tool",
            "state": "suggested",
            "comment": "CANARY-NOTE",
        },
        {
            "item_id": "b2",
            "resource": "0-INBOX/b.md",
            "ai_suggested_category": "tool",
            "state": "suggested",
            "override": "children",
            "comment": "CANARY-NOTE",
        },
    ]
    out = CODE["triage.backlog"](_ctx(), triage={"rows": rows}, new_rows=[])
    assert "CANARY-NOTE" not in repr(out)
    rel = CODE["triage.release"](_ctx(), triage={"rows": rows})
    assert "CANARY-NOTE" not in rel["proposal_note"]


def _ctx_attempts(counts):
    from tests.fakes import FakeProposalAttempts

    ctx = _ctx()
    ctx.ports.extra["proposal_attempts"] = FakeProposalAttempts(counts)
    return ctx


def test_release_does_not_propose_again_after_the_cap_and_names_it_as_gave_up():
    rows = [_row("d", override="Children"), _row("f", override="Kids")]
    ctx = _ctx_attempts({"d": ("children", 3), "f": ("kids", 2)})
    out = CODE["triage.release"](ctx, triage={"rows": rows})
    assert [p["id"] for p in out["proposals"]] == ["f"]  # two failures so far: one more try
    assert out["proposal_gave_up"] == [{"id": "d", "requested_category": "children"}]
    assert out["count_gave_up"] == 1 and out["pending"] == ["d"] and out["unknown_category"] == []
    assert "'children' failed 3 times and is not retried" in out["proposal_note"]
    assert out["rows"] == [{"item_id": "d", "state": "pending"}]


def test_the_cap_counts_per_requested_name_so_a_changed_override_is_proposed():
    out = CODE["triage.release"](
        _ctx_attempts({"d": ("children", 3)}), triage={"rows": [_row("d", override="Kids")]}
    )
    assert [p["requested_category"] for p in out["proposals"]] == ["kids"] and out["count_gave_up"] == 0


def test_backlog_counts_a_gave_up_proposal_for_the_owner_and_notifies():
    rows = [_row("d", override="children", state="pending")]
    out = CODE["triage.backlog"](_ctx_attempts({"d": ("children", 3)}), triage={"rows": rows}, new_rows=[])
    assert out["proposal_gave_up"] == 1 and out["proposed"] == 0 and out["awaiting"] == 1 and out["notify"]
    assert "'children' failed 3 times and is not retried" in out["message"]


def test_the_failed_and_recorded_steps_count_and_clear_the_attempts():
    from tests.fakes import FakeProposalAttempts

    ctx = _ctx({"id": "d", "requested_category": "children"})
    ctx.ports.extra["proposal_attempts"] = store = FakeProposalAttempts()
    assert CODE["category.proposal_failed"](ctx).item["proposal_attempts"] == 1
    assert CODE["category.proposal_failed"](ctx).item["proposal_attempts"] == 2
    assert CODE["category.proposal_recorded"](ctx).item["state"] == "awaiting_category"
    assert store.counts == {}


def test_without_the_port_the_steps_still_work_and_nothing_is_capped():
    ctx = _ctx({"id": "d", "requested_category": "children"})
    assert CODE["category.proposal_failed"](ctx).item["processing_status"] == "failed"
    out = CODE["triage.release"](_ctx(), triage={"rows": [_row("d", override="Children")]})
    assert len(out["proposals"]) == 1
