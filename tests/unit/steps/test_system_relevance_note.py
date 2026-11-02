"""Spec 016 US1 / SC-001: what ``category.note`` makes of the system relevance session (code decides)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pkpipe.steps import CODE
from tests.conftest import REPO
from tests.support.flows import make_snapshot

SYSTEM = (REPO / "packs/learning-inbox/contexts/system.md").read_text(encoding="utf-8")
FIT = {
    "relevance": "candidate",
    "component": "Scheduler",
    "benefit": "Missed runs are replayed.",
    "effort": "M",
}


def _ctx(with_context=True):
    assets = {"contexts/system.md": SYSTEM} if with_context else {}
    snap = make_snapshot(
        {"id": "x", "start": "a", "nodes": [{"id": "a", "type": "code", "config": {"function": "item.set"}}]},
        assets=assets,
        categories=[
            {
                "id": "tool",
                "label": "T",
                "subflow": "other_resource",
                "para_path": "3-Resources/Tools",
                "triage_hint": "x",
                "sessions": [],
            }  # fmt: skip
        ],
    )
    item = {"id": "it1", "path": "0-INBOX/x.md", "category": "tool", "para_path": "3-Resources/Tools"}
    return SimpleNamespace(item=item, snapshot=snap)


def _sessions(relevance=FIT, name="Chronos-lite"):
    outputs = {
        "research": {"name": name},
        "synthesize": {"title": name, "summary": "S.", "recommendation": "trial"},
    }
    if relevance is not None:
        outputs["system_relevance"] = relevance
    return {"note_session": "synthesize", "skipped": [], "sessions": outputs}


def test_a_candidate_is_added_to_the_note_and_the_rows_to_write():
    out = CODE["category.note"](_ctx(), sessions=_sessions(), candidates={"rows": []})
    assert out["has_candidate"] and out["system_relevance"] == "candidate"
    assert (
        "## System relevance\n\nCandidate for Scheduler (effort M): Missed runs are replayed." in out["body"]
    )
    [row] = out["candidate_rows"]
    assert row["tool"] == "Chronos-lite" and row["component"] == "Scheduler" and row["effort"] == "M"
    assert row["evaluation_note"] == out["path"]  # the row links to the evaluation note


def test_a_rejected_tool_is_not_listed_again_and_the_note_says_so():
    """SC-001: a rejected candidate is not re-proposed (no new row, no candidate claim in the note)."""
    existing = {"rows": [{"id": "chronoslite", "status": "rejected", "reason": "worse"}]}
    out = CODE["category.note"](_ctx(), sessions=_sessions(), candidates=existing)
    assert not out["has_candidate"] and out["candidate_rows"] == []
    assert "Rejected earlier as a system tool" in out["body"] and "Candidate for" not in out["body"]


def test_an_adopted_or_in_use_tool_is_marked_in_use():
    adopted = {"rows": [{"id": "chronoslite", "status": "adopted"}]}
    out = CODE["category.note"](_ctx(), sessions=_sessions(), candidates=adopted)
    assert "In use: this tool was adopted" in out["body"] and not out["has_candidate"]
    out = CODE["category.note"](_ctx(), sessions=_sessions(name="Lightpanda"), candidates={"rows": []})
    assert "In use: pkpipe already uses this tool" in out["body"] and not out["has_candidate"]


def test_a_component_outside_the_system_context_is_never_listed():
    """The model may only name components of the context: an invented one is dropped and said so."""
    out = CODE["category.note"](
        _ctx(), sessions=_sessions({**FIT, "component": "Mainframe"}), candidates={"rows": []}
    )
    assert not out["has_candidate"] and "not part of the system context" in out["body"]


@pytest.mark.parametrize("relevance", [None, {"skipped": "context_missing"}])
def test_without_a_relevance_session_or_context_the_note_has_no_section(relevance):
    out = CODE["category.note"](_ctx(), sessions=_sessions(relevance), candidates={"rows": []})
    assert "System relevance" not in out["body"] and not out["has_candidate"] and out["candidate_rows"] == []


def test_relevance_none_adds_nothing():
    out = CODE["category.note"](
        _ctx(), sessions=_sessions({"relevance": "none", "component": "", "benefit": "", "effort": "S"})
    )
    assert "System relevance" not in out["body"] and not out["has_candidate"]


def test_without_the_system_context_nothing_is_a_candidate():
    """A checkout whose config lacks contexts/system.md has no component list: no row can be valid."""
    out = CODE["category.note"](_ctx(with_context=False), sessions=_sessions(), candidates=None)
    assert not out["has_candidate"] and "not part of the system context" in out["body"]
