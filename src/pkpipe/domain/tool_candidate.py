"""System tool candidates (spec 016): a tool evaluation may show that the tool could improve pkpipe itself.

The sixth Tool session (``system_relevance``, context ``system``) only *proposes*. Code decides:

* what reaches the candidate list (:func:`propose`): a known component of the system context, a clean tool
  name, never a tool that is already in use or adopted, never a rejected tool without new evidence (SC-001);
* which status changes are possible and what each needs (:func:`check_transition`): FR-001 candidate ->
  investigating -> trial -> adopted | rejected, FR-002 adoption needs an ADR and a green A/B on the
  eval suite, a rejection needs a reason, a rejected tool comes back only with new evidence;
* the pipeline never changes the status of an existing row (constitution VI): the owner does.

This module is pure; the vault file is :mod:`pkpipe.obsidian.tool_candidates`, the A/B comparison
:mod:`pkpipe.evals.ab`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from pkpipe.domain.layout import DEFAULT_LAYOUT

#: The one ADR directory of the repository (docs/decisions, the folder of ADR 0001).
ADR_DIR = "docs/decisions"
#: Vault file the candidates are written to (``VaultPolicy.tool_candidates_file``).
CANDIDATES_FILE = DEFAULT_LAYOUT.tool_candidates_file
#: Value of the ``relevance`` field of the system relevance session.
RELEVANCE_NONE, RELEVANCE_CANDIDATE, RELEVANCE_IN_USE = "none", "candidate", "in_use"
RELEVANCE = (RELEVANCE_NONE, RELEVANCE_CANDIDATE, RELEVANCE_IN_USE)
_FIELD_LIMITS = {"tool": 120, "component": 80, "benefit": 300, "evaluation_note": 300}


class Effort(StrEnum):
    S = "S"
    M = "M"
    L = "L"


class Status(StrEnum):
    CANDIDATE = "candidate"
    INVESTIGATING = "investigating"
    TRIAL = "trial"
    ADOPTED = "adopted"
    REJECTED = "rejected"


#: FR-001: owner status changes. ``rejected -> candidate`` additionally needs new evidence; nothing leaves
#: ``adopted`` (retiring an adopted tool is a new decision record, not a status change).
TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.CANDIDATE: frozenset({Status.CANDIDATE, Status.INVESTIGATING, Status.REJECTED}),
    Status.INVESTIGATING: frozenset({Status.INVESTIGATING, Status.CANDIDATE, Status.TRIAL, Status.REJECTED}),
    Status.TRIAL: frozenset({Status.TRIAL, Status.INVESTIGATING, Status.ADOPTED, Status.REJECTED}),
    Status.ADOPTED: frozenset({Status.ADOPTED}),
    Status.REJECTED: frozenset({Status.REJECTED, Status.CANDIDATE}),
}


def clean(text: Any, limit: int) -> str:
    """One line of printable text. Names and reasons derive from web content, so control characters, line
    breaks and table pipes are removed before anything is stored."""
    folded = unicodedata.normalize("NFKC", str(text or ""))
    folded = re.sub(r"[\x00-\x1f\x7f|]+", " ", folded)
    return " ".join(folded.split())[:limit].strip()


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:60].strip("-")


def tool_key(tool: str) -> str:
    """Identity of a tool across evaluations: ``Vector-Lite`` and ``vector lite`` are one tool."""
    return re.sub(
        r"[^a-z0-9]+", "", unicodedata.normalize("NFKD", tool).encode("ascii", "ignore").decode().lower()
    )


class ToolCandidate(BaseModel):
    """One row of ``System-Tool-Candidates.md`` (data-model ``ToolCandidate``)."""

    id: str
    tool: str = Field(min_length=1, max_length=120)
    evaluation_note: str = Field(description="Link to the evaluation note (vault path)")
    component: str = Field(min_length=1, max_length=80, description="C4 component name")
    benefit: str = Field(min_length=1, max_length=300)
    effort: Effort
    status: Status = Status.CANDIDATE
    reason: str = ""
    adr: str = ""
    ab: str = ""
    evidence: str = ""

    @field_validator("effort", mode="before")
    @classmethod
    def _effort(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


#: Fields only the owner changes on an existing row; the pipeline writes none of them.
OWNER_FIELDS = ("status", "reason", "adr", "ab", "evidence")


def candidate_id(tool: str) -> str:
    return tool_key(tool)[:60] or "tool"


# -- the system context ----------------------------------------------------------------------------------
_BULLET = re.compile(r"^\s*[-*]\s+(?:\*\*(?P<bold>[^*]+)\*\*|(?P<plain>[^:(]+?))\s*(?:[:(].*)?$")


def _section(context: str, heading: str) -> list[str]:
    lines, out, inside = context.splitlines(), [], False
    for line in lines:
        if line.startswith("#"):
            inside = line.lstrip("# ").strip().lower() == heading
            continue
        if inside:
            out.append(line)
    return out


def _names(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        m = _BULLET.match(line)
        if m:
            name = (m.group("bold") or m.group("plain") or "").strip()
            if name:
                out.append(name)
    return out


def context_components(context: str) -> list[str]:
    """Names in the ``## Components`` section of the system context (``- **Name**: ...``)."""
    return _names(_section(context, "components"))


