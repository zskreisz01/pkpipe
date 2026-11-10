"""Spec 033: ``write_pipeline`` writes the overview note only when it changed and never replaces a file that
is not a pipeline note."""

from __future__ import annotations

import pytest

from pkpipe.domain import pipeline as pl
from pkpipe.obsidian.errors import PathRejected
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault


def make(tmp_path, **layout):
    root = tmp_path / "vault"
    (root / "1-Inbox").mkdir(parents=True)
    base = {
        "inbox_dir": "1-Inbox",
        "inbox_list_file": "1-Inbox/INBOX.md",
        "triage_file": "1-Inbox/Inbox-Triage.md",
        "processing_list": "1-Inbox/Manual-Resource-Processing-List.md",
        "archive_dir": "6-Archive/Inbox",
        "inbox_archive_pattern": "6-Archive/Inbox/Inbox-Archive-{yyyy-MM-dd}.md",
        "quarantine_dir": "1-Inbox/_quarantine",
        "attachments_dir": "Attachments",
        "resources_dir": "4-Resources",
        "areas_dir": "3-Areas",
        "projects_dir": "2-Projects",
        "tool_candidates_file": "3-Areas/System/System-Tool-Candidates.md",
        "improvement_file": "3-Areas/Self-Improvement/Improvement-Backlog.md",
        "journal_pattern": "3-Areas/Journal/{yyyy-MM-dd}.md",
        **layout,
    }
    return Vault(VaultPolicy(vault_dir=root, layout=base)), root


def text(generated: str, body: str = "x") -> str:
    return f"{pl.HEADING}\n\n{pl.GENERATED} {generated}\n\n{body}\n"


def test_the_note_is_written_once_and_then_only_when_it_changed(tmp_path):
    vault, root = make(tmp_path)
    assert vault.write_pipeline(text("10:00")) == {"file": "1-Inbox/Pipeline.md", "written": True}
    assert vault.write_pipeline(text("11:00")) == {"file": "1-Inbox/Pipeline.md", "written": False}
    assert "10:00" in (root / "1-Inbox/Pipeline.md").read_text()  # the unchanged note kept its old stamp
    assert vault.write_pipeline(text("12:00", "y"))["written"] is True


def test_the_layout_key_moves_the_note(tmp_path):
    vault, root = make(tmp_path, pipeline_file="1-Inbox/Overview/Pipeline-Overview.md")
    assert vault.write_pipeline(text("10:00"))["file"] == "1-Inbox/Overview/Pipeline-Overview.md"
    assert (root / "1-Inbox/Overview/Pipeline-Overview.md").is_file()


def test_a_file_that_is_not_a_pipeline_note_is_never_replaced(tmp_path):
    vault, root = make(tmp_path)
    (root / "1-Inbox/Pipeline.md").write_text("# My own page\n", encoding="utf-8")
    with pytest.raises(PathRejected):
        vault.write_pipeline(text("10:00"))
    assert (root / "1-Inbox/Pipeline.md").read_text() == "# My own page\n"


def test_only_a_pipeline_note_of_a_sane_size_is_accepted(tmp_path):
    vault, _ = make(tmp_path)
    with pytest.raises(PathRejected):
        vault.write_pipeline("# Something else\n")
    with pytest.raises(PathRejected):
        vault.write_pipeline(text("10:00", "x" * 250_000))


def test_the_policy_refuses_a_layout_that_puts_the_note_outside_its_allow_list(tmp_path):
    with pytest.raises(ValueError, match="outside the allowlist of tool 'write_pipeline'"):
        make(tmp_path, pipeline_file="3-Areas/Pipeline.md")


def test_the_inbox_entry_ids_have_no_text(tmp_path):
    vault, root = make(tmp_path)
    (root / "1-Inbox/INBOX.md").write_text(
        "# Inbox\n---\n- https://example.org/a\n- a thought\n", encoding="utf-8"
    )
    ids = vault.inbox_entry_ids()
    assert ids is not None and len(ids) == 2 and all(len(i) == 12 for i in ids)
