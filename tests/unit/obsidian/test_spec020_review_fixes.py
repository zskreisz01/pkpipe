"""Spec 020 review fixes: archive placement, header rule, one day per call, layout reach of the parser
settings, source choice by ``inbox_list_file``, write destinations of both layouts."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from pkpipe.domain.inbox_list import list_items, parse_list
from pkpipe.domain.layout import DEFAULT_LAYOUT, VaultLayout, layout_structure_errors
from pkpipe.obsidian.errors import PathRejected
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault
from pkpipe.steps import CODE, RunInfo, StepContext
from tests.support.flows import make_ports, make_snapshot

REAL = VaultLayout(
    inbox_dir="1-Inbox",
    triage_file="1-Inbox/Inbox-Triage.md",
    processing_list="1-Inbox/Manual-Resource-Processing-List.md",
    archive_dir="6-Archive/Inbox",
    inbox_archive_pattern="6-Archive/Inbox/Inbox-Archive-{yyyy-MM-dd}.md",
    quarantine_dir="1-Inbox/_quarantine",
    attachments_dir="1-Inbox/attachments",
    areas_dir="3-Areas",
    projects_dir="2-Projects",
    resources_dir="4-Resources",
    agent_tasks_dir="3-Areas/Agent-Tasks",
    tool_candidates_file="3-Areas/System/System-Tool-Candidates.md",
    improvement_file="3-Areas/Self-Improvement/Improvement-Backlog.md",
    proposals_dir="3-Areas/Self-Improvement/System-Changes",
    context_review_dir="3-Areas/Context-Review",
    journal_pattern="3-Areas/Journal/{yyyy-MM-dd}.md",
    inbox_list_file="1-Inbox/INBOX.md",
)


# -- archive placement -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        "0-INBOX/Arch-{yyyy-MM-dd}.md",
        "2-Areas/Arch-{yyyy-MM-dd}.md",
        "1-Projects/x/Arch.md",
        "3-Resources/{yyyy}/Arch.md",
    ],
)
def test_archive_inside_a_writable_or_inbox_folder_is_rejected(pattern):
    with pytest.raises(ValidationError, match="writable or INBOX folder"):
        VaultLayout(inbox_list_file="0-INBOX/INBOX.md", inbox_archive_pattern=pattern)


def test_archive_below_the_archive_root_is_accepted_and_not_writable(tmp_path):
    layout = VaultLayout(inbox_list_file="0-INBOX/INBOX.md")
    (tmp_path / "4-Archive/Inbox").mkdir(parents=True)
    vault = Vault(VaultPolicy(vault_dir=tmp_path, layout=layout.model_dump()))
    with pytest.raises(PathRejected):
        vault.write_note(layout.inbox_archive_path(date(2026, 11, 4)), "x", overwrite=True)


# -- header rule -----------------------------------------------------------------------------------------


def test_a_rule_between_entries_of_a_headerless_list_drops_nothing():
    entries = parse_list("- a1\n- a2\n---\n- b1\n")
    assert [e.raw for e in entries] == ["- a1", "- a2", "- b1"]


def test_a_rule_before_the_first_bullet_still_ends_the_header():
    entries = parse_list("# Inbox\nintro\n---\n- a\n---\n- b\n")
    assert [e.raw for e in entries] == ["- a", "- b"]


# -- one day per call ------------------------------------------------------------------------------------


def test_inbox_remove_entries_reads_the_day_once(tmp_path):
    (tmp_path / "0-INBOX").mkdir()
    (tmp_path / "4-Archive/Inbox").mkdir(parents=True)
    (tmp_path / "0-INBOX/INBOX.md").write_text("# I\n---\n- a\n- b\n", encoding="utf-8")
    calls = []

    def today():
        calls.append(1)
        return date(2026, 11, 4) + timedelta(days=len(calls) - 1)

    policy = VaultPolicy(vault_dir=tmp_path, layout={"inbox_list_file": "0-INBOX/INBOX.md"})
    out = Vault(policy, today=today).inbox_remove_entries(["- a", "- b"])
    assert len(calls) == 1
    assert out["archive"].endswith("2026-11-04.md")
    text = (tmp_path / out["archive"]).read_text(encoding="utf-8")
    assert "2026-11-04" in text and "- a" in text and "- b" in text


# -- parser settings reach the parse through the layout ----------------------------------------------------


def _parse(layout: VaultLayout, text: str):
    return parse_list(text, layout.inbox_list.to_config())


def test_layout_settings_change_the_parse_end_to_end():
    text = "# T\n---\n+ plus https://x.example/?utm_source=a&id=1\n- dash https://y.example/?ref=z&fbclid=q\n"
    default = _parse(VaultLayout(inbox_list_file="I.md"), text)
    assert len(default) == 1 and default[0].raw.startswith("- dash")  # '+' is a continuation of nothing
    custom = _parse(
        VaultLayout(
            inbox_list_file="I.md",
            inbox_list={"bullet_markers": ["+"], "strip_params": ["ref"]},
        ),
        text,
    )
    assert [e.resource for e in custom] == ["https://x.example/?utm_source=a&id=1"]
    assert custom[0].raw.endswith("id=1\n- dash https://y.example/?ref=z&fbclid=q")  # '-' continues '+'


def test_comments_ignored_false_keeps_a_comment_as_text():
    text = "# T\n---\n- a\n<!-- note -->\n- b\n"
    on = _parse(VaultLayout(inbox_list_file="I.md"), text)
    off = _parse(VaultLayout(inbox_list_file="I.md", inbox_list={"comments_ignored": False}), text)
    assert [e.raw for e in on] == ["- a", "- b"]
    assert [e.raw for e in off] == ["- a\n<!-- note -->", "- b"]


# -- the layout alone chooses the source -------------------------------------------------------------------


def _ctx(layout: VaultLayout, parsed):
    snap = make_snapshot()
    snap.data["layout"] = layout.model_dump()
    return StepContext(
        flow=None,
        node=None,
        run=RunInfo("r", "f", "manual", snap.version),
        snapshot=snap,
        ports=make_ports(),
        item=None,
        inputs={},
        resolver=lambda ref: parsed if ref == "parse_list" else None,
    )


def _select(layout, notes, parsed, **kw):
    ctx = _ctx(layout, parsed)
    return CODE["inbox.select_new"](ctx, listing={"notes": notes}, triage={"rows": []}, **kw)


def test_note_source_when_no_list_file_and_list_source_when_named():
    text = "# I\n---\n- https://e.example/a\n"
    parsed = {"items": [{**i.as_dict(), "needs_owner": False} for i in list_items(parse_list(text))]}
    notes = ["n1.md", "INBOX.md"]
    note_mode = _select(VaultLayout(), notes, None)
    assert {i["path"] for i in note_mode["items"]} == {"0-INBOX/n1.md", "0-INBOX/INBOX.md"}
    list_layout = VaultLayout(inbox_list_file="0-INBOX/INBOX.md")
    list_mode = _select(list_layout, notes, parsed)
    assert [i["resource"] for i in list_mode["items"]] == ["https://e.example/a"]  # notes ignored, no mixing
    both = _select(list_layout, notes, parsed, also_notes=True)
    assert {i["resource"] for i in both["items"]} == {"https://e.example/a", "0-INBOX/n1.md"}


# -- both layouts: every write destination lands in a configured place -----------------------------------


@pytest.mark.parametrize("layout", [DEFAULT_LAYOUT, REAL], ids=["default", "real"])
def test_every_write_destination_follows_the_layout(tmp_path, layout):
    assert layout_structure_errors(layout) == []
    vault = Vault(VaultPolicy(vault_dir=tmp_path, layout=layout.model_dump()))
    dests = [layout.agent_tasks_dir, layout.proposals_dir, layout.attachments_dir, layout.context_review_dir]
    for d in dests:
        assert vault.write_note(f"{d}/n.md", "x")["path"] == f"{d}/n.md"
    other = "1-Inbox" if layout is DEFAULT_LAYOUT else "0-INBOX"
    with pytest.raises(PathRejected):
        vault.write_note(f"{other}/n.md", "x")
    roots = set(layout.snapshot_roots())
    assert {d.split("/")[0] for d in dests} <= roots
    assert layout.inbox_archive_pattern.split("/")[0] in roots


@pytest.mark.parametrize(
    "field", ["agent_tasks_dir", "proposals_dir", "attachments_dir", "context_review_dir"]
)
def test_a_half_moved_write_destination_fails_validation(field):
    layout = VaultLayout(**{field: "Elsewhere/x"})
    errors = layout_structure_errors(layout)
    assert any(e.startswith(f"layout.{field} ") and "write_note" in e for e in errors)


def test_snapshot_roots_cover_the_list_file_and_archive():
    layout = VaultLayout(inbox_list_file="Lists/INBOX.md", inbox_dir="0-INBOX")
    assert "Lists" in layout.snapshot_roots()


def test_front_matter_alone_is_no_header_for_a_later_rule():
    entries = parse_list("---\ntags: [a]\n---\n- a\n---\n- b\n")
    assert [e.raw for e in entries] == ["- a", "- b"]
