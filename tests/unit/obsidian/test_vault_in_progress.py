"""Spec 031: ``inbox_in_progress`` moves unfinished entries into the table, archives closed ones, restores
reopened ones, and never loses an entry. ``read_in_progress`` / ``read_processing_list`` are read only."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pkpipe.domain.inbox_list import parse_list
from pkpipe.obsidian.errors import PathRejected
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "inbox" / "INBOX-list.md"
DAY = date(2026, 11, 10)
ALPHA = "https://example.org/articles/alpha"


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for f in ("1-Inbox", "6-Archive/Inbox", "3-Areas"):
        (root / f).mkdir(parents=True)
    (root / "1-Inbox" / "INBOX.md").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    policy = VaultPolicy(
        vault_dir=root,
        ui_public_url="http://ui.test:8787",
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


def _list(v) -> str:
    return (v.policy.root / "1-Inbox" / "INBOX.md").read_text(encoding="utf-8")


def _ids(v) -> dict[str, dict]:
    return {r["item_id"]: r for r in v.read_in_progress()["rows"]}


def _item_id(v) -> str:
    return next(e.item_id for e in parse_list(_list(v)) if e.resource.endswith("articles/alpha"))


def _row(item_id, **kw):
    return {
        "item_id": item_id,
        "kind": "link",
        "resource": ALPHA,
        "category": "tool",
        "state": "processed",
        "situation": "failed once",
        "cause": "no_source_found",
        "next_step": "add a link",
        "last_run": "run1",
        "facts": "f1",
        "move_entry": True,
        **kw,
    }


def test_an_unfinished_entry_moves_into_its_row_and_nothing_else_in_the_list_changes(vault):
    before, item = _list(vault), _item_id(vault)
    out = vault.inbox_in_progress([_row(item, since="2026-11-10")])
    assert out["moved"] >= 1 and out["upserted"] == 1
    row = _ids(vault)[item]
    assert row["entry"].startswith("- ") and ALPHA in row["entry"] and row["since"] == "2026-11-10"
    after = _list(vault)
    assert ALPHA not in after
    removed = [e.raw for e in parse_list(before) if e.item_id == item]
    for raw in removed:  # exactly those lines are gone, every other byte stays
        before = before.replace(raw + "\n", "", 1)
    assert after == before
    assert "[history](http://ui.test:8787/items/" + item in (
        vault.policy.root / "1-Inbox/INBOX-InProgress.md"
    ).read_text(encoding="utf-8")
    assert not list((vault.policy.root / "6-Archive/Inbox").glob("*.md"))  # moved is not archived


def test_the_same_rows_again_change_nothing_and_a_row_keeps_its_since(vault):
    item = _item_id(vault)
    vault.inbox_in_progress([_row(item, since="2026-11-01")])
    table = (vault.policy.root / "1-Inbox/INBOX-InProgress.md").read_text(encoding="utf-8")
    list_after = _list(vault)
    vault.inbox_in_progress([_row(item, since="2026-11-10")])  # entry already moved: tolerated
    assert (vault.policy.root / "1-Inbox/INBOX-InProgress.md").read_text(encoding="utf-8") == table
    assert _list(vault) == list_after and _ids(vault)[item]["since"] == "2026-11-01"


def test_a_closed_item_leaves_the_table_and_its_kept_entry_is_archived(vault):
    item = _item_id(vault)
    vault.inbox_in_progress([_row(item)])
    kept = _ids(vault)[item]["entry"]
    out = vault.inbox_in_progress([], archive_ids=[item])
    assert out["archived"] >= 1 and item not in _ids(vault)
    (archive,) = (vault.policy.root / "6-Archive/Inbox").glob("Inbox-Archive-2026-11-10.md")
    assert kept.split("\x1e")[0] in archive.read_text(encoding="utf-8")


def test_a_reopened_item_gets_its_entry_back_once(vault):
    item = _item_id(vault)
    vault.inbox_in_progress([_row(item)])
    kept = _ids(vault)[item]["entry"]
    vault.inbox_in_progress([], restore_ids=[item])
    assert item not in _ids(vault) and _list(vault).count(kept.split("\x1e")[0]) == 1
    vault.inbox_in_progress([_row(item)])  # moved again ...
    vault.inbox_in_progress([], restore_ids=[item])  # ... and back: still one entry
    assert _list(vault).count(kept.split("\x1e")[0]) == 1


def test_closing_an_item_without_a_kept_entry_only_drops_the_row(vault):
    vault.inbox_in_progress([_row("c0ffee000001", move_entry=False, kind="note")])
    out = vault.inbox_in_progress([], archive_ids=["c0ffee000001"])
    assert out["archived"] == 0 and "c0ffee000001" not in _ids(vault)


def test_a_note_item_gets_a_row_without_touching_the_list(vault):
    before = _list(vault)
    vault.inbox_in_progress([_row("c0ffee000002", move_entry=False, kind="note")])
    assert _list(vault) == before and _ids(vault)["c0ffee000002"]["entry"] == ""


def test_the_read_tools_return_ids_codes_and_sentences_only(vault):
    item = _item_id(vault)
    vault.inbox_in_progress([_row(item)])
    got = vault.read_in_progress()
    assert got["count"] == 1 and got["file"] == "1-Inbox/INBOX-InProgress.md"
    vault.upsert_processing_list(
        [{"item_id": item, "category": "tool", "obsidian_link": "x", "link": "r", "status": "failed"}]
    )
    (line,) = vault.read_processing_list()["items"]
    assert line["item_id"] == item and line["status"] == "failed" and line["link"] == "r"


def test_the_table_file_is_managed_and_cannot_be_written_by_the_note_tools(vault):
    vault.inbox_in_progress([_row(_item_id(vault))])
    with pytest.raises(PathRejected):
        vault.write_note("1-Inbox/INBOX-InProgress.md", "x", overwrite=True)
    with pytest.raises(PathRejected):  # the list file is no table of the note tools either
        vault.write_note("1-Inbox/INBOX.md", "x", overwrite=True)


def test_a_bad_row_id_is_skipped_not_written(vault):
    out = vault.inbox_in_progress([_row("../evil")])
    assert out["upserted"] == 0 and _ids(vault) == {}
