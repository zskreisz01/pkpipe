"""Spec 020 US2 (T005, T007): the layout block of the vault policy, its validation and the journal pattern."""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from pkpipe.domain.layout import DEFAULT_LAYOUT, VaultLayout
from pkpipe.obsidian.policy import DEFAULT_ALLOW, VaultPolicy

REAL = {
    "inbox_dir": "1-Inbox",
    "inbox_list_file": "1-Inbox/INBOX.md",
    "triage_file": "1-Inbox/Inbox-Triage.md",
    "processing_list": "1-Inbox/Manual-Resource-Processing-List.md",
    "archive_dir": "6-Archive/Inbox",
    "inbox_archive_pattern": "6-Archive/Inbox/Inbox-Archive-{yyyy-MM-dd}.md",
    "agent_tasks_dir": "3-Areas/Agent-Tasks",
    "tool_candidates_file": "3-Areas/System/System-Tool-Candidates.md",
    "improvement_file": "3-Areas/Self-Improvement/Improvement-Backlog.md",
    "proposals_dir": "3-Areas/Self-Improvement/System-Changes",
    "quarantine_dir": "1-Inbox/_quarantine",
    "journal_pattern": "DailyJournals/{yyyy}/{yyyy-MM-dd}.md",
    "attachments_dir": "1-Inbox/attachments",
    "projects_dir": "2-Projects",
    "areas_dir": "3-Areas",
    "resources_dir": "4-Resources",
    "context_review_dir": "3-Areas/Context-Review",
}
_PARA = ["1-Inbox", "2-Projects", "3-Areas", "4-Resources", "6-Archive", "DailyJournals"]
REAL_ALLOW = {"read_notes": _PARA, "read_note": _PARA}


def test_defaults_equal_the_pre_020_constants(tmp_path):
    p = VaultPolicy(vault_dir=tmp_path)
    assert p.triage_file == "0-INBOX/Inbox-Triage.md"
    assert p.processing_list == "0-INBOX/Manual-Resource-Processing-List.md"
    assert p.improvement_file == "2-Areas/Self-Improvement/Improvement-Backlog.md"
    assert p.tool_candidates_file == "2-Areas/System/System-Tool-Candidates.md"
    assert p.quarantine_dir == "0-INBOX/_quarantine"
    assert p.allow["read_triage"] == ["0-INBOX"] and p.allow == DEFAULT_ALLOW
    assert DEFAULT_LAYOUT.archive_dir == "4-Archive/Inbox"
    assert DEFAULT_LAYOUT.journal_path(date(2026, 11, 4)) == "2-Areas/Journal/2026-11-04.md"


def test_legacy_top_level_keys_still_work(tmp_path):
    p = VaultPolicy(vault_dir=tmp_path, triage_file="0-INBOX/T.md")
    assert p.layout.triage_file == "0-INBOX/T.md"


def test_load_derives_the_allowlists_of_the_real_layout(tmp_path):
    cfg = tmp_path / "vault.json"
    cfg.write_text(json.dumps({"vault_dir": str(tmp_path), "layout": REAL, "allow": REAL_ALLOW}))
    p = VaultPolicy.load(cfg)
    assert p.allow["read_triage"] == ["1-Inbox"]
    assert p.allow["update_improvements"] == ["3-Areas"]
    assert p.allow["move_note"][-1] == "6-Archive"
    assert p.allow["read_notes"][-1] == "DailyJournals"
    assert p.allow["inbox_remove_entries"] == ["1-Inbox", "6-Archive"]


def test_a_layout_outside_the_allowlists_fails_and_names_path_and_tool(tmp_path):
    """US2 scenario 3: the journal root is not readable unless the owner allows it."""
    with pytest.raises(ValidationError) as exc:
        VaultPolicy(vault_dir=tmp_path, layout=REAL)
    msg = str(exc.value)
    assert "layout.journal_pattern 'DailyJournals/{yyyy}/{yyyy-MM-dd}.md'" in msg and "'read_notes'" in msg


def test_layout_paths_must_be_relative_and_clean():
    for bad in ("/abs/x.md", "../x/Triage.md", "a/./b.md", "~/x.md", "a\\b.md", ".hidden/x.md"):
        with pytest.raises(ValidationError):
            VaultLayout(triage_file=bad)
    with pytest.raises(ValidationError, match="unknown placeholder"):
        VaultLayout(journal_pattern="J/{yy}/{yyyy-MM-dd}.md")
    with pytest.raises(ValidationError, match=".md"):
        VaultLayout(triage_file="0-INBOX/Triage.txt")
    with pytest.raises(ValidationError):
        VaultLayout(unknown_place="x")


def test_inbox_list_file_must_be_inside_the_tool_allowlists(tmp_path):
    with pytest.raises(ValidationError, match="inbox_list_file 'Elsewhere/INBOX.md'.*inbox_remove_entries"):
        VaultPolicy(vault_dir=tmp_path, layout={"inbox_list_file": "Elsewhere/INBOX.md"})


def test_journal_pattern_placeholders():
    lay = VaultLayout(journal_pattern="DailyJournals/{yyyy}/{MM}/{dd}-{yyyy-MM-dd}.md")
    assert lay.journal_path(date(2026, 3, 9)) == "DailyJournals/2026/03/09-2026-03-09.md"
    assert lay.inbox_archive_path(date(2026, 3, 9)) == "4-Archive/Inbox/Inbox-Archive-2026-03-09.md"


def test_snapshot_scope_follows_the_layout():
    """FR-009 / T014: spec 019 takes its scope from the layout, not from a list of its own."""
    assert DEFAULT_LAYOUT.snapshot_roots() == ["0-INBOX", "1-Projects", "2-Areas", "3-Resources", "4-Archive"]
    assert VaultLayout(**REAL).snapshot_roots() == [
        "1-Inbox",
        "2-Projects",
        "3-Areas",
        "4-Resources",
        "6-Archive",
    ]
