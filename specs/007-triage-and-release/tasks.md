---
spec_id: 007-triage-and-release
status: Draft
description: Tasks for Daily triage and morning release
updated: 2026-10-31
---

# Tasks: Daily triage and morning release

**Input**: `specs/007-triage-and-release/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [ ] T001 [US1] Flow `triage.json`
- [ ] T002 [US1] Categorization instruction + schema
- [ ] T003 [US1] Notification payload with backlog size

## Phase: User Story 2 (P1)

- [ ] T004 [US2] Flow `process_released.json` with fan-out to subflows
- [ ] T005 [US2] Re-triage on unknown category

## Phase: User Story 3 (P2)

- [ ] T006 [US3] Daily summary step

## Phase: Polish & cross-cutting

- [ ] T007 Update process pages Process Inbox Items (image + Mermaid) if behaviour changed

## Verification

- [ ] T008 CI green (ruff, pytest)
- [ ] T009 Eval gates passed where applicable
- [ ] T010 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
