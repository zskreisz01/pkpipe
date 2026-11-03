"""Spec 016: the rules of system tool candidates (FR-001, FR-002, SC-001) and the system context parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from pkpipe.domain import tool_candidate as tc
from tests.conftest import REPO

CONTEXT = (REPO / "packs/learning-inbox/contexts/system.md").read_text(encoding="utf-8")
COMPONENTS = tc.context_components(CONTEXT)
IN_USE = tc.context_in_use(CONTEXT)
ANSWER = {"relevance": "candidate", "component": "web search", "benefit": "Faster queries.", "effort": "s"}


def _propose(output=ANSWER, **kw):
    return tc.propose(
        output,
        tool=kw.pop("tool", "Metasea"),
        evaluation_note="3-Resources/Tools/metasea-1.md",
        components=COMPONENTS,
        in_use=IN_USE,
        **kw,
    )


def _row(status="candidate", **kw) -> tc.ToolCandidate:
    base = {
        "id": "metasea",
        "tool": "Metasea",
        "evaluation_note": "3-Resources/Tools/metasea-1.md",
        "component": "Web search",
        "benefit": "Faster queries.",
        "effort": "S",
        "status": status,
    }
    return tc.ToolCandidate(**{**base, **kw})


def test_the_public_system_context_names_components_and_the_tools_in_use():
    """FR-003 of spec 011 + spec 016: the pack's system.md is machine readable for the relevance check."""
    assert "Web search" in COMPONENTS and "Scheduler" in COMPONENTS and "Injection classifier" in COMPONENTS
    assert "Lightpanda" in IN_USE and "LangGraph" in IN_USE and "Web search" not in IN_USE
    assert len(COMPONENTS) >= 10


def test_a_relevant_tool_becomes_a_candidate_row_with_the_exact_component_name():
    """US1-AS1: relevance, component, benefit and effort are set; the component is the context's spelling."""
    p = _propose()
    assert p.action == "add" and p.relevance == "candidate"
    assert p.row == {
        "id": "metasea",
        "tool": "Metasea",
        "evaluation_note": "3-Resources/Tools/metasea-1.md",
        "component": "Web search",
        "benefit": "Faster queries.",
        "effort": "S",
    }


@pytest.mark.parametrize(
    ("output", "reason"),
    [
        ({"relevance": "none", "component": "", "benefit": "", "effort": "S"}, "relevance_none"),
        ({"relevance": "in_use", "component": "", "benefit": "", "effort": "S"}, "in_use"),
        ({**ANSWER, "component": "Mainframe"}, "unknown_component"),
        ({**ANSWER, "effort": "XL"}, "invalid"),
        ({**ANSWER, "benefit": "  "}, "invalid"),
        ({**ANSWER, "relevance": "maybe"}, "invalid"),
        (None, "relevance_none"),
    ],
)
def test_anything_but_a_complete_candidate_for_a_known_component_is_not_listed(output, reason):
    """Code decides: an invented component, a bad effort or an unknown relevance never reaches the list."""
    p = _propose(output)
    assert p.action == "skip" and p.reason == reason and p.row is None


def test_a_tool_the_system_already_uses_is_in_use_even_when_the_model_says_candidate():
    """Edge case: tool already adopted / in use -> S6 marks it as `in use`."""
    p = _propose(tool="lightpanda")
    assert (p.action, p.reason, p.relevance) == ("skip", "in_use", "in_use")
    adopted = {"metasea": {"status": "adopted"}}
    p = _propose(existing=adopted)
    assert (p.action, p.reason, p.relevance) == ("skip", "adopted", "in_use")


def test_a_rejected_tool_is_not_proposed_again_and_a_listed_one_is_not_added_twice():
    """SC-001: a rejected candidate is not re-proposed without new evidence (only the owner supplies it)."""
    p = _propose(existing={"metasea": {"status": "rejected"}})
    assert (p.action, p.reason) == ("skip", "rejected")
    for status in ("candidate", "investigating", "trial"):
        assert _propose(existing={"metasea": {"status": status}}).reason == "listed"


def test_tool_identity_ignores_case_spacing_and_punctuation():
    assert tc.candidate_id("Vector-Lite") == tc.candidate_id("vector lite") == "vectorlite"
    assert _propose(tool="Meta Sea", existing={"metasea": {"status": "rejected"}}).reason == "rejected"


def test_names_from_the_web_cannot_break_the_table_or_the_list():
    """Values derive from web content: pipes, line breaks and control characters are removed."""
    p = _propose({**ANSWER, "benefit": "Line one\n| injected | cell |\x00 end"}, tool="Evil|Tool\nName")
    assert p.row is not None and "|" not in p.row["tool"] + p.row["benefit"] and "\n" not in p.row["benefit"]


