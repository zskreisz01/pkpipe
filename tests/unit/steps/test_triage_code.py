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
