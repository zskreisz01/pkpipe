---
spec_id: 002-model-port
title: ModelPort and OpenAI-compatible model adapter
status: Draft
description: A ModelPort with structured (JSON-schema) completions and an adapter for any OpenAI-compatible server, configured by model profiles. Default target: LM Studio with a ~9B instruct model.
relevant_when:
  - ModelPort
  - LM Studio
  - structured output
  - models.json
  - model profile
related: [001-foundation-and-docs, 005-flow-engine]
updated: 2026-10-31
---

# Feature Specification: ModelPort and OpenAI-compatible model adapter

**Feature Branch**: `spec/002-model-port`
**Created**: 2026-10-31
**Status**: Draft
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [research.md](research.md) · [data-model.md](data-model.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Steps get schema-valid JSON (Priority: P1)

An LLM step asks for output matching a JSON schema and receives a validated object or a typed error.

**Independent Test**: Adapter tests against a fake server; one live smoke test marked `@pytest.mark.live`.

**Acceptance Scenarios**:

1. **Given** a schema, **When** `complete(system, user, schema)` runs, **Then** the result validates against it
2. **Given** invalid JSON from the model, **When** retry is configured, **Then** the call is retried and then raises `SchemaError`

### User Story 2 - Model is chosen by profile (Priority: P1)

Each step names a profile (`default`, `judge`) instead of an endpoint.

**Independent Test**: Config validation test.

**Acceptance Scenarios**:

1. **Given** `models.json` with `env:` values, **When** loading, **Then** endpoints resolve from the environment
2. **Given** a profile with `fallback`, **When** the primary endpoint is down, **Then** the fallback is used and a span event is recorded

### Edge Cases

- Model returns text around the JSON → extract the first JSON object, then validate.
- Context overflow → typed error, no silent truncation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: ModelPort MUST define `complete(system, user, json_schema=None, profile='default') -> dict`.
- **FR-002**: The adapter MUST use the OpenAI-compatible `response_format: json_schema` when a schema is given and validate the result locally.
- **FR-003**: Profiles MUST configure base URL, model, temperature, max tokens, context size, timeout and an optional fallback profile.
- **FR-004**: Every call MUST produce an OpenTelemetry span with model, tokens and latency (via OpenInference).

### Key Entities

- **ModelProfile**: endpoint and generation settings, referenced by name.
- **Completion**: validated JSON object plus usage metadata.

## Success Criteria *(mandatory)*

- **SC-001**: 100% of step outputs in the flow tests validate against their schema.
- **SC-002**: Switching from LM Studio to Ollama is a `models.json` change only.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

## Out of Scope

- Streaming responses.
- Vendor-specific SDKs.
