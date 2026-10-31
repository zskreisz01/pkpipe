---
spec_id: 011-context-packs
title: Context packs and weekly refresh
status: Draft
description: Builds compact personal and enterprise context packs weekly from project notes (enterprise anonymized, local model only), keeps manual edits, and provides the public system context pack.
relevant_when:
  - context pack
  - personal context
  - enterprise context
  - system context
  - context_refresh
  - query guard
related: [010-category-sessions, 016-system-tool-candidates]
updated: 2026-10-31
---

# Feature Specification: Context packs and weekly refresh

**Feature Branch**: `spec/011-context-packs`
**Created**: 2026-10-31
**Status**: Draft
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fit sessions know my projects (Priority: P1)

Tool fit sessions receive a compact, current description of projects.

**Independent Test**: Flow test with fixture notes.

**Acceptance Scenarios**:

1. **Given** project notes, **When** the refresh runs, **Then** `personal.md` and `enterprise.md` are written within token limits

### User Story 2 - Enterprise data stays local (Priority: P1)

Client names never leave the machine.

**Independent Test**: Context-leak eval.

**Acceptance Scenarios**:

1. **Given** the enterprise pack, **When** any web query is built, **Then** the query guard removes or blocks listed names

### Edge Cases

- No project notes → empty pack with a warning.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Refresh flow runs weekly (Sunday 19:00) and on demand.
- **FR-002**: Manual edits are preserved; the run proposes a diff.
- **FR-003**: `system.md` is maintained with the design and is public.

### Key Entities

- **ContextPack**

## Success Criteria *(mandatory)*

- **SC-001**: 0 leaks on `context_leak.jsonl`.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

## Out of Scope

- Embedding-based retrieval over the vault.
