---
spec_id: 011-context-packs
status: Completed
description: Tasks for Context packs and weekly refresh
updated: 2026-11-02
---

# Tasks: Context packs and weekly refresh

**Input**: `specs/011-context-packs/`

Format: `- [x] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [x] T001 [US1] Refresh flow with parallel personal/enterprise branches (`context_refresh` fan-out over scopes into `context_pack`, `context.*` code functions, local-only data class `context`, shipped `system.md`)
- [x] T002 [US1] Diff-preserving writer (`ContextStorePort`, hash header, manual section, `.proposed` file, review note)

## Phase: User Story 2 (P1)

- [x] T003 [US2] Query guard + context-leak eval (terms file fed by the refresh, anonymizing scrub + verify, `context_leak` suite and dataset, gate 0 leaks, live run)

## Verification

- [x] T004 CI green (ruff, pytest)
- [x] T005 Eval gates passed where applicable (context_leak 0 leaks live; sessions 85/85 checks, judge 4.906)
- [x] T006 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI): Refresh Project Contexts page, PNG and Mermaid
