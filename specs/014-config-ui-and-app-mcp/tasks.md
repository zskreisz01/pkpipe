---
spec_id: 014-config-ui-and-app-mcp
status: Draft
description: Tasks for Config UI and app MCP server
updated: 2026-10-31
---

# Tasks: Config UI and app MCP server

**Input**: `specs/014-config-ui-and-app-mcp/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [ ] T001 [US1] Config CRUD + validation
- [ ] T002 [US1] Publish pipeline (validate → smoke eval → git commit)
- [ ] T003 [US1] Runs and triage views

## Phase: User Story 2 (P2)

- [ ] T004 [US2] App MCP server + contract

## Verification

- [ ] T005 CI green (ruff, pytest)
- [ ] T006 Eval gates passed where applicable
- [ ] T007 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
