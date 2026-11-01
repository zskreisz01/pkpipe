---
spec_id: 008-secure-fetch
status: Active
description: Tasks for Secure fetch
updated: 2026-11-01
---

# Tasks: Secure fetch

**Input**: `specs/008-secure-fetch/`

Format: `- [x] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [x] T001 Datamarking + canary helpers (exist), verdict function

## Phase: User Story 1 (P1)

- [x] T002 [US1] web-fetch MCP registration + fallback adapter
- [x] T003 [US1] Reader instruction + schema
- [x] T004 [US1] Flow `secure_fetch.json`

## Phase: User Story 2 (P1)

- [x] T005 [US2] Sanitizer with tests on fixtures
- [x] T006 [US2] Prompt Guard 2 wrapper (chunking); live check with the real model pending its installation (spec status_reason)
- [x] T007 [US2] Security eval runner

## Phase: Polish & cross-cutting

- [x] T008 Docs security page update

## Verification

- [x] T009 CI green (ruff, pytest)
- [x] T010 Eval gates passed where applicable
- [x] T011 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
