---
spec_id: 016-system-tool-candidates
title: System tool candidates
status: Draft
description: A sixth Tool session checks whether an evaluated tool could improve pkpipe itself; candidates go to a separate list and are adopted only after a spike that wins an A/B on the eval suite, recorded in an ADR.
relevant_when:
  - system relevance
  - tool candidates
  - ADR
  - spike
related: [010-category-sessions, 011-context-packs, 013-evaluation-framework]
updated: 2026-10-31
---

# Feature Specification: System tool candidates

**Feature Branch**: `spec/016-system-tool-candidates`
**Created**: 2026-10-31
**Status**: Draft
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

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Candidate statuses: candidate → investigating → trial → adopted | rejected.
- **FR-002**: Adoption requires an ADR in `docs/adr/` and a green eval gate.

### Key Entities

- **ToolCandidate**

## Success Criteria *(mandatory)*

- **SC-001**: Rejected candidates are not re-proposed without new evidence.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

## Out of Scope

- Automatic adoption.
