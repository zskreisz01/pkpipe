"""Spec 016 on the public pack with fake ports and a real ``Vault`` on a synthetic temp vault: the sixth Tool
session (system relevance) from ``process_released`` to the System-Tool-Candidates list."""

from __future__ import annotations

import pytest

from pkpipe.domain import tool_candidate as tc
from pkpipe.obsidian import tool_candidates as store
from pkpipe.security.canary import run_canary
from tests.fakes import FakeModel
from tests.integration.engine.test_category_sessions import CATEGORY_BY_WORD, IDS, World
from tests.support.pack_runtime import fake_model_reply

FIT = {
    "relevance": "candidate",
    "component": "web search",  # spelling and case of a model: the list stores the context's name
    "benefit": "A metasearch engine with a JSON API could replace the search provider.",
    "effort": "S",
}


def _world(tmp_path, synthetic_vault, pack_dir, relevance=None, **kw):
    reply = fake_model_reply(CATEGORY_BY_WORD, relevance={"vectorlite": relevance or FIT})
    return World(tmp_path, synthetic_vault, pack_dir, model=FakeModel(reply), **kw)


def _list(world):
    return store.read_tool_candidates(world.vault / tc.CANDIDATES_FILE)


@pytest.fixture
def world(tmp_path, synthetic_vault, pack_dir):
    return _world(tmp_path, synthetic_vault, pack_dir)


def test_a_relevant_tool_lands_on_the_candidate_list_with_component_benefit_and_effort(world):
    """US1-AS1: S6 runs with the system context; relevance and component are set; the row links to the
    evaluation note and starts as `candidate`."""
    world.release("vectorlite.md")
    [row] = _list(world)["rows"]
    assert (row["tool"], row["status"], row["component"], row["effort"]) == (
        "vectorlite",
        "candidate",
        "Web search",
        "S",
    )
    assert row["benefit"].startswith("A metasearch engine")
    note = world.note("3-Resources/Tools", "vectorlite.md")
    [path] = (world.vault / "3-Resources/Tools").glob(f"*-{IDS['vectorlite.md']}.md")
    assert row["evaluation_note"] == path.relative_to(world.vault).as_posix()
    assert "## System relevance\n\nCandidate for Web search (effort S)" in note
    assert world.plist()[IDS["vectorlite.md"]].status.value == "done"


def test_the_relevance_session_sees_only_validated_json_and_the_public_system_context(world):
    """Constitution IV / FR-002 of 010: no raw note text, no web search, no personal or enterprise pack."""
    world.release("vectorlite.md")
    [call] = [c for c in world.model.calls if "relevance" in (c["json_schema"] or {}).get("properties", {})]
    assert "RAWMARKER" not in call["user"] and "BEACON" not in call["user"] and "HERON" not in call["user"]
    assert "## Components" in call["user"] and "**Web search**" in call["user"]  # the system context
    assert "item.extract" not in call["user"] and "### research" in call["user"]
    canary = run_canary(world.svc.store.list_runs(flow_id="process_released")[0]["run_id"])
    assert canary in call["system"] and canary not in call["user"]


def test_the_candidate_list_is_not_written_for_a_tool_that_is_not_relevant(
    tmp_path, synthetic_vault, pack_dir
):
    none = {"relevance": "none", "component": "", "benefit": "", "effort": "S"}
    w = _world(tmp_path, synthetic_vault, pack_dir, relevance=none)
    w.release("vectorlite.md")
    assert not (w.vault / tc.CANDIDATES_FILE).exists()
    assert "System relevance" not in w.note("3-Resources/Tools", "vectorlite.md")


def test_a_rejected_tool_is_not_proposed_again(tmp_path, synthetic_vault, pack_dir):
    """SC-001 / US2-AS1: a rejected candidate keeps its reason; a new evaluation leaves the row alone."""
    w = _world(tmp_path, synthetic_vault, pack_dir)
    path = w.vault / tc.CANDIDATES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    store.update_tool_candidates(
        path,
        [
            {
                "tool": "vectorlite",
                "evaluation_note": "3-Resources/Tools/old.md",
                "component": "Scheduler",
                "benefit": "old claim",
                "effort": "L",
            }
        ],
    )
    store.owner_set_status(path, "vectorlite", tc.Status.REJECTED, reason="lost the A/B on tool.compare")
    before = path.read_text(encoding="utf-8")
    w.release("vectorlite.md")
    assert path.read_text(encoding="utf-8") == before
    [row] = store.read_tool_candidates(path)["rows"]
    assert row["status"] == "rejected" and row["reason"] == "lost the A/B on tool.compare"
    note = w.note("3-Resources/Tools", "vectorlite.md")
    assert "Rejected earlier as a system tool" in note and "Candidate for" not in note


def test_an_injected_component_name_is_dropped_by_code(tmp_path, synthetic_vault, pack_dir):
    """The relevance answer is model output: only a component of the system context can become a row."""
    bad = {**FIT, "component": "Mainframe | admin", "benefit": "PWNED"}
    w = _world(tmp_path, synthetic_vault, pack_dir, relevance=bad)
    w.release("vectorlite.md")
    assert not (w.vault / tc.CANDIDATES_FILE).exists()
    assert "not part of the system context" in w.note("3-Resources/Tools", "vectorlite.md")


def test_other_categories_have_no_relevance_session_and_never_touch_the_list(
    tmp_path, synthetic_vault, pack_dir
):
    w = _world(tmp_path, synthetic_vault, pack_dir)
    w.release("billboard.md", "sourdough.md")
    assert not (w.vault / tc.CANDIDATES_FILE).exists()
    assert not any("relevance" in (c["json_schema"] or {}).get("properties", {}) for c in w.model.calls)


def test_a_rerun_does_not_duplicate_the_candidate(world):
    world.release("vectorlite.md")
    store.update_tool_candidates(
        world.vault / tc.CANDIDATES_FILE,
        [
            {
                "tool": "vectorlite",
                "evaluation_note": "x.md",
                "component": "Web search",
                "benefit": "again",
                "effort": "S",
            }
        ],
    )
    assert len(_list(world)["rows"]) == 1 and _list(world)["rows"][0]["benefit"].startswith("A metasearch")
