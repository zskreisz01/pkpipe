---
spec_id: 002-model-port
status: Completed
description: Tasks for ModelPort and OpenAI-compatible model adapter
updated: 2026-10-31
---

# Tasks: ModelPort and OpenAI-compatible model adapter

**Input**: `specs/002-model-port/`

Format: `- [x] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [x] T001 Finalize ModelPort signature and errors (`SchemaError`, `ModelUnavailable`)
- [x] T002 Pydantic model for `models.json`

## Phase: User Story 1 (P1)

- [x] T003 [US1] OpenAI adapter with json_schema response_format and local validation
- [x] T004 [US1] Retry on schema error (configurable)
- [x] T005 [US1] Fake model port for tests (scripted responses)

## Phase: User Story 2 (P1)

- [x] T006 [US2] Profile resolution with `env:` values and fallback
- [x] T007 [US2] Live smoke test against LM Studio (`-m live`)

## Phase: Polish & cross-cutting

- [x] T008 Document profiles in `docs/configuration.md`

## Verification

- [x] T009 CI green (ruff, pytest)
- [x] T010 Eval gates passed where applicable
- [x] T011 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
