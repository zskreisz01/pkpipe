---
spec_id: 010-category-sessions
status: Completed
description: Tasks for Category session plans and Process Other Resource
updated: 2026-11-01
---

# Tasks: Category session plans and Process Other Resource

**Input**: `specs/010-category-sessions/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [x] T001 [US1] Dynamic session-plan step (`session_plan` step type, `category.plan` / `category.note` code, plan validation, query guard)
- [x] T002 [US1] Tool instructions + schemas (research, compare, fit personal / enterprise, synthesize)
- [x] T003 [US1] Store + processing list + archive steps (flow `other_resource`; PARA note, `move_note`, failed items with the reason in the list)

## Phase: User Story 2 (P1)

- [x] T004 [US2] Overlay fixture test (`tests/integration/engine/test_category_sessions.py`, `test_overlay_on_disk.py`)

## Phase: User Story 3 (P3)

- [x] T005 [US3] Misc evaluate session (new-category suggestion becomes an agent task note)

## Phase: Polish & cross-cutting

- [x] T006 Session eval datasets (synthetic): `datasets/sessions/<category>/<session>.jsonl` for every session of the public plans, rubrics, live run and cassettes

## Verification

- [x] T007 CI green (ruff, pytest)
- [x] T008 Eval gates passed where applicable (sessions suite live on qwen3.5-9b-mlx with the gemma-4-12b judge; flows 21/21, sessions 73/73 checks, judge 4.95)
- [x] T009 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI): the BPMN page Process Other Resource, its PNG and Mermaid now show the conditional secure fetch, Misc as a plan that passes the output guard, the suggestion after the guard and the failed-item path; config / flow docs updated
