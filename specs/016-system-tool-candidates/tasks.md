---
spec_id: 016-system-tool-candidates
status: Completed
description: Tasks for System tool candidates
updated: 2026-11-03
---

# Tasks: System tool candidates

**Input**: `specs/016-system-tool-candidates/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [x] T001 [US1] S6 session + schema (`tool.system_relevance`, `contexts/system.md`, `tool_system_relevance`, dataset + rubric)
- [x] T002 [US1] Candidate list writer (`pkpipe.obsidian.tool_candidates`, vault tools, flow nodes, `category.note`)

## Phase: User Story 2 (P2)

- [x] T003 [US2] ADR template + spike checklist (`docs/decisions/template.md`, A/B runner `pkpipe.evals.ab`, `pkpipe candidates`)

## Verification

- [x] T004 CI green (ruff, pytest)
- [x] T005 Eval gates passed where applicable
- [x] T006 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
