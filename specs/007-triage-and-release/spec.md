---
spec_id: 007-triage-and-release
title: Daily triage and morning release
status: Completed
description: The 20:00 triage flow suggests a category per new INBOX item in an isolated session and notifies the owner; the 06:00 flow releases only confirmed or overridden items and fans them out to the category processes.
relevant_when:
  - triage
  - release
  - Inbox-Triage
  - categorize
  - process_released
  - pending
related: [004-obsidian-vault-mcp, 005-flow-engine, 006-notifications]
updated: 2026-11-01
---

# Feature Specification: Daily triage and morning release

**Feature Branch**: `spec/007-triage-and-release`
**Created**: 2026-10-31
**Status**: Completed
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Evening suggestions (Priority: P1)

Every new INBOX item gets an AI category suggestion and the owner a notification.

**Independent Test**: Flow test + triage eval (`evals/datasets/triage*.jsonl`).

**Acceptance Scenarios**:

1. **Given** new INBOX items, **When** triage runs, **Then** each gets a suggestion with confidence and reason in the triage file
2. **Given** the categorization session, **When** it runs, **Then** it has no tools and sees only URL, title and note

### User Story 2 - Morning release (Priority: P1)

Only confirmed or overridden items are processed; the rest stays pending.

**Independent Test**: Unit tests on `domain.triage.release` + flow test.

**Acceptance Scenarios**:

1. **Given** unconfirmed items, **When** 06:00 runs, **Then** they stay pending and appear in the next notification
2. **Given** an override, **When** released, **Then** the override category is used

### User Story 3 - Daily summary (Priority: P2)

After processing, the owner gets counts of processed, pending and quarantined items.

**Independent Test**: Flow test.

**Acceptance Scenarios**:

1. **Given** a finished release run, **When** it ends, **Then** a summary is sent

### Edge Cases

- Same resource captured twice → one item (id = hash).
- Category removed from config while pending → item flagged for re-triage.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Categorization MUST use the `triage_result` schema (category, confidence, reason).
- **FR-002**: The categorization session MUST NOT fetch item content (constitution IV).
- **FR-003**: Release MUST follow `domain.triage.release`; quarantined and processed items are skipped.
- **FR-004**: Each released item MUST run in its own instance; a security flag affects only that item.

### Key Entities

- **TriageItem** (see 004 data model)

## Success Criteria *(mandatory)*

- **SC-001**: Triage accuracy ≥ 85% on the triage dataset (initial gate).
- **SC-002**: 0 unconfirmed items processed in flow tests.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

### Session 2026-11-01

- Q: What happens when a category is removed while an item is open? → A: The next triage run selects the item again and suggests a category from the remaining ones; an override naming an unknown category is never released.

## Out of Scope

- Smaller-model tier for categorization (decided: one model for now).
