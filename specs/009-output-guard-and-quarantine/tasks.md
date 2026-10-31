---
spec_id: 009-output-guard-and-quarantine
status: Draft
description: Tasks for Output guard and quarantine
updated: 2026-10-31
---

# Tasks: Output guard and quarantine

**Input**: `specs/009-output-guard-and-quarantine/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [ ] T001 [US1] Output guard step type
- [ ] T002 [US1] Qwen3Guard wrapper

## Phase: User Story 2 (P1)

- [ ] T003 [US2] Flow handle_security_flag
- [ ] T004 [US2] Quarantine writer (inert)
- [ ] T005 [US2] malicious_urls.jsonl + deny-list

## Phase: User Story 3 (P2)

- [ ] T006 [US3] Release action (UI + app MCP)
- [ ] T007 [US3] Eval candidate export

## Verification

- [ ] T008 CI green (ruff, pytest)
- [ ] T009 Eval gates passed where applicable
- [ ] T010 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
