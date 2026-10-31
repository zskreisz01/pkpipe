---
spec_id: 014-config-ui-and-app-mcp
title: Config UI and app MCP server
status: Draft
description: FastAPI + HTMX UI for editing config (draft, validate, smoke eval, publish to git), viewing runs and triage, and an MCP server so chat agents can run flows and confirm items.
relevant_when:
  - config UI
  - FastAPI
  - HTMX
  - publish
  - app MCP
  - Hermes
related: [013-evaluation-framework, 005-flow-engine]
updated: 2026-10-31
---

# Feature Specification: Config UI and app MCP server

**Feature Branch**: `spec/014-config-ui-and-app-mcp`
**Created**: 2026-10-31
**Status**: Draft
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [research.md](research.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Edit config safely (Priority: P1)

The owner edits a session instruction, runs the smoke eval and publishes.

**Independent Test**: API tests + UI smoke test.

**Acceptance Scenarios**:

1. **Given** a draft, **When** publish is requested, **Then** validation and smoke eval run before a git commit
2. **Given** a publish, **When** done, **Then** the diff and rollback are available

### User Story 2 - Control from chat (Priority: P2)

A chat agent lists triage items and confirms them over MCP.

**Independent Test**: MCP contract tests.

**Acceptance Scenarios**:

1. **Given** `confirm_item(id)`, **When** called, **Then** the triage file is updated

### Edge Cases

- Concurrent edits → optimistic lock on file hash.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Access only on localhost / Tailscale with a token.
- **FR-002**: App MCP tools: `run_flow`, `list_triage`, `confirm_item`, `override_item`, `get_run`, `release_quarantine`.

### Key Entities

- **Draft**
- **Publish**

## Success Criteria *(mandatory)*

- **SC-001**: A new category can be created end-to-end from the UI.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

## Out of Scope

- Multi-user access.
