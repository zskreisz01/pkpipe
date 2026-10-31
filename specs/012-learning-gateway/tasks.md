---
spec_id: 012-learning-gateway
status: Draft
description: Tasks for learning-gateway MCP and Process Learning Material
updated: 2026-10-31
---

# Tasks: learning-gateway MCP and Process Learning Material

**Input**: `specs/012-learning-gateway/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [ ] T001 Contract `contracts/learning-gateway.md`
- [ ] T002 Fake backend

## Phase: User Story 1 (P1)

- [ ] T003 [US1] Open Notebook backend
- [ ] T004 [US1] Flow learning_material.json with wait + timeout
- [ ] T005 [US1] Docker compose for Open Notebook + TTS

## Phase: User Story 2 (P2)

- [ ] T006 [US2] NotebookLM browser backend

## Verification

- [ ] T007 CI green (ruff, pytest)
- [ ] T008 Eval gates passed where applicable
- [ ] T009 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