def context_in_use(context: str) -> list[str]:
    """Tools the system already uses (``## In use`` section): evaluating one of them says ``in use``."""
    return _names(_section(context, "in use"))


def match_component(name: str, components: Collection[str]) -> str | None:
    """The exact component name for a model's answer (case, punctuation and spacing insensitive), or None."""
    want = tool_key(name)
    for comp in components:
        if tool_key(comp) == want:
            return comp
    return None


# -- proposing a candidate -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Proposal:
    """What the pipeline does with the system relevance output: ``add`` a row or ``skip`` with a reason."""

    action: str  # add | skip
    reason: str  # relevance_none | in_use | adopted | rejected | listed | unknown_component | invalid | add
    row: dict[str, Any] | None = None
    relevance: str = RELEVANCE_NONE  # what the note says: none | candidate | in_use


def propose(
    output: Mapping[str, Any] | None,
    *,
    tool: str,
    evaluation_note: str,
    components: Collection[str],
    in_use: Collection[str] = (),
    existing: Mapping[str, Mapping[str, Any]] | None = None,
) -> Proposal:
    """Decide what the system relevance output becomes. ``existing`` maps candidate id to its row (status).

    Order matters: a tool the system already uses is never a candidate, an adopted tool is ``in use``, a
    rejected tool is not proposed again (SC-001: only the owner reopens it, with new evidence), a tool that
    is already listed is not added twice."""
    out = output or {}
    name = clean(tool, _FIELD_LIMITS["tool"])
    relevance = str(out.get("relevance") or RELEVANCE_NONE).strip().lower()
    if not name or relevance not in RELEVANCE:
        return Proposal("skip", "invalid")
    key = tool_key(name)
    if any(tool_key(u) == key for u in in_use):
        return Proposal("skip", "in_use", relevance=RELEVANCE_IN_USE)
    row = (existing or {}).get(candidate_id(name))
    if row is not None:
        status = str(row.get("status") or "")
        if status == Status.ADOPTED:
            return Proposal("skip", "adopted", relevance=RELEVANCE_IN_USE)
        if status == Status.REJECTED:
            return Proposal("skip", "rejected", relevance=RELEVANCE_NONE)
        return Proposal("skip", "listed", relevance=RELEVANCE_CANDIDATE)
    if relevance == RELEVANCE_IN_USE:
        return Proposal("skip", "in_use", relevance=RELEVANCE_IN_USE)
    if relevance == RELEVANCE_NONE:
        return Proposal("skip", "relevance_none", relevance=RELEVANCE_NONE)
    component = match_component(str(out.get("component") or ""), components)
    effort = str(out.get("effort") or "").strip().upper()
    benefit = clean(out.get("benefit"), _FIELD_LIMITS["benefit"])
    if component is None:
        return Proposal("skip", "unknown_component", relevance=RELEVANCE_NONE)
    if effort not in Effort.__members__ or not benefit:
        return Proposal("skip", "invalid", relevance=RELEVANCE_NONE)
    row_out = {
        "id": candidate_id(name),
        "tool": name,
        "evaluation_note": clean(evaluation_note, _FIELD_LIMITS["evaluation_note"]),
        "component": component,
        "benefit": benefit,
        "effort": effort,
    }
    return Proposal("add", "add", row_out, RELEVANCE_CANDIDATE)


# -- status changes ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AbEvidence:
    """What adoption needs from the A/B comparison (:class:`pkpipe.evals.ab.AbReport`)."""

    tool: str
    verdict: str  # better | equal | worse | inconclusive
    gate_green: bool
    summary: str = ""


