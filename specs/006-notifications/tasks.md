---
spec_id: 006-notifications
status: Draft
description: Tasks for Notification port and channels
updated: 2026-10-31
---

# Tasks: Notification port and channels

**Input**: `specs/006-notifications/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [ ] T001 [US1] NotifyPort + router
- [ ] T002 [US1] Discord webhook adapter
- [ ] T003 [US1] Teams workflow (Adaptive Card) adapter
- [ ] T004 [US1] Apprise adapter
- [ ] T005 [US1] Templates for triage_ready, daily_summary, security_flag, weekly_review

## Verification

- [ ] T006 CI green (ruff, pytest)
- [ ] T007 Eval gates passed where applicable
- [ ] T008 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
