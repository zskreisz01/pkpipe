---
spec_id: 015-self-improvement-cycle
title: Self-improvement cycle (overlay)
status: Completed
description: Daily extraction of improvement items from journal notes with a local session without tools, weekly evidence collection and verdict proposals, owner review, and system changes through the eval gate. Ships as an optional overlay-enabled flow.
relevant_when:
  - self-improvement
  - journal
  - improvement backlog
  - weekly review
related: [013-evaluation-framework]
updated: 2026-11-01
---

# Feature Specification: Self-improvement cycle (overlay)

**Feature Branch**: `spec/015-self-improvement-cycle`
**Created**: 2026-10-31
**Status**: Completed
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

### Session 2026-11-01 (implementation)

- Q: Where does the journal come from and what does the session see? → A: `<journal folder>/<day>.md` (default
  `2-Areas/Journal`, node config, overlay-changeable); code extracts the `## Improvement` section and only that text
  reaches the session. No section, no model call.
- Q: How is "no invented items" (SC-001) enforced? → A: twice. Code keeps a statement only if it is verbatim in the
  section (the statement is the owner's wording, not a rewrite), and the `improvement` eval suite measures precision
  after that code (gate 0.90) plus the raw `invented_rate`.
- Q: Who changes a status? → A: only the owner, in the backlog table. `update_improvements` creates `proposed` rows and
  lets the weekly review write evidence, verdict, reason, next experiment and proposal link; any other pipeline write
  is refused (FR-001).
- Q: What is "evidence"? → A: quotes from the journals after the day the item was written (at most 28 days; that day holds only the statement) that are verbatim in
  a journal day, found by a session without tools; without verified evidence the verdict is `keep` and no verdict
  session runs.
- Q: What does "enters the eval gate" mean for a kept system item? → A: a proposal note
  (`System-Changes/<id>.md`, `awaiting-eval-gate`) is written; applying it is a draft config change that
  `PublishGate` (spec 013) must pass. The pipeline never edits config.
- Q: How is "local only" (FR-002) enforced? → A: `pkpipe validate` rejects journal-class flows with tool sessions,
  non-loopback model profiles (including fallbacks), calls to MCP servers marked `egress`, or non-journal subflows;
  traces never capture their content.
- Q: Enabled by default? → A: No. The flows ship in the public pack; an overlay `schedules.json` entry enables them.
- Status: Completed. Live results (qwen3.5-9b-mlx, judge gemma-4-12b) in docs/evaluation.md.

## Out of Scope

- Habit tracking dashboards.