def check_transition(
    old: ToolCandidate,
    new_status: Status,
    *,
    reason: str = "",
    evidence: str = "",
    adr_exists: bool = False,
    ab: AbEvidence | None = None,
) -> str | None:
    """Why ``old.status -> new_status`` is not allowed, or ``None``. The owner decides; code enforces."""
    if new_status not in TRANSITIONS[old.status]:
        return f"status change {old.status.value} -> {new_status.value} is not allowed"
    if new_status == old.status:
        return None
    if new_status is Status.REJECTED and not (reason or "").strip():
        return "a rejection needs a reason"
    if old.status is Status.REJECTED:
        if not (evidence or "").strip():
            return "a rejected tool is proposed again only with new evidence (SC-001): give the evidence"
        if old.evidence.strip() and clean(evidence, 300).casefold() == clean(old.evidence, 300).casefold():
            return "this evidence reopened the tool before: a rejected tool comes back only with new evidence"
    if new_status is Status.ADOPTED:
        return adoption_error(old, adr_exists=adr_exists, ab=ab)
    return None


def adoption_error(row: ToolCandidate, *, adr_exists: bool, ab: AbEvidence | None) -> str | None:
    """FR-002: adoption needs an ADR in ``docs/decisions/`` and an A/B on the eval suite that the candidate
    won with a green gate."""
    if not adr_exists:
        return f"adoption needs an ADR in {ADR_DIR}/ (pkpipe candidates adr)"
    if ab is None:
        return "adoption needs an A/B report that beats the current component (pkpipe candidates ab)"
    if not tool_key(ab.tool) or tool_key(ab.tool) != tool_key(row.tool):
        return f"the A/B report is for '{ab.tool}', not '{row.tool}'"
    if ab.verdict != "better":
        return f"the A/B comparison is '{ab.verdict}', adoption needs 'better'"
    if not ab.gate_green:
        return "the eval gate is not green for the candidate"
    return None


def row_violations(row: ToolCandidate) -> list[str]:
    """Problems of a row the owner edited by hand in Obsidian (reported by ``read_tool_candidates``)."""
    problems = []
    if row.status is Status.REJECTED and not row.reason.strip():
        problems.append("rejected without a reason")
    if row.status is Status.ADOPTED:
        if not row.adr.strip():
            problems.append("adopted without an ADR")
        if not row.ab.strip().lower().startswith("better"):
            problems.append("adopted without an A/B result 'better'")
    return problems


# -- ADR ----------------------------------------------------------------------------------------------------
ADR_NUMBER = re.compile(r"^(\d{4})-.+\.md$")


def next_adr_number(names: Collection[str]) -> int:
    nums = [int(m.group(1)) for n in names if (m := ADR_NUMBER.match(n))]
    return max(nums, default=0) + 1


def adr_filename(number: int, tool: str) -> str:
    return f"{number:04d}-adopt-{slug(tool) or 'tool'}.md"


def render_adr(
    *, number: int, row: ToolCandidate, today: str, ab: Mapping[str, Any], status: str = "Proposed"
) -> str:
    """The ADR of an adoption from the candidate row and the A/B report (the owner edits the prose)."""
    reasons = "\n".join(f"- {r}" for r in ab.get("reasons") or []) or "- none"
    metrics = ab.get("metrics") or []
    table = "\n".join(
        f"| {m['suite']}.{m['metric']} | {m['baseline']} | {m['candidate']} | {m['verdict']} |"
        for m in metrics
    )
    pairwise = ab.get("pairwise") or []
    pair_lines = "\n".join(f"- {p['session']}: {p['score']}" for p in pairwise) or "- none"
    return (
        f'---\ntitle: "{number:04d} Adopt {row.tool}"\n---\n\n'
        f"# {number:04d}: Adopt {row.tool} for {row.component}\n\n"
        f"- **Status:** {status}, {today}\n"
        f"- **Candidate:** {row.id} (evaluation note: {row.evaluation_note})\n"
        f"- **Component:** {row.component}\n"
        f"- **A/B verdict:** {ab.get('verdict')} (gate green: {ab.get('gate_green')})\n\n"
        "## Context\n\n"
        f"{row.benefit}\n\n"
        "## Decision\n\n"
        f"Replace or extend the current {row.component} with {row.tool} behind its adapter. The change is "
        "published through the eval gate.\n\n"
        "## Evidence (A/B on the eval suite)\n\n"
        f"{reasons}\n\n"
        "| Metric | Current | With the tool | Result |\n| --- | --- | --- | --- |\n"
        f"{table}\n\n"
        "Pairwise judge score of the candidate (0.5 = no difference):\n\n"
        f"{pair_lines}\n\n"
        "## Consequences\n\n"
        "- Effort: " + row.effort.value + "\n"
        "- Rollback: switch the configuration back to the previous component; the A/B report names both "
        "config versions.\n"
    )
