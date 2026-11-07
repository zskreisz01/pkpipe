"""Spec 024: the code functions of the run report and the ``report`` config section."""

from __future__ import annotations

import pytest

from pkpipe.engine.config_models import ReportConfig, validate_effective
from pkpipe.steps import CODE, RunInfo, StepContext
from tests.fakes import FakeClock
from tests.support.flows import make_ports, make_snapshot

CATEGORIES = [
    {"id": "tool", "label": "Tools", "subflow": "other_resource", "para_path": "{resources_dir}/Tools"},
    {
        "id": "learning-material",
        "label": "Learning",
        "subflow": "learning_material",
        "para_path": "{resources_dir}/Learning",
    },
    {"id": "misc", "label": "Misc", "subflow": "other_resource", "para_path": "{resources_dir}/Misc"},
]
NOON = (
    1_794_052_800.0  # a fixed instant; the date part is read in the local zone, so tests only compare shape
)


def _ctx(item=None, resolved=None, **config):
    snap = make_snapshot(categories=CATEGORIES, **config)
    return StepContext(
        flow=None,
        node=None,
        run=RunInfo("399cb7d7-1c7f", "f", "schedule", snap.version),
        snapshot=snap,
        ports=make_ports(clock=FakeClock(NOON)),
        item=item,
        inputs={},
        resolver=lambda ref: (resolved or {}).get(ref),
    )


def _row(item_id, cat="tool", confirm=False, override=None, state="suggested", **extra):
    return {
        "item_id": item_id,
        "ai_suggested_category": cat,
        "confirm": confirm,
        "override": override,
        "state": state,
        **extra,
    }


def test_triage_counts_split_the_owner_todo_from_the_notebook_todo():
    from pkpipe.steps.report_code import triage_counts

    rows = [
        _row("a"),  # unreviewed: approval
        _row("b", state="pending"),  # unreviewed: approval
        _row("c", cat="removed-cat", confirm=True),  # decided on a category that no longer exists: approval
        _row("d", state="awaiting_category", override="children"),  # proposed, not configured: approval
        _row("e", state="awaiting_category", override="misc"),  # configured now: released next run
        _row("f", cat="learning-material", confirm=True),  # decided learning item: notebook todo
        _row("g", cat="tool", override="Learning-Material"),  # override resolves to a learning category
        _row("h", cat="learning-material", confirm=True, state="released"),  # failed before: notebook todo
        _row("i", cat="learning-material", confirm=True, state="processed"),  # done
        _row("j", cat="learning-material", state="quarantined"),
        _row("k", cat="tool", confirm=True),  # decided, not a notebook
        _row("l", state="nonsense"),
        _row("m", state="processed", override="!!! not a name !!!"),  # finished: never a todo
    ]
    assert triage_counts(_ctx(), rows) == {"approval": 4, "notebooks": 3}
    assert triage_counts(_ctx(), []) == {"approval": 0, "notebooks": 0}


def test_settings_gives_the_switch_the_legacy_flag_and_the_item_of_the_subflow():
    ctx = _ctx(
        report={"enabled": True, "tool_category": "tool"},
        triage={"surface": "web"},
        ui={"public_url": "http://100.64.0.10:8787"},
    )
    out = CODE["report.settings"](
        ctx,
        kind="process_released",
        notify=True,
        results=[{"status": "done", "item": {"category": "tool", "note": "n.md"}}],
        release={"count_proposals": 1, "proposal_note": " 1 item(s) propose a new category: children."},
        list_report={"note": " 1 processed item(s) no longer in the INBOX list (not_found)."},
    )
    assert out["enabled"] is True and out["legacy_notify"] is False
    item = out["item"]
    assert item["kind"] == "process_released" and item["summary"]["by_category"] == {"tool": 1}
    assert item["summary"]["proposals"] == 1 and item["tool_folder"] == "3-Resources/Tools"
    assert item["surface"] == "web" and item["public_url"] == "http://100.64.0.10:8787"
    assert item["notes"] == [
        "1 item(s) propose a new category: children.",
        "1 processed item(s) no longer in the INBOX list (not_found).",
    ]


def test_with_the_report_off_the_earlier_message_is_asked_for_when_the_run_had_one():
    off = _ctx(report={"enabled": False})
    out = CODE["report.settings"](off, kind="triage", notify=True, new_rows=[], backlog={})
    assert out["enabled"] is False and out["legacy_notify"] is True
    quiet = CODE["report.settings"](off, kind="triage", notify=False, new_rows=[], backlog={})
    assert quiet["legacy_notify"] is False  # the earlier "no nagging" rule


