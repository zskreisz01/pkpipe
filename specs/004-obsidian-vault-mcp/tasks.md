---
spec_id: 004-obsidian-vault-mcp
status: Completed
description: Tasks for obsidian-vault MCP server
updated: 2026-10-31
---

# Tasks: obsidian-vault MCP server

**Input**: `specs/004-obsidian-vault-mcp/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [x] T001 Path allowlist and safe path resolver
- [x] T002 Atomic writer with front matter

## Phase: User Story 1 (P1)

- [x] T003 [US1] Tools list/read/write/append/move with tests on a temp vault

## Phase: User Story 2 (P1)

- [x] T004 [US2] Triage file parser/writer (checkbox + override column)
- [x] T005 [US2] Processing list upsert
- [x] T006 [US2] Conflict-file detection

## Phase: Polish & cross-cutting

- [x] T007 launchd plist example
- [x] T008 Contract doc `contracts/obsidian-vault.md`

## Verification

- [x] T009 CI green (ruff, pytest)
- [x] T010 Eval gates passed where applicable
- [x] T011 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
