---
spec_id: 010-category-sessions
title: Category session plans and Process Other Resource
status: Draft
description: Executes per-category session plans from config (Tool with six sessions, Marketing, Misc and overlay categories), stores notes in PARA folders and maintains the processing list.
relevant_when:
  - session plan
  - categories.json
  - instructions
  - tool evaluation
  - PARA
  - processing list
  - overlay category
related: [008-secure-fetch, 009-output-guard-and-quarantine, 011-context-packs]
updated: 2026-10-31
---

# Feature Specification: Category session plans and Process Other Resource

**Feature Branch**: `spec/010-category-sessions`
**Created**: 2026-10-31
**Status**: Draft
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [data-model.md](data-model.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Tool evaluation note (Priority: P1)

A tool item becomes an evaluation note with alternatives, personal and enterprise fit and a recommendation.

**Independent Test**: Flow test + session evals.

**Acceptance Scenarios**:

1. **Given** a tool item, **When** processed, **Then** sessions run in plan order with declared inputs only
2. **Given** the synthesize output, **When** stored, **Then** the note lands in the category's PARA path and the processing list

### User Story 2 - New categories without code (Priority: P1)

An overlay adds a category with its own session plan.

**Independent Test**: Config + flow test with an overlay fixture.

**Acceptance Scenarios**:

1. **Given** an overlay category, **When** an item has it, **Then** its session plan runs

### User Story 3 - Misc evaluation (Priority: P3)

Misc items get a new-category suggestion when appropriate.

**Independent Test**: Session eval.

**Acceptance Scenarios**:

1. **Given** a misc item that fits a new theme, **When** evaluated, **Then** a suggestion is added to agent tasks

### Edge Cases

- Session schema error after retries → item failed with reason in the list.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Session plan entries: `id`, `system`, `instruction`, `tools`, `inputs`, `context`, `output_schema`.
- **FR-002**: Sessions with an enterprise context MUST NOT have web search tools; queries pass the query guard.
- **FR-003**: Instructions ≤ ~800 tokens; system prompts fixed per role.

### Key Entities

- **Category**
- **SessionSpec**
- **SessionResult**

## Success Criteria *(mandatory)*

- **SC-001**: Rubric ≥ 3.5/5 on session datasets.
- **SC-002**: Adding an overlay category needs no code change (test).

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

## Out of Scope

- Job and business-opportunity instructions (private overlay).
