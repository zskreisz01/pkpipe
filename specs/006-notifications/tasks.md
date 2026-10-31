---
spec_id: 006-notifications
status: Completed
description: Tasks for Notification port and channels
updated: 2026-11-01
---

# Tasks: Notification port and channels

**Input**: `specs/006-notifications/`

Format: `- [x] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [x] T001 [US1] NotifyPort + router
- [x] T002 [US1] Discord webhook adapter
- [x] T003 [US1] Teams workflow (Adaptive Card) adapter
- [x] T004 [US1] Apprise adapter
- [x] T005 [US1] Templates for triage_ready, daily_summary, security_flag, weekly_review

## Verification

- [x] T006 CI green (ruff, pytest)
- [x] T007 Eval gates passed where applicable
- [x] T008 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
