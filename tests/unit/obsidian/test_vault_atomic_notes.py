"""Spec 004 FR-003, US1, T002/T003: atomic writes, front matter and the note operations on a temp vault."""

from __future__ import annotations

import os
import threading

import pytest

from pkpipe.obsidian import atomic
from pkpipe.obsidian.errors import NoteExists, NoteNotFound, PathRejected, VaultError, WriteConflict
from pkpipe.obsidian.notes import parse_note
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault


@pytest.fixture
def vault(synthetic_vault) -> Vault:
    return Vault(VaultPolicy.load(None, synthetic_vault))


def test_write_outside_allowlist_rejected(vault, synthetic_vault):
    """US1.1: a write outside the allowlist is rejected and nothing is created."""
    with pytest.raises(PathRejected):
        vault.write_note("4-Archive/x.md", "body")
    with pytest.raises(PathRejected):
        vault.write_note("../x.md", "body")
    assert not (synthetic_vault / "4-Archive").exists()
    assert not (synthetic_vault.parent / "x.md").exists()


def test_write_note_creates_with_front_matter_and_refuses_overwrite(vault, synthetic_vault):
    vault.write_note(
        "3-Resources/Tools/new.md", "Body\n", {"source": "https://e.invalid", "tags": ["a", "b"]}
    )
    text = (synthetic_vault / "3-Resources/Tools/new.md").read_text()
    assert text.startswith("---\nsource: https://e.invalid\ntags:\n- a\n- b\n---\nBody\n")
    with pytest.raises(NoteExists):
        vault.write_note("3-Resources/Tools/new.md", "other")
    assert (synthetic_vault / "3-Resources/Tools/new.md").read_text() == text


def test_overwrite_without_front_matter_keeps_existing_block_byte_for_byte(vault, synthetic_vault):
    """FR-003: front matter stays intact (comments, key order, quoting) when only the body is rewritten."""
    path = synthetic_vault / "0-INBOX" / "fm.md"
    header = "---\n# keep me\nzeta: 'q'\nalpha:   [1,  2]\n---\n"
    path.write_text(header + "old body\n")
    vault.write_note("0-INBOX/fm.md", "new body\n", overwrite=True)
    assert path.read_text() == header + "new body\n"


def test_overwrite_with_front_matter_replaces_block(vault, synthetic_vault):
    path = synthetic_vault / "0-INBOX" / "fm.md"
    path.write_text("---\na: 1\n---\nold\n")
    vault.write_note("0-INBOX/fm.md", "new\n", {"b": 2}, overwrite=True)
    assert path.read_text() == "---\nb: 2\n---\nnew\n"


def test_unserialisable_front_matter_rejected_without_touching_file(vault, synthetic_vault):
    path = synthetic_vault / "0-INBOX" / "fm.md"
    path.write_text("keep\n")
    with pytest.raises(VaultError):
        vault.write_note("0-INBOX/fm.md", "x", {"bad": object()}, overwrite=True)
    assert path.read_text() == "keep\n"


def test_body_cannot_smuggle_a_second_front_matter_block(vault):
    with pytest.raises(VaultError):
        vault.write_note("0-INBOX/smuggle.md", "---\nevil: true\n---\nbody")


def test_append_preserves_front_matter_and_body(vault, synthetic_vault):
    path = synthetic_vault / "0-INBOX" / "example-tool.md"
    before = path.read_text()
    vault.append_note("0-INBOX/example-tool.md", "- appended line\n")
    after = path.read_text()
    assert after == before + "- appended line\n"
    assert parse_note(after).front_matter == {"source": "https://example.invalid/tool"}


def test_append_adds_newline_separator_and_requires_existing_note(vault, synthetic_vault):
    path = synthetic_vault / "0-INBOX" / "nonl.md"
    path.write_text("no newline")
    vault.append_note("0-INBOX/nonl.md", "next")
    assert path.read_text() == "no newline\nnext"
    with pytest.raises(NoteNotFound):
        vault.append_note("0-INBOX/missing.md", "x")


def test_read_note_parses_front_matter_and_reports_bad_yaml(vault, synthetic_vault):
    note = vault.read_note("0-INBOX/example-tool.md")
    assert note["front_matter"] == {"source": "https://example.invalid/tool"}
    assert "synthetic note" in note["body"]
    (synthetic_vault / "0-INBOX" / "bad.md").write_text("---\na: [unclosed\n---\ntext\n")
    bad = vault.read_note("0-INBOX/bad.md")
    assert (
        bad["front_matter"] == {} and "invalid YAML" in bad["front_matter_error"] and bad["body"] == "text\n"
    )


