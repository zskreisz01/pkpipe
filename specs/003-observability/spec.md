---
spec_id: 003-observability
title: OpenTelemetry tracing and metrics
status: Draft
description: OTLP traces and metrics for flows, steps, LLM, tool and HTTP calls, with pkpipe-specific attributes, to Langfuse or Phoenix. Content capture configurable and off for private data.
relevant_when:
  - OpenTelemetry
  - OTLP
  - Phoenix
  - Langfuse
  - metrics
  - tracing
related: [005-flow-engine]
updated: 2026-10-31
---

# Feature Specification: OpenTelemetry tracing and metrics

**Feature Branch**: `spec/003-observability`
**Created**: 2026-10-31
**Status**: Draft
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [research.md](research.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every run is traceable (Priority: P1)

The owner opens a trace and sees each step, LLM call and tool call of an item.

**Independent Test**: In-memory span exporter in tests.

**Acceptance Scenarios**:

1. **Given** a flow run, **When** it finishes, **Then** spans exist for flow, steps, LLM and tool calls with `flow.id`, `step.id`, `item.id`, `config.version`

### User Story 2 - Operational metrics (Priority: P2)

Backlog size, items per category, flags per detector and learning timeouts are visible as metrics.

**Independent Test**: Metric reader in tests.

**Acceptance Scenarios**:

1. **Given** a triage run, **When** it finishes, **Then** `pkpipe.items_total{category}` and `pkpipe.backlog_size` are updated

### Edge Cases

- Trace backend unreachable → app keeps running; exporter drops with a warning metric.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Telemetry MUST be configured from standard `OTEL_*` environment variables and export OTLP only.
- **FR-002**: OpenInference instrumentation for LangChain/LangGraph and OpenTelemetry instrumentation for FastAPI and httpx MUST be enabled.
- **FR-003**: Custom attributes `flow.id`, `step.id`, `bpmn.element`, `item.id`, `item.category`, `config.version`, `run.trigger` MUST be set on step spans.
- **FR-004**: Prompt and response content capture MUST be configurable per flow and disabled for journal and context data.
- **FR-005**: Security flags MUST be recorded as span events `security.flag`.

### Key Entities

- **Span attributes** and **metrics** as listed in `docs/operations.md`.

## Success Criteria *(mandatory)*

- **SC-001**: Every step of a flow test produces exactly one step span with all required attributes.
- **SC-002**: No journal text appears in exported spans (test with a canary string).

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

## Out of Scope

- Dashboards in the trace backend.
