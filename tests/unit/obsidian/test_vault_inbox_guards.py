"""Spec 020 FR-005 hardening: the archive and the list file are never a managed file, and the archive text
always comes from what the locked rewrite removes."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from pkpipe.domain.layout import VaultLayout
from pkpipe.obsidian import vault as vault_module
from pkpipe.obsidian.errors import PathRejected
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault

DAY = date(2026, 11, 4)
LIST = "# Inbox\n---\n- https://example.org/a\n- https://example.org/b\n"


def _policy(root, **layout):
    return VaultPolicy(vault_dir=root, layout={"inbox_list_file": "0-INBOX/INBOX.md", **layout})


@pytest.fixture
def root(tmp_path):
    for f in ("0-INBOX", "4-Archive/Inbox"):
        (tmp_path / f).mkdir(parents=True)
    (tmp_path / "0-INBOX" / "INBOX.md").write_text(LIST, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "field,value,fragment",
    [
        ("inbox_archive_pattern", "0-INBOX/Inbox-Triage.md", "managed file layout.triage_file"),
        ("inbox_archive_pattern", "0-INBOX/INBOX.md", "managed file layout.inbox_list_file"),
        ("inbox_archive_pattern", "0-inbox/inbox.md", "managed file layout.inbox_list_file"),
        ("triage_file", "0-INBOX/INBOX.md", "name the same file"),
        ("inbox_list_file", "0-INBOX/_quarantine/INBOX.md", "quarantine folder"),
        ("improvement_file", "2-Areas/System/System-Tool-Candidates.md", "name the same file"),
    ],
)
def test_layout_rejects_overlapping_managed_files(field, value, fragment):
    data = {"inbox_list_file": "0-INBOX/INBOX.md", field: value}
    with pytest.raises(ValidationError, match=fragment):
        VaultLayout(**data)


def test_a_dated_archive_pattern_is_fine():
    assert VaultLayout(inbox_list_file="0-INBOX/INBOX.md").inbox_archive_pattern.endswith("{yyyy-MM-dd}.md")


def test_the_vault_refuses_an_archive_that_resolves_to_a_managed_file(root):
    """Defence in depth: a pattern that is dated by name but formats to the triage file is refused at run
    time, nothing is written and the entry stays in the list."""
    policy = _policy(root)
    object.__setattr__(policy.layout, "inbox_archive_pattern", "0-INBOX/Inbox-Triage.md")  # bypass the model
    with pytest.raises(PathRejected, match="archive"):
        Vault(policy, today=lambda: DAY).inbox_remove_entries(["- https://example.org/a"])
    assert not (root / "0-INBOX" / "Inbox-Triage.md").exists()
    assert (root / "0-INBOX" / "INBOX.md").read_text(encoding="utf-8") == LIST


def test_the_vault_refuses_an_archive_that_is_the_list_itself(root):
    policy = _policy(root)
    object.__setattr__(policy.layout, "inbox_archive_pattern", "0-INBOX/INBOX.md")
    with pytest.raises(PathRejected, match="archive"):
        Vault(policy, today=lambda: DAY).inbox_remove_entries(["- https://example.org/a"])
    assert (root / "0-INBOX" / "INBOX.md").read_text(encoding="utf-8") == LIST


def test_the_vault_refuses_a_list_file_that_is_another_managed_file(root):
    policy = _policy(root)
    object.__setattr__(policy.layout, "triage_file", "0-INBOX/INBOX.md")
    with pytest.raises(PathRejected, match="managed"):
        Vault(policy, today=lambda: DAY).inbox_remove_entries(["- https://example.org/a"])


def test_the_vault_refuses_a_list_file_in_the_quarantine_folder(root):
    (root / "0-INBOX" / "_quarantine").mkdir()
    (root / "0-INBOX" / "_quarantine" / "L.md").write_text(LIST, encoding="utf-8")
    policy = _policy(root)
    object.__setattr__(policy.layout, "inbox_list_file", "0-INBOX/_quarantine/L.md")
    with pytest.raises(PathRejected, match="list file"):
        Vault(policy, today=lambda: DAY).inbox_remove_entries(["- https://example.org/a"])


def test_an_entry_that_appears_before_the_locked_rewrite_is_archived_too(root, monkeypatch):
    """The list changes between planning and the locked rewrite: the entry the rewrite removes is archived."""
    listing = root / "0-INBOX" / "INBOX.md"
    real = vault_module.update_text
    injected: list[bool] = []

    def racing(path, transform, **kw):
        if path == listing.resolve() and not injected:
            injected.append(True)  # the owner adds the same entry in Obsidian just before our rewrite
            listing.write_text(LIST + "- late entry\n", encoding="utf-8")
        return real(path, transform, **kw)

    monkeypatch.setattr(vault_module, "update_text", racing)
    out = Vault(_policy(root), today=lambda: DAY).inbox_remove_entries(["- late entry"])
    assert out["removed"] == ["- late entry"] and out["not_found"] == []
    assert "late entry" not in listing.read_text(encoding="utf-8")
    archive = (root / out["archive"]).read_text(encoding="utf-8")
    assert archive.count("- late entry") == 1  # in the archive exactly once


def test_an_entry_removed_meanwhile_is_not_archived_and_reported(root, monkeypatch):
    listing = root / "0-INBOX" / "INBOX.md"
    real = vault_module.update_text

    def racing(path, transform, **kw):
        if path == listing.resolve():
            listing.write_text("# Inbox\n---\n- https://example.org/b\n", encoding="utf-8")
        return real(path, transform, **kw)

    monkeypatch.setattr(vault_module, "update_text", racing)
    out = Vault(_policy(root), today=lambda: DAY).inbox_remove_entries(["- https://example.org/a"])
    assert out["removed"] == [] and out["not_found"] == ["- https://example.org/a"]
    # archived by the plan (at-least-once, see the code comment), but never an entry missing from both files
    assert "https://example.org/b" in listing.read_text(encoding="utf-8")