def test_read_note_dates_in_front_matter_are_jsonable(vault, synthetic_vault):
    (synthetic_vault / "0-INBOX" / "d.md").write_text("---\ncreated: 2026-01-02\n---\nx")
    assert vault.read_note("0-INBOX/d.md")["front_matter"] == {"created": "2026-01-02"}


def test_move_note_moves_into_para_and_never_overwrites(vault, synthetic_vault):
    vault.move_note("0-INBOX/example-tool.md", "3-Resources/Tools/example-tool.md")
    assert not (synthetic_vault / "0-INBOX/example-tool.md").exists()
    assert (synthetic_vault / "3-Resources/Tools/example-tool.md").exists()
    (synthetic_vault / "3-Resources/Tools/taken.md").write_text("mine")
    with pytest.raises(NoteExists):
        vault.move_note("0-INBOX/example-video.md", "3-Resources/Tools/taken.md")
    assert (synthetic_vault / "0-INBOX/example-video.md").exists()
    assert (synthetic_vault / "3-Resources/Tools/taken.md").read_text() == "mine"


def test_move_note_destination_must_be_allowed(vault):
    vault.policy.allow["move_note"] = ["0-INBOX", "3-Resources"]
    with pytest.raises(PathRejected):
        vault.move_note("0-INBOX/example-tool.md", "../escaped.md")
    with pytest.raises(PathRejected):
        vault.move_note("0-INBOX/example-tool.md", "4-Archive/x.md")
    with pytest.raises(NoteNotFound):
        vault.move_note("0-INBOX/nope.md", "3-Resources/x.md")


def test_managed_files_cannot_be_written_or_moved_generically(vault, synthetic_vault):
    """The triage file and processing list are only reachable through their structured tools."""
    for managed in ("0-INBOX/Inbox-Triage.md", "0-INBOX/Manual-Resource-Processing-List.md"):
        (synthetic_vault / managed).write_text("| x |\n")
        with pytest.raises(PathRejected):
            vault.write_note(managed, "x", overwrite=True)
        with pytest.raises(PathRejected):
            vault.append_note(managed, "x")
        with pytest.raises(PathRejected):
            vault.move_note(managed, "3-Resources/x.md")
        with pytest.raises(PathRejected):
            vault.move_note("0-INBOX/example-tool.md", managed)


@pytest.mark.parametrize(
    "variant",
    ["0-INBOX/inbox-triage.md", "0-INBOX/INBOX-TRIAGE.md", "0-INBOX/Inbox-triage.md"],
)
def test_managed_files_protected_against_case_variants(vault, synthetic_vault, variant):
    """FR-002 on case-insensitive file systems (macOS APFS): a case variant of the triage file name must not
    bypass the managed-file rule and overwrite the owner's triage decisions."""
    triage = synthetic_vault / "0-INBOX/Inbox-Triage.md"
    triage.write_text("| owner data |\n")
    with pytest.raises(PathRejected):
        vault.write_note(variant, "wiped", overwrite=True)
    with pytest.raises(PathRejected):
        vault.append_note(variant, "junk")
    with pytest.raises(PathRejected):
        vault.move_note("0-INBOX/example-tool.md", variant)
    assert triage.read_text() == "| owner data |\n"
    assert (synthetic_vault / "0-INBOX/example-tool.md").exists()


def test_managed_file_reached_through_a_symlink_is_protected(vault, synthetic_vault):
    triage = synthetic_vault / "0-INBOX/Inbox-Triage.md"
    triage.write_text("| owner data |\n")
    (synthetic_vault / "3-Resources/alias.md").symlink_to(triage)
    with pytest.raises(PathRejected):
        vault.write_note("3-Resources/alias.md", "wiped", overwrite=True)
    assert triage.read_text() == "| owner data |\n"


def test_move_never_overwrites_a_destination_that_appears_during_the_move(
    vault, synthetic_vault, monkeypatch
):
    """A note created in Obsidian (which ignores our lock) right after the last check survives the move."""
    import contextlib

    from pkpipe.obsidian import vault as vault_mod

    dest = synthetic_vault / "3-Resources/Tools/race.md"
    real_lock, real_exists = vault_mod.dir_lock, type(dest).exists
    held = {"on": False}

    @contextlib.contextmanager
    def lock(directory):
        with real_lock(directory):
            held["on"] = True
            try:
                yield
            finally:
                held["on"] = False

    def racing_exists(self, *a, **k):
        if held["on"] and self == dest and not real_exists(self):
            self.write_text("created in Obsidian")  # appears right after this check reports "free"
            return False
        return real_exists(self, *a, **k)

    def racing_link(src, dst, **k):
        if held["on"] and not real_exists(dest):
            dest.write_text("created in Obsidian")
        return real_link(src, dst, **k)

    real_link = os.link
    monkeypatch.setattr(vault_mod, "dir_lock", lock)
    monkeypatch.setattr(type(dest), "exists", racing_exists)
    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(NoteExists):
        vault.move_note("0-INBOX/example-tool.md", "3-Resources/Tools/race.md")
    monkeypatch.undo()
    assert dest.read_text() == "created in Obsidian"
    assert (synthetic_vault / "0-INBOX/example-tool.md").exists()


