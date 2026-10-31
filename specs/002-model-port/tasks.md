---
spec_id: 002-model-port
status: Draft
description: Tasks for ModelPort and OpenAI-compatible model adapter
updated: 2026-10-31
---

# Tasks: ModelPort and OpenAI-compatible model adapter

**Input**: `specs/002-model-port/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [ ] T001 Finalize ModelPort signature and errors (`SchemaError`, `ModelUnavailable`)
- [ ] T002 Pydantic model for `models.json`

## Phase: User Story 1 (P1)

- [ ] T003 [US1] OpenAI adapter with json_schema response_format and local validation
- [ ] T004 [US1] Retry on schema error (configurable)
- [ ] T005 [US1] Fake model port for tests (scripted responses)

## Phase: User Story 2 (P1)

- [ ] T006 [US2] Profile resolution with `env:` values and fallback
- [ ] T007 [US2] Live smoke test against LM Studio (`-m live`)

## Phase: Polish & cross-cutting

- [ ] T008 Document profiles in `docs/configuration.md`

## Verification

- [ ] T009 CI green (ruff, pytest)
- [ ] T010 Eval gates passed where applicable
- [ ] T011 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