def test_the_triage_summary_counts_suggestions_without_the_quarantined_rows():
    rows = [
        {"item_id": "a", "ai_suggested_category": "tool", "state": "suggested"},
        {"item_id": "b", "ai_suggested_category": "none", "state": "quarantined"},
    ]
    out = CODE["report.settings"](
        _ctx(), kind="triage", notify=False, new_rows=rows, backlog={"quarantined": 1}
    )
    assert out["item"]["summary"]["new_by_category"] == {"tool": 1}
    assert out["item"]["summary"]["quarantined"] == 1


def test_run_summary_builds_text_todo_lines_and_the_journal_call():
    item = {
        "kind": "process_released",
        "summary": {"by_category": {"tool": 2}, "processed": 2, "evaluations": 2},
        "notes": [],
        "tool_folder": "3-Resources/Tools",
        "tool_category": "tool",
        "journal": True,
        "heading": "## pkpipe todo",
        "surface": "web",
        "public_url": "http://100.64.0.10:8787",
        "triage_file": "1-Inbox/Inbox-Triage.md",
        "processing_list": "1-Inbox/Manual-Resource-Processing-List.md",
    }
    triage = {"rows": [_row("a"), _row("b", cat="learning-material", confirm=True)]}
    ctx = _ctx(item=item, resolved={"tool_notes": {"count": 2, "newest": "3-Resources/Tools/x-abc.md"}})
    out = CODE["report.run_summary"](ctx, triage=triage)
    day = out["date"]
    assert out["lines"] == [
        f"- {day} - Review triage decisions - 1 item - [triage UI](http://100.64.0.10:8787/triage)",
        f"- {day} - Process notebook materials and podcasts - 1 item - "
        "[[1-Inbox/Manual-Resource-Processing-List]]",
        f"- {day} - Read tool evaluations - 2 items - [[3-Resources/Tools/x-abc]]",
    ]
    assert out["todos"] == {"approval": 1, "notebooks": 1, "tool_reads": 2}
    assert out["write_journal"] is True and out["heading"] == "## pkpipe todo"
    assert out["title"] == "Release run" and out["awaiting"] == 1 and out["processed"] == 2
    assert "**Processed (2):** tool 2" in out["text"] and "**Todo**" in out["text"]
    assert "run 399cb7d7" in out["text"]


def test_run_summary_without_the_tool_count_still_reports():
    item = {
        "kind": "triage",
        "summary": {"new_by_category": {}},
        "journal": False,
        "surface": "vault",
        "triage_file": "1-Inbox/Inbox-Triage.md",
        "processing_list": "1-Inbox/List.md",
    }
    out = CODE["report.run_summary"](_ctx(item=item), triage={"rows": []})
    assert out["lines"] == [] and out["write_journal"] is False and "**Todo:** nothing open" in out["text"]


def test_the_report_never_carries_item_text_or_urls():
    secret = "SECRET-TITLE https://private.example/path?token=abc"
    item = {
        "kind": "process_released",
        "summary": {"by_category": {"tool": 1}, "processed": 1},
        "notes": [],
        "journal": True,
        "heading": "## pkpipe todo",
        "surface": "vault",
        "triage_file": "1-Inbox/Inbox-Triage.md",
        "processing_list": "1-Inbox/List.md",
    }
    rows = [_row("a", resource=secret, topic=secret, comment=secret)]
    out = CODE["report.run_summary"](_ctx(item=item), triage={"rows": rows})
    assert "SECRET" not in repr(out) and "private.example" not in repr(out)


# -- config ---------------------------------------------------------------------------------------------
def test_the_report_config_defaults_the_report_on_and_the_journal_off():
    cfg = ReportConfig()
    assert cfg.enabled is True and cfg.tool_category == "tool"
    assert cfg.journal_todos.enabled is False and cfg.journal_todos.heading == "## pkpipe todo"


@pytest.mark.parametrize(
    "bad",
    [
        {"enabled": "maybe"},
        {"tool_category": "Tool Evaluations"},
        {"journal_todos": {"heading": "no hash"}},
        {"journal_todos": {"heading": "## <b>"}},
        {"journal_todos": {"enabled": True, "extra": 1}},
        {"unknown": 1},
    ],
)
def test_a_bad_report_section_is_a_config_error_naming_the_file(bad):
    errors = validate_effective({"report": bad})
    assert errors and all(e.startswith("report.json:") for e in errors)


def test_a_good_report_section_validates():
    ok = {
        "enabled": True,
        "tool_category": "tool",
        "journal_todos": {"enabled": True, "heading": "### My todo"},
    }
    assert validate_effective({"report": ok}) == []
