---
spec_id: 008-secure-fetch
status: Draft
description: Tasks for Secure fetch
updated: 2026-10-31
---

# Tasks: Secure fetch

**Input**: `specs/008-secure-fetch/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [ ] T001 Datamarking + canary helpers (exist), verdict function

## Phase: User Story 1 (P1)

- [ ] T002 [US1] web-fetch MCP registration + fallback adapter
- [ ] T003 [US1] Reader instruction + schema
- [ ] T004 [US1] Flow `secure_fetch.json`

## Phase: User Story 2 (P1)

- [ ] T005 [US2] Sanitizer with tests on fixtures
- [ ] T006 [US2] Prompt Guard 2 wrapper (chunking)
- [ ] T007 [US2] Security eval runner

## Phase: Polish & cross-cutting

- [ ] T008 Docs security page update

## Verification

- [ ] T009 CI green (ruff, pytest)
- [ ] T010 Eval gates passed where applicable
- [ ] T011 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
