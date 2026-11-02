---
spec_id: 016-system-tool-candidates
title: System tool candidates
status: Completed
description: A sixth Tool session checks whether an evaluated tool could improve pkpipe itself; candidates go to a separate list and are adopted only after a spike that wins an A/B on the eval suite, recorded in an ADR.
relevant_when:
  - system relevance
  - tool candidates
  - ADR
  - spike
related: [010-category-sessions, 011-context-packs, 013-evaluation-framework]
updated: 2026-11-03
---

# Feature Specification: System tool candidates

**Feature Branch**: `spec/016-system-tool-candidates`
**Created**: 2026-10-31
**Status**: Completed
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [data-model.md](data-model.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Spot tools for the system (Priority: P1)

Relevant tools land on the candidate list with component, benefit and effort.

**Independent Test**: Session eval + flow test.

**Acceptance Scenarios**:

1. **Given** a tool evaluation, **When** S6 runs with the system context, **Then** relevance and component are set

### User Story 2 - Adopt with evidence (Priority: P2)

A candidate is adopted only after it beats the current component.

**Independent Test**: Process test.

**Acceptance Scenarios**:

1. **Given** a spike, **When** the A/B eval is worse, **Then** the candidate is rejected with reason and not re-proposed

### Edge Cases

- Tool already adopted → S6 marks it as `in use`.
- Rejected tool evaluated again → listed row untouched, the note says it was rejected; only the owner reopens it, with new evidence.
- A/B inconclusive (a suite or the judge unavailable) → never a win; the candidate stays in `trial`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Candidate statuses: candidate → investigating → trial → adopted | rejected.
- **FR-002**: Adoption requires an ADR in the ADR directory (`docs/decisions/`) and a green eval gate: an A/B report whose verdict is `better` with the candidate's gate green.
- **FR-003**: The A/B runner (`pkpipe.evals.ab`, `pkpipe candidates ab`) runs the same suites on the current and a candidate config and decides `better` / `equal` / `worse` / `inconclusive` in code.
- **FR-004**: The pipeline only adds candidate rows (status `candidate`); every status change is the owner's and checked by code (rejection needs a reason, adoption an ADR and a won A/B, a rejected tool returns only with new evidence).

### Key Entities

- **ToolCandidate**

## Success Criteria *(mandatory)*

- **SC-001**: Rejected candidates are not re-proposed without new evidence.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

### Session 2026-11-02 (implementation)

- Q: Where is S6 and what does it see? → A: `system_relevance`, the sixth session of the public Tool plan (after `synthesize`; `note_session: synthesize` keeps the note independent). Context `system` (`contexts/system.md`, public: `## Components` and `## In use`), inputs `research` and `compare` (validated JSON), no tools, schema `tool_system_relevance` (`relevance` none | candidate, `component`, `benefit`, `effort`; `in use` is decided by code from the context's `## In use` list and the adopted rows, because the 9B model confused the list with alternatives in the inputs). No enterprise or personal context, no web search, no raw text.
- Q: Who decides what is listed? → A: Code (`pkpipe.domain.tool_candidate.propose`): the component must be one of the system context's (exact name, case-insensitive), effort S/M/L, benefit not empty; a tool under `## In use` or adopted is `in use`; a rejected tool is never proposed again (SC-001) and a listed one is not added twice; names are cleaned (no pipes, line breaks, control characters). The vault tool `update_tool_candidates` adds rows only (existing rows are returned `unchanged`), so a model, a rerun or a second evaluation cannot change a rejected row.
- Q: Where is the list and who changes statuses? → A: `2-Areas/System/System-Tool-Candidates.md` (table, owner edits survive); the pipeline has `read_tool_candidates` / `update_tool_candidates`; status changes are in-process owner commands (`pkpipe candidates set`), not a tool a session could call; hand edits that break a rule are reported by `read_tool_candidates` (`violations`).
- Q: ADR directory? → A: the repository already keeps ADRs in `docs/decisions/` (0001): that is the ADR directory (the spec text said `docs/adr/`). `docs/decisions/template.md` holds the ADR template and the spike checklist; `pkpipe candidates adr` drafts the record from the row and the A/B report.
- Q: What is the A/B? → A: `pkpipe.evals.ab.compare` runs the chosen suites (default sessions, triage, flows, security) on the current and the candidate config with the same model and judge (the candidate is a second pack or overlay with the tool behind its adapter), adds the pairwise judge comparison of every session whose prompt-relevant config differs (013) and `decide` returns `better` only when the candidate's gate is green, no gated metric is worse beyond a tolerance (0.01, direction from the gate: `min` higher, `max` lower, so a new canary leak is a loss) and a metric improved or the judge's pairwise score is at least 0.6; `equal` and `inconclusive` never adopt, `worse` is a reason to reject. Both sides share one cassette per suite, so a recorded A/B replays offline.
- Q: Does the BPMN change? → A: First answer (superseded below): no. The System Tool Candidates page already shows S6, the candidate list, the statuses, the spike with an A/B, the ADR and the rejection with a reason; Run Category Sessions already lists S6. The flow adds the list write after the note is stored (nodes `candidates` and `candidate_write`), which the System Tool Candidates page names ("Add to System-Tool-Candidates").
- Q: Results? → A: Session dataset `tool/system_relevance` (15 synthetic cases: five candidates, eight not relevant of which one is built on the same stack and three carry an injection or a line addressed to the assistant, two tools listed under *In use*), live on qwen3.5-9b-mlx with judge gemma-4-12b in three runs: schema 1.0, checks 15/15, judge 5.0 each, see [Evaluation](../../docs/evaluation.md#measured-with-the-reference-model); the instruction decides in order (the tool itself under *In use* -> `none`, an alternative doing a component's job -> `candidate`, else `none`) after the earlier wordings confused the two. Flows 23 cases (the Tool case now lists a candidate, two new cases: rejected tool not proposed again, tool in use).
- Q: Does the BPMN change after all? → A: Yes (2026-11-03 review): Process Other Resource now shows the inclusive follow-up gateway after the note is stored (new-category suggestion, system tool candidate, none) because the flow writes the candidate list itself; System Tool Candidates names the code checks of its gateway. Drawio, PNGs and Mermaid were updated together.

## Out of Scope

- Automatic adoption.
