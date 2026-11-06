"""Spec 020 US3 (T010): ``inbox_remove_entries``, the only new write path, and its limits."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pkpipe.domain.inbox_list import parse_list
from pkpipe.obsidian.errors import NoteNotFound, PathRejected
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "inbox" / "INBOX-list.md"
DAY = date(2026, 11, 4)


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for f in ("1-Inbox", "6-Archive/Inbox", "3-Areas"):
        (root / f).mkdir(parents=True)
    (root / "1-Inbox" / "INBOX.md").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    policy = VaultPolicy(
        vault_dir=root,
        layout={
            "inbox_dir": "1-Inbox",
            "inbox_list_file": "1-Inbox/INBOX.md",
            "triage_file": "1-Inbox/Inbox-Triage.md",
            "processing_list": "1-Inbox/Manual-Resource-Processing-List.md",
            "archive_dir": "6-Archive/Inbox",
            "inbox_archive_pattern": "6-Archive/Inbox/Inbox-Archive-{yyyy-MM-dd}.md",
            "quarantine_dir": "1-Inbox/_quarantine",
            "areas_dir": "3-Areas",
            "tool_candidates_file": "3-Areas/System/System-Tool-Candidates.md",
            "improvement_file": "3-Areas/Self-Improvement/Improvement-Backlog.md",
            "journal_pattern": "3-Areas/Journal/{yyyy-MM-dd}.md",
        },
    )
    return Vault(policy, today=lambda: DAY)


def _list(vault) -> str:
    return (vault.policy.root / "1-Inbox" / "INBOX.md").read_text(encoding="utf-8")


def test_removed_entries_leave_the_list_and_land_in_the_dated_archive(vault):
    before = _list(vault)
    entries = parse_list(before)
    two_line = entries[2].raw
    out = vault.inbox_remove_entries([two_line, entries[0].raw])
    assert out["removed"] == [two_line, entries[0].raw] and out["not_found"] == []
    assert out["archive"] == "6-Archive/Inbox/Inbox-Archive-2026-11-04.md"
    after = _list(vault)
    assert two_line not in after and entries[0].raw not in after
    assert [e.raw for e in parse_list(after)] == [e.raw for i, e in enumerate(entries) if i not in (0, 2)]
    archive = (vault.policy.root / out["archive"]).read_text(encoding="utf-8")
    assert archive == f"# Inbox archive 2026-11-04\n\n{two_line}\n{entries[0].raw}\n"
    # the header, the comments and the blank lines are untouched
    assert after.startswith(before.split("- https://example.org/articles/alpha?utm_source")[0])
    assert "<!-- owner note: check the speaker's other talks -->" in after


def test_a_second_call_appends_to_the_same_archive_file(vault):
    entries = parse_list(_list(vault))
    vault.inbox_remove_entries([entries[3].raw])
    out = vault.inbox_remove_entries([entries[4].raw])
    archive = (vault.policy.root / out["archive"]).read_text(encoding="utf-8")
    assert archive.count("# Inbox archive") == 1 and entries[3].raw in archive and entries[4].raw in archive


def test_unknown_entry_changes_nothing_and_is_reported(vault):
    before = _list(vault)
    out = vault.inbox_remove_entries(["- it is not there"])
    assert out["removed"] == [] and out["not_found"] == ["- it is not there"] and out["archive"] is None
    assert _list(vault) == before
    assert not list((vault.policy.root / "6-Archive/Inbox").iterdir())


def test_entries_added_in_obsidian_meanwhile_survive(vault):
    entry = parse_list(_list(vault))[1]
    path = vault.policy.root / "1-Inbox" / "INBOX.md"
    path.write_text(path.read_text(encoding="utf-8") + "- added while processing\n", encoding="utf-8")
    vault.inbox_remove_entries([entry.raw])
    assert _list(vault).endswith("- added while processing\n")


def test_it_needs_a_list_file_a_clean_request_and_an_existing_list(vault, tmp_path):
    for bad in ([], [""], ["x"] * 201, "not a list"):
        with pytest.raises(PathRejected):
            vault.inbox_remove_entries(bad)
    (vault.policy.root / "1-Inbox" / "INBOX.md").unlink()
    with pytest.raises(NoteNotFound):
        vault.inbox_remove_entries(["- a"])
    plain = Vault(VaultPolicy(vault_dir=tmp_path))
    with pytest.raises(PathRejected, match="inbox_list_file"):
        plain.inbox_remove_entries(["- a"])


def test_generic_tools_cannot_rewrite_the_list_file(vault):
    for call in (
        lambda: vault.write_note("1-Inbox/INBOX.md", "x", None, True),
        lambda: vault.append_note("1-Inbox/INBOX.md", "x"),
        lambda: vault.move_note("1-Inbox/INBOX.md", "6-Archive/Inbox/INBOX.md"),
    ):
        with pytest.raises(PathRejected, match="maintained"):
            call()
    assert vault.read_note("1-Inbox/INBOX.md")["body"]  # reading is how the flow gets the entries
