---
spec_id: 005-flow-engine
status: Completed
description: Tasks for Flow engine: compiler, step executors, run service, scheduler
updated: 2026-11-01
---

# Tasks: Flow engine: compiler, step executors, run service, scheduler

**Input**: `specs/005-flow-engine/`

Format: `- [x] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [x] T001 Flow and step Pydantic schemas
- [x] T002 Step executor interface + registry (entry points)

## Phase: User Story 1 (P1)

- [x] T003 [US1] Compiler: nodes, edges, conditions, subflows
- [x] T004 [US1] Executors: llm, llm_tools, tool, code, notify, guard
- [x] T005 [US1] Flow tests with fake ports

## Phase: User Story 2 (P1)

- [x] T006 [US2] SQLite checkpointer + run table
- [x] T007 [US2] Config version pinning
- [x] T008 [US2] wait step with deadline and poll interval
- [x] T009 [US2] Restart test

## Phase: User Story 3 (P2)

- [x] T010 [US3] Scheduler sync from schedules.json, misfire handling
- [x] T011 [US3] CLI `run <flow>`

## Phase: Polish & cross-cutting

- [x] T012 Docs: flow JSON reference

## Verification

- [x] T013 CI green (ruff, pytest)
- [x] T014 Eval gates passed where applicable (none yet: LLM step datasets and gates arrive with spec 013)
- [x] T015 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI): flows implement the existing BPMN pages; new `docs/flows.md`, configuration/operations/architecture updated
