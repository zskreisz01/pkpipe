---
spec_id: 005-flow-engine
status: Draft
description: Tasks for Flow engine: compiler, step executors, run service, scheduler
updated: 2026-10-31
---

# Tasks: Flow engine: compiler, step executors, run service, scheduler

**Input**: `specs/005-flow-engine/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [ ] T001 Flow and step Pydantic schemas
- [ ] T002 Step executor interface + registry (entry points)

## Phase: User Story 1 (P1)

- [ ] T003 [US1] Compiler: nodes, edges, conditions, subflows
- [ ] T004 [US1] Executors: llm, llm_tools, tool, code, notify, guard
- [ ] T005 [US1] Flow tests with fake ports

## Phase: User Story 2 (P1)

- [ ] T006 [US2] SQLite checkpointer + run table
- [ ] T007 [US2] Config version pinning
- [ ] T008 [US2] wait step with deadline and poll interval
- [ ] T009 [US2] Restart test

## Phase: User Story 3 (P2)

- [ ] T010 [US3] Scheduler sync from schedules.json, misfire handling
- [ ] T011 [US3] CLI `run <flow>`

## Phase: Polish & cross-cutting

- [ ] T012 Docs: flow JSON reference

## Verification

- [ ] T013 CI green (ruff, pytest)
- [ ] T014 Eval gates passed where applicable
- [ ] T015 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
