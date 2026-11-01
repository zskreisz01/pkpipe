---
spec_id: 014-config-ui-and-app-mcp
status: Completed
description: Tasks for Config UI and app MCP server
updated: 2026-11-01
---

# Tasks: Config UI and app MCP server

**Input**: `specs/014-config-ui-and-app-mcp/`

Format: `- [x] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [x] T001 [US1] Config CRUD + validation
- [x] T002 [US1] Publish pipeline (validate → smoke eval → git commit)
- [x] T003 [US1] Runs and triage views

## Phase: User Story 2 (P2)

- [x] T004 [US2] App MCP server + contract

## Verification

- [x] T005 CI green (ruff, pytest)
- [x] T006 Eval gates passed where applicable
- [x] T007 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
