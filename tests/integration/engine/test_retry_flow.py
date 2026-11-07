"""Spec 027 end to end: an item fails, the cause is fixed, the owner retries, the next release run finishes
it.

Real vault files and the real flows with fake ports; the retry goes through the vault's owner method (what the
UI, the app MCP and the CLI call)."""

from __future__ import annotations

import pytest

from pkpipe.obsidian import lists
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault
from tests.integration.engine.test_category_sessions import (
    IDS,
    PAGES,
    PROCESSING_LIST,
    TRIAGE_FILE,
    World,
)

VECTORLITE = "https://example.invalid/vectorlite"


@pytest.fixture
def world(tmp_path, synthetic_vault, pack_dir):
    return World(tmp_path, synthetic_vault, pack_dir)


def owner_vault(world):
    return Vault(VaultPolicy(vault_dir=world.vault))


def states(world):
    return {r["item_id"]: r["state"] for r in lists.read_triage(world.vault / TRIAGE_FILE)["rows"]}


def test_a_failed_item_is_retried_after_the_cause_is_fixed_and_ends_done_without_a_duplicate_line(world):
    item = IDS["vectorlite.md"]
    world.web.pages.pop(VECTORLITE)  # the page cannot be fetched: the item fails
    world.release("vectorlite.md")
    assert world.plist()[item].status.value == "failed" and states(world)[item] == "processed"
    assert world.svc.start("process_released").ok  # a second run: nothing changes, the item stays failed
    assert states(world)[item] == "processed"

    world.web.pages[VECTORLITE] = PAGES[VECTORLITE]  # the cause is fixed
    out = owner_vault(world).owner_retry(item, retries=0, max_retries=3)
    assert out["state"] == "released" and world.plist()[item].status.value == "unprocessed"
    run = world.svc.start("process_released")
    assert run.ok, run.error

    assert states(world)[item] == "processed" and world.plist()[item].status.value == "done"
    text = (world.vault / PROCESSING_LIST).read_text(encoding="utf-8")
    assert sum(1 for ln in text.splitlines() if ln.startswith(f"| {item} |")) == 1  # one line, no duplicate
    assert world.note("3-Resources/Tools", "vectorlite.md")  # the note landed in its PARA folder


def test_the_owner_decision_survives_the_retry(world):
    item = IDS["vectorlite.md"]
    world.web.pages.pop(VECTORLITE)
    world.release("vectorlite.md")
    owner_vault(world).owner_retry(item, retries=0, max_retries=3)
    row = next(r for r in lists.read_triage(world.vault / TRIAGE_FILE)["rows"] if r["item_id"] == item)
    assert row["confirm"] is True and row["state"] == "released"


def test_the_run_report_counts_failed_items_as_a_todo_with_a_link(world):
    world.web.pages.pop(VECTORLITE)
    world.release("vectorlite.md")
    reports = [x for x in world.notify.sent if x.event == "daily_summary"]
    assert reports and "Retry failed items" in str(reports[-1].payload)
