---
spec_id: 007-triage-and-release
status: Active
description: Tasks for Daily triage and morning release
updated: 2026-11-01
---

# Tasks: Daily triage and morning release

**Input**: `specs/007-triage-and-release/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [x] T001 [US1] Flow `triage.json`
- [x] T002 [US1] Categorization instruction + schema
- [x] T003 [US1] Notification payload with backlog size

## Phase: User Story 2 (P1)

- [x] T004 [US2] Flow `process_released.json` with fan-out to subflows
- [x] T005 [US2] Re-triage on unknown category
- [x] T011 [US2] Review fix: no re-suggestion of confirmed items (FR-005); `invalid` decisions reported in `triage_ready` even without new items; flow eval case `unknown-override-goes-back-to-owner`
- [ ] T012 [US1] De-duplicate two INBOX notes that capture the same URL (deferred, see spec `status_reason`)

## Phase: User Story 3 (P2)

- [x] T006 [US3] Daily summary step

## Phase: Polish & cross-cutting

- [x] T007 Update process pages Process Inbox Items (image + Mermaid) if behaviour changed

## Verification

- [x] T008 CI green (ruff, pytest)
- [x] T009 Eval gates passed where applicable
- [x] T010 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