def test_list_folder_reports_conflicts_and_hides_dotfiles(vault, synthetic_vault):
    """Edge case: Obsidian Sync conflict copies are ignored and reported."""
    inbox = synthetic_vault / "0-INBOX"
    (inbox / "example-tool (conflict).md").write_text("c")
    (inbox / ".hidden.md").write_text("h")
    (inbox / "image.png").write_bytes(b"\x89PNG")
    result = vault.list_folder("0-INBOX")
    assert result["notes"] == ["example-tool.md", "example-video.md"]
    assert result["conflicts"] == ["example-tool (conflict).md"]
    assert "_quarantine" in result["folders"]
    with pytest.raises(NoteNotFound):
        vault.list_folder("3-Resources/Missing")


def test_cannot_write_conflict_named_files(vault, synthetic_vault):
    """Edge case: conflict copies are ignored - never written, appended to or moved."""
    conflict = synthetic_vault / "0-INBOX/example-tool (conflict).md"
    conflict.write_text("sync copy")
    with pytest.raises(PathRejected):
        vault.write_note("0-INBOX/a (conflict).md", "x")
    with pytest.raises(PathRejected):
        vault.append_note("0-INBOX/example-tool (conflict).md", "x")
    with pytest.raises(PathRejected):
        vault.move_note("0-INBOX/example-tool (conflict).md", "3-Resources/x.md")
    assert conflict.read_text() == "sync copy"
    with pytest.raises(PathRejected):
        vault.move_note("0-INBOX/example-tool.md", "0-INBOX/a (conflict).md")


# -- atomic writer ----------------------------------------------------------------------


def test_atomic_write_leaves_no_temp_files_and_old_content_on_failure(tmp_path, monkeypatch):
    """US1.2 / FR-003: temp file + rename; a failure mid-write keeps the old file and cleans up."""
    target = tmp_path / "n.md"
    target.write_text("old")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic.write_bytes_atomic(target, b"new")
    monkeypatch.undo()
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["n.md"]
    atomic.write_bytes_atomic(target, b"new")
    assert target.read_text() == "new" and [p.name for p in tmp_path.iterdir()] == ["n.md"]


def test_atomic_write_preserves_file_mode(tmp_path):
    target = tmp_path / "n.md"
    target.write_text("old")
    target.chmod(0o640)
    atomic.write_bytes_atomic(target, b"new")
    assert target.stat().st_mode & 0o777 == 0o640


def test_update_text_retries_with_fresh_read_when_file_changes(tmp_path):
    """Edge case: file edited (e.g. in Obsidian) between our read and write -> transform sees fresh text."""
    target = tmp_path / "t.md"
    target.write_text("a\n")
    seen: list[str | None] = []

    def transform(text):
        seen.append(text)
        if len(seen) == 1:  # concurrent edit lands after the first read
            target.write_text("a\nowner edit\n")
        return text + "agent\n"

    atomic.update_text(target, transform)
    assert seen == ["a\n", "a\nowner edit\n"]
    assert target.read_text() == "a\nowner edit\nagent\n"


def test_update_text_gives_up_after_retries(tmp_path):
    target = tmp_path / "t.md"
    target.write_text("0")
    counter = iter(range(100))

    def transform(text):
        target.write_text(str(next(counter) + 1))  # always changes underneath us
        return "x"

    with pytest.raises(WriteConflict):
        atomic.update_text(target, transform, retries=3)
    assert target.read_text() != "x"


def test_concurrent_appends_do_not_lose_updates(tmp_path):
    """FR-003: parallel read-modify-write cycles are serialised by the directory lock."""
    target = tmp_path / "log.md"
    target.write_text("")

    def worker(n):
        for i in range(10):
            atomic.update_text(target, lambda t, n=n, i=i: (t or "") + f"{n}-{i}\n", retries=200)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    lines = target.read_text().splitlines()
    assert len(lines) == 40 and len(set(lines)) == 40
