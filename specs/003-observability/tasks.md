---
spec_id: 003-observability
status: Completed
description: Tasks for OpenTelemetry tracing and metrics
updated: 2026-10-31
---

# Tasks: OpenTelemetry tracing and metrics

**Input**: `specs/003-observability/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [x] T001 Telemetry bootstrap (resource, OTLP exporters, env config)

## Phase: User Story 1 (P1)

- [x] T002 [US1] Instrument LangGraph via OpenInference
- [x] T003 [US1] Step span decorator with custom attributes
- [x] T004 [US1] Content-capture switch per flow; journal flows forced off
- [x] T005 [US1] Test with in-memory exporter and canary string

## Phase: User Story 2 (P2)

- [x] T006 [US2] Metrics: items_total, backlog_size, flags_total, learning_timeouts_total, step_errors_total

## Phase: Polish & cross-cutting

- [x] T007 Docker compose entries for Phoenix
- [x] T008 Docs: how to point at Langfuse

## Verification

- [x] T009 CI green (ruff, pytest)
- [x] T010 Eval gates passed where applicable
- [x] T011 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
