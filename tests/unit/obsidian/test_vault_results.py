"""Spec 032: ``write_results_index`` lists the pkpipe notes of a folder and rewrites only on change;
``import_audio`` copies a podcast only from an allowed folder."""

from __future__ import annotations

import pytest

from pkpipe.obsidian.errors import NoteNotFound, PathRejected
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault


def make(tmp_path, audio_dirs=()):
    root = tmp_path / "vault"
    for f in ("1-Inbox", "4-Resources/Tools", "Attachments"):
        (root / f).mkdir(parents=True)
    policy = VaultPolicy(
        vault_dir=root,
        audio_import_dirs=list(audio_dirs),
        layout={
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
        },
    )
    return Vault(policy), root


def note(root, name, front, body):
    (root / "4-Resources/Tools" / name).write_text(f"---\n{front}\n---\n{body}", encoding="utf-8")


def test_the_index_lists_only_pkpipe_notes_and_is_rewritten_only_on_change(tmp_path):
    vault, root = make(tmp_path)
    note(
        root,
        "wordcraft-1c797f38b5c7.md",
        "type: resource_note\npkpipe_item: 1c797f38b5c7\ncategory: tool\n"
        "source: https://wordcraft.example/\ncreated: '2026-11-08'",
        "# WordCraft\n\n## Recommendation\n\nhold\n",
    )
    note(root, "mine.md", "type: note", "# My own note\n")
    first = vault.write_results_index("4-Resources/Tools")
    assert first == {"folder": "4-Resources/Tools", "notes": 1, "written": True}
    text = (root / "4-Resources/Tools/_Index.md").read_text(encoding="utf-8")
    assert "wordcraft-1c797f38b5c7" in text and "hold" in text and "My own note" not in text
    assert vault.write_results_index("4-Resources/Tools")["written"] is False  # nothing changed
    note(
        root,
        "doory-73fe75b3dfbb.md",
        "type: resource_note\npkpipe_item: 73fe75b3dfbb\ncategory: tool",
        "# Doory\n",
    )
    second = vault.write_results_index("4-Resources/Tools")
    assert second["notes"] == 2 and second["written"] is True


def test_the_index_of_a_missing_folder_or_outside_the_policy_is_refused_or_empty(tmp_path):
    vault, _ = make(tmp_path)
    assert vault.write_results_index("4-Resources/Nope")["notes"] == 0
    with pytest.raises(PathRejected):
        vault.write_results_index("Secret")


def test_a_podcast_is_copied_only_from_an_allowed_folder(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "ep.m4a").write_bytes(b"x" * 100)
    (tmp_path / "other.m4a").write_bytes(b"y")
    vault, root = make(tmp_path, audio_dirs=[audio])
    out = vault.import_audio(str(audio / "ep.m4a"), "abc-ep.m4a")
    assert out == {"path": "Attachments/Podcasts/abc-ep.m4a", "bytes": 100, "copied": True}
    assert (root / "Attachments/Podcasts/abc-ep.m4a").read_bytes() == b"x" * 100
    assert vault.import_audio(str(audio / "ep.m4a"), "abc-ep.m4a")["copied"] is False  # same size: kept
    with pytest.raises(PathRejected, match="outside audio_import_dirs"):
        vault.import_audio(str(tmp_path / "other.m4a"), "o.m4a")
    with pytest.raises(PathRejected, match="plain file name"):
        vault.import_audio(str(audio / "ep.m4a"), "../x.m4a")
    with pytest.raises(NoteNotFound):
        vault.import_audio(str(audio / "none.m4a"), "n.m4a")


def test_without_audio_import_dirs_every_copy_is_refused(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "ep.m4a").write_bytes(b"x")
    vault, _ = make(tmp_path)
    with pytest.raises(PathRejected):
        vault.import_audio(str(audio / "ep.m4a"), "ep.m4a")
