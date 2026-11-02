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
        "count": 1,
        "retriage": 0,
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
    assert sorted(out["pending"]) == ["c", "d"] and out["unknown_category"] == ["d"]
    assert sorted((r["item_id"], r["state"]) for r in out["rows"]) == [
        ("a", "released"),
        ("b", "released"),
        ("c", "pending"),
        ("d", "pending"),
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
        _row("ov", override="no-such", state="pending"),
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
