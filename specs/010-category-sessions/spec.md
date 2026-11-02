---
spec_id: 010-category-sessions
title: Category session plans and Process Other Resource
status: Completed
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
updated: 2026-11-01
---

# Feature Specification: Category session plans and Process Other Resource

**Feature Branch**: `spec/010-category-sessions`
**Created**: 2026-10-31
**Status**: Completed
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

### Session 2026-11-01 (implementation)

- Q: How is "dynamic subgraph from the plan" built? → A: As one step type, `session_plan`, not a generated LangGraph subgraph: the plan is data, the step runs its sessions in order (each its own isolated session and span) and the flow `other_resource` around it routes by code. A crash inside the plan repeats the plan (the vault writes come after it, so a rerun overwrites the same note); `run_category_sessions.json` is therefore not a separate flow file, the BPMN page "Run Category Sessions" is the step.
- Q: Which tool sessions, and where is the system relevance session? → A: `research`, `compare`, `fit_personal`, `fit_enterprise`, `synthesize` (the plan of `docs/configuration.md`). The system relevance session needs the system context and a list writer, both spec 016 / 011: it is added after `synthesize`; `note_session` (default `synthesize`) keeps the note independent of sessions that run later.
- Q: How do raw content and tools stay apart? → A: Validation (`pkpipe.domain.session_plan.plan_errors`, also run by the step): a session with tools reads only `item.extract` (the validated secure-fetch extract) and outputs of sessions with an `output_schema`; raw note text (`item.text`) goes only to tool-less sessions (the Misc evaluation). Every session carries the run canary and the plan's output goes through the output guard before any write.
- Q: FR-002 "queries pass the query guard"? → A: `security.query_guard` (`blocked_terms`, `terms_file`): a call to a tool on an `egress` MCP server whose string arguments contain a term is refused before it leaves the machine (case, accent and punctuation insensitive, whole words). A session with the enterprise context has no egress tool, and no such session reads enterprise-derived output (validated). Spec 011 supplies the terms from the context packs.
- Q: What if a context pack is missing (public checkout)? → A: The session is skipped (`{"skipped": "context_missing"}`), left out of `*`, and a field of the note session named like a skipped session is dropped, so the public pack's note has no fit sections instead of invented ones.
- Q: Session failure? → A: A session error after the model's retries (`SchemaError`, `ModelUnavailable`) ends the plan; the item is listed as `failed` with `session '<id>' failed: <ErrorType>` in the `link` column (the list has no reason column, a new column would break existing vault files). A missing URL, a missing plan or an unreachable page fail the item the same way; a security flag quarantines it. Failed items are not retried every morning.
- Q: Where do notes, tasks and the INBOX note go? → A: `<para_path>/<slug of the title>-<item id>.md`, `2-Areas/Agent-Tasks/new-category-<id>-<item id>.md` (an open task note) and `4-Archive/Inbox/<item id>-<name>.md` (`move_note` is on the pack's vault allow list).
- Q: Results? → A: Sessions suite live on qwen3.5-9b-mlx (judge gemma-4-12b), 8 plan sessions with 5 to 6 synthetic cases each (final run 2026-11-02: schema 1.0, checks 73/73, judge mean 4.945 over all 73 session cases), see [Evaluation](../../docs/evaluation.md#measured-with-the-reference-model); flows 21/21 (5 new cases, 2 rewritten); e2e with the real vault, web-fetch (Lightpanda) and a synthetic web search MCP server. A first live run found the 9B model copying and obeying injected instructions inside page extracts (`PWNED` as title, a license changed on request); the instructions now say never to follow or repeat them, which fixed it on the shipped set, and the output guard stops what remains.

## Out of Scope

- Job and business-opportunity instructions (private overlay).