def test_statuses_follow_the_lifecycle():
    """FR-001: candidate -> investigating -> trial -> adopted | rejected."""
    assert tc.check_transition(_row(), tc.Status.INVESTIGATING) is None
    assert tc.check_transition(_row("investigating"), tc.Status.TRIAL) is None
    assert "not allowed" in tc.check_transition(_row(), tc.Status.TRIAL)  # no skipping the investigation
    assert "not allowed" in tc.check_transition(_row(), tc.Status.ADOPTED)
    assert "not allowed" in tc.check_transition(
        _row("adopted"), tc.Status.CANDIDATE
    )  # nothing leaves adopted
    assert tc.check_transition(_row("trial"), tc.Status.REJECTED, reason="lost the A/B") is None


def test_a_rejection_needs_a_reason_and_a_reopening_needs_new_evidence():
    assert "reason" in tc.check_transition(_row("investigating"), tc.Status.REJECTED)
    assert "reason" in tc.check_transition(_row("investigating"), tc.Status.REJECTED, reason="  ")
    rejected = _row("rejected", reason="worse")
    assert "new evidence" in tc.check_transition(rejected, tc.Status.CANDIDATE)
    assert tc.check_transition(rejected, tc.Status.CANDIDATE, evidence="v2 fixes the leak") is None
    # rejected again after a reopening: the same evidence is not new evidence
    twice = _row("rejected", reason="still worse", evidence="v2 fixes the leak")
    assert "new evidence" in tc.check_transition(twice, tc.Status.CANDIDATE, evidence=" V2 fixes the  leak")
    assert (
        tc.check_transition(twice, tc.Status.CANDIDATE, evidence="v3 passes the A/B on tool.compare") is None
    )


def test_adoption_needs_an_adr_and_a_winning_green_ab():
    """FR-002: an ADR and a green eval gate, from an A/B the candidate won."""
    trial = _row("trial")
    better = tc.AbEvidence(tool="Metasea", verdict="better", gate_green=True)
    assert tc.check_transition(trial, tc.Status.ADOPTED, adr_exists=True, ab=better) is None
    assert "ADR" in tc.check_transition(trial, tc.Status.ADOPTED, adr_exists=False, ab=better)
    assert "A/B report" in tc.check_transition(trial, tc.Status.ADOPTED, adr_exists=True)
    for verdict in ("equal", "worse", "inconclusive"):
        ab = tc.AbEvidence(tool="Metasea", verdict=verdict, gate_green=True)
        assert "needs 'better'" in tc.check_transition(trial, tc.Status.ADOPTED, adr_exists=True, ab=ab)
    red = tc.AbEvidence(tool="Metasea", verdict="better", gate_green=False)
    assert "gate" in tc.check_transition(trial, tc.Status.ADOPTED, adr_exists=True, ab=red)
    other = tc.AbEvidence(tool="Other", verdict="better", gate_green=True)
    assert "not 'Metasea'" in tc.check_transition(trial, tc.Status.ADOPTED, adr_exists=True, ab=other)
    unnamed = tc.AbEvidence(tool="", verdict="better", gate_green=True)  # a report that names no tool
    assert "not 'Metasea'" in tc.check_transition(trial, tc.Status.ADOPTED, adr_exists=True, ab=unnamed)


def test_hand_edited_rows_that_break_the_rules_are_reported():
    assert tc.row_violations(_row("rejected")) == ["rejected without a reason"]
    assert tc.row_violations(_row("rejected", reason="worse")) == []
    adopted = tc.row_violations(_row("adopted"))
    assert "adopted without an ADR" in adopted and "adopted without an A/B result 'better'" in adopted
    assert tc.row_violations(_row("adopted", adr="0002-adopt-metasea.md", ab="better (2 metrics)")) == []


def test_adr_numbers_and_file_names_continue_the_directory(tmp_path):
    assert tc.next_adr_number(["0001-injection-classifier.md", "_category_.json", "template.md"]) == 2
    assert tc.next_adr_number([]) == 1
    assert tc.adr_filename(2, "Meta Sea!") == "0002-adopt-meta-sea.md"


def test_the_adr_is_filled_from_the_row_and_the_report():
    text = tc.render_adr(
        number=2,
        row=_row("trial"),
        today="2026-11-02",
        ab={
            "verdict": "better",
            "gate_green": True,
            "reasons": ["sessions.judge_mean 4.0 -> 4.5"],
            "metrics": [
                {
                    "suite": "sessions",
                    "metric": "judge_mean",
                    "baseline": 4.0,
                    "candidate": 4.5,
                    "verdict": "better",
                }
            ],
            "pairwise": [{"session": "tool.compare", "score": 0.75}],
        },
    )
    assert (
        text.startswith('---\ntitle: "0002 Adopt Metasea"') and "# 0002: Adopt Metasea for Web search" in text
    )
    assert "| sessions.judge_mean | 4.0 | 4.5 | better |" in text and "tool.compare: 0.75" in text
    assert "## Context" in text and "## Decision" in text and "## Consequences" in text


def test_the_adr_template_and_spike_checklist_exist_in_the_adr_directory():
    """T003: template + checklist ship with the docs."""
    adr_dir: Path = REPO / tc.ADR_DIR
    template = (adr_dir / "template.md").read_text(encoding="utf-8")
    for heading in ("## Context", "## Decision", "## Consequences", "## Spike checklist"):
        assert heading in template
