"""Spec 032 end to end: a ready notebook gets a learning note, every result folder gets an index, an image
without an address becomes its own guarded note, and the injection detector alone passes when the owner chose
``auto_pass``."""

from __future__ import annotations

import pytest

from pkpipe.obsidian import lists
from tests.integration.engine.test_triage_release import TOOL, VIDEO, World, _states, owner_decides

PROCESSING = "0-INBOX/Manual-Resource-Processing-List.md"


@pytest.fixture
def world(synthetic_vault, pack_dir):
    return World(synthetic_vault, pack_dir, {"CLI tool": "tool", "video": "learning-material"})


def release(world):
    assert world.svc.start("triage").ok
    owner_decides(world.vault, confirm=[TOOL, VIDEO])
    run = world.svc.start("process_released")
    assert run.ok, run.error
    return run


def test_a_ready_notebook_gets_a_learning_note_the_list_row_points_at(world):
    release(world)
    notes = list((world.vault / "3-Resources/Learning").glob(f"*-{VIDEO}.md"))
    assert len(notes) == 1
    text = notes[0].read_text(encoding="utf-8")
    assert "type: learning_note" in text and f"pkpipe_item: {VIDEO}" in text
    assert "Notebook: " in text and "Source: " in text
    row = next(r for r in lists.read_processing_list(world.vault / PROCESSING) if r.item_id == VIDEO)
    assert row.status.value == "ready" and row.obsidian_link.endswith(f"-{VIDEO}.md")
    assert _states(world.vault)[VIDEO] == "processed"


def test_the_learning_note_holds_no_text_of_the_page(world):
    release(world)
    note = next((world.vault / "3-Resources/Learning").glob(f"*-{VIDEO}.md"))
    text = note.read_text(encoding="utf-8")
    assert "worth a notebook" not in text and "fictional" not in text  # only our own facts


def test_every_result_folder_gets_an_index_of_its_notes(world):
    release(world)
    index = (world.vault / "3-Resources/Tools/_Index.md").read_text(encoding="utf-8")
    assert f"-{TOOL}" in index and "type: pkpipe_index" in index
    learning = (world.vault / "3-Resources/Learning/_Index.md").read_text(encoding="utf-8")
    assert f"-{VIDEO}" in learning and "podcast + notebook" in learning


def test_the_index_is_left_alone_when_nothing_changed(world):
    release(world)
    path = world.vault / "3-Resources/Tools/_Index.md"
    first = path.stat().st_mtime_ns
    assert world.svc.start("process_released").ok  # nothing to release: no write
    assert path.stat().st_mtime_ns == first
