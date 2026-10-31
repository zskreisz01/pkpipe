---
spec_id: 015-self-improvement-cycle
title: Self-improvement cycle (overlay)
status: Draft
description: Daily extraction of improvement items from journal notes with a local session without tools, weekly evidence collection and verdict proposals, owner review, and system changes through the eval gate. Ships as an optional overlay-enabled flow.
relevant_when:
  - self-improvement
  - journal
  - improvement backlog
  - weekly review
related: [013-evaluation-framework]
updated: 2026-10-31
---

# Feature Specification: Self-improvement cycle (overlay)

**Feature Branch**: `spec/015-self-improvement-cycle`
**Created**: 2026-10-31
**Status**: Draft
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [data-model.md](data-model.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Capture improvements from the journal (Priority: P1)

Improvement statements in the journal become backlog items.

**Independent Test**: Flow test + `improvement_extract` eval.

**Acceptance Scenarios**:

1. **Given** a journal with an `## Improvement` section, **When** extraction runs, **Then** proposed items are added
2. **Given** journal content, **When** processed, **Then** no tool, web call or trace content capture occurs

### User Story 2 - Weekly verdicts (Priority: P2)

The owner reviews evidence and proposed verdicts every Sunday.

**Independent Test**: Flow test.

**Acceptance Scenarios**:

1. **Given** active items, **When** the review runs, **Then** evidence and a verdict proposal are listed and the owner is notified
2. **Given** a system-area item kept, **When** confirmed, **Then** a config change proposal enters the eval gate

### Edge Cases

- Review skipped → items unchanged.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Only `proposed` is set by the agent; other transitions need owner confirmation.
- **FR-002**: Journal processing is local only (constitution VII).

### Key Entities

- **ImprovementItem**

## Success Criteria *(mandatory)*

- **SC-001**: No invented items on the extraction dataset (precision ≥ 0.9).

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

## Out of Scope

- Habit tracking dashboards.
