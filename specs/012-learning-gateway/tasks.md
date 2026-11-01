---
spec_id: 012-learning-gateway
status: Active
description: Tasks for learning-gateway MCP and Process Learning Material
updated: 2026-11-01
---

# Tasks: learning-gateway MCP and Process Learning Material

**Input**: `specs/012-learning-gateway/`

Format: `- [x] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [x] T001 Contract `contracts/learning-gateway.md`
- [x] T002 Fake backend

## Phase: User Story 1 (P1)

- [x] T003 [US1] Open Notebook backend
- [x] T004 [US1] Flow learning_material.json with wait + timeout
- [x] T005 [US1] Docker compose for Open Notebook + TTS

## Phase: User Story 2 (P2)

- [x] T006 [US2] NotebookLM browser backend (interface + `NotebookLMDriver`, contract-tested with a scripted driver; no driver shipped)

## Verification

- [x] T007 CI green (ruff, pytest)
- [x] T008 Eval gates passed where applicable (no new LLM step; flow suite 15/15 with six new learning cases)
- [x] T009 Docs updated; BPMN image and Mermaid unchanged: the process did not change (constitution XI)
