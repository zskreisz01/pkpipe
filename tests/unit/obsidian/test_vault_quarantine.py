"""Spec 009 US2/US3 on a synthetic vault: ``quarantine_note`` moves a flagged note into the quarantine folder
in inert form (code block, ``security_flag`` front matter, flag codes only); nothing else may write there;
the owner's release moves the exact original text back and re-queues the row."""

from __future__ import annotations

import pytest

from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian.errors import NoteExists, PathRejected, VaultError
from pkpipe.obsidian.notes import parse_note
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault

NOTE = "---\nsource: https://evil.example/x\n---\nBody with ``` fences, [[links]] and <script>x</script>.\n"
FLAGS = [{"flag": "canary_leak", "detector": "canary", "reason_code": "canary_leak"}]


@pytest.fixture
def vault(synthetic_vault):
    (synthetic_vault / "0-INBOX" / "odd.md").write_text(NOTE, encoding="utf-8")
    return Vault(VaultPolicy(vault_dir=synthetic_vault))


def _quarantine(vault):
    item = item_id_for("0-INBOX/odd.md")
    vault.update_triage(
        [{"resource": "0-INBOX/odd.md", "ai_suggested_category": "none", "state": "quarantined"}]
    )
    res = vault.quarantine_note(
        item, "0-INBOX/odd.md", FLAGS, run_id="run-1", flagged_at="2026-11-01T06:00:00"
    )
    return item, res


def test_quarantine_moves_the_note_in_inert_form(vault, synthetic_vault):
    item, res = _quarantine(vault)
    assert res == {"path": f"0-INBOX/_quarantine/{item}.md", "original_path": "0-INBOX/odd.md", "moved": True}
    assert not (synthetic_vault / "0-INBOX/odd.md").exists()
    text = (synthetic_vault / res["path"]).read_text(encoding="utf-8")
    note = parse_note(text)
    assert note.front_matter["security_flag"]["flags"] == ["canary_leak"]
    assert note.front_matter["security_flag"]["run_id"] == "run-1"
    assert note.front_matter["original_path"] == "0-INBOX/odd.md" and note.front_matter["quarantined"] is True
    # the original front matter is not a front matter any more: it sits inside the code block
    assert "source" not in note.front_matter
    assert "````text\n---\nsource: https://evil.example/x" in note.body  # fence longer than the note's ```


def test_quarantine_is_idempotent_after_the_move(vault):
    item, first = _quarantine(vault)
    again = vault.quarantine_note(item, "0-INBOX/odd.md", FLAGS, run_id="run-1")
    assert again == {**first, "moved": False}


def test_url_only_item_gets_a_metadata_note(vault, synthetic_vault):
    res = vault.quarantine_note("abc123", None, FLAGS, run_id="r")
    text = (synthetic_vault / res["path"]).read_text(encoding="utf-8")
    assert "No source note" in text and res["moved"] is False


@pytest.mark.parametrize("item_id", ["../x", "a/b", "", "x" * 65, "a.b"])
def test_item_id_cannot_escape_the_quarantine_folder(vault, item_id):
    with pytest.raises(PathRejected):
        vault.quarantine_note(item_id, None, FLAGS)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("write_note", ("0-INBOX/_quarantine/x.md", "body")),
        ("append_note", ("0-INBOX/_quarantine/x.md", "more")),
        ("move_note", ("0-INBOX/example-tool.md", "0-INBOX/_quarantine/x.md")),
        ("move_note", ("0-INBOX/_quarantine/x.md", "0-INBOX/back.md")),
        ("write_note", ("0-INBOX/_QUARANTINE/x.md", "body")),
    ],
)
def test_only_quarantine_note_writes_the_quarantine_folder(vault, synthetic_vault, tool, args):
    (synthetic_vault / "0-INBOX/_quarantine/x.md").write_text("q", encoding="utf-8")
    with pytest.raises(PathRejected, match="quarantine"):
        getattr(vault, tool)(*args)


def test_managed_files_and_quarantined_notes_cannot_be_quarantined(vault):
    with pytest.raises(PathRejected):
        vault.quarantine_note("x1", "0-INBOX/Inbox-Triage.md", FLAGS)
    with pytest.raises(PathRejected):
        vault.quarantine_note("x2", "0-INBOX/_quarantine/other.md", FLAGS)


def test_release_restores_the_exact_note_and_requeues_the_row(vault, synthetic_vault):
    item, res = _quarantine(vault)
    out = vault.owner_release_quarantine(item)
    assert out["state"] == "pending" and out["restored"] == "0-INBOX/odd.md"
    assert out["content"] == NOTE and out["flags"] == ["canary_leak"]
    assert (synthetic_vault / "0-INBOX/odd.md").read_text(encoding="utf-8") == NOTE
    assert not (synthetic_vault / res["path"]).exists()


def test_release_is_refused_before_anything_changes_when_the_path_is_taken(vault, synthetic_vault):
    item, res = _quarantine(vault)
    (synthetic_vault / "0-INBOX/odd.md").write_text("new note", encoding="utf-8")
    with pytest.raises(NoteExists):
        vault.owner_release_quarantine(item)
    assert vault.read_triage()["rows"][0]["state"] == "quarantined"
    assert (synthetic_vault / res["path"]).exists()


def test_release_of_an_item_that_is_not_quarantined_is_refused(vault):
    vault.update_triage([{"resource": "0-INBOX/example-tool.md", "ai_suggested_category": "tool"}])
    with pytest.raises(VaultError):
        vault.owner_release_quarantine(item_id_for("0-INBOX/example-tool.md"))
