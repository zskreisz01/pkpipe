---
spec_id: 004-obsidian-vault-mcp
status: Draft
description: Tasks for obsidian-vault MCP server
updated: 2026-10-31
---

# Tasks: obsidian-vault MCP server

**Input**: `specs/004-obsidian-vault-mcp/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase 2: Foundational

- [ ] T001 Path allowlist and safe path resolver
- [ ] T002 Atomic writer with front matter

## Phase: User Story 1 (P1)

- [ ] T003 [US1] Tools list/read/write/append/move with tests on a temp vault

## Phase: User Story 2 (P1)

- [ ] T004 [US2] Triage file parser/writer (checkbox + override column)
- [ ] T005 [US2] Processing list upsert
- [ ] T006 [US2] Conflict-file detection

## Phase: Polish & cross-cutting

- [ ] T007 launchd plist example
- [ ] T008 Contract doc `contracts/obsidian-vault.md`

## Verification

- [ ] T009 CI green (ruff, pytest)
- [ ] T010 Eval gates passed where applicable
- [ ] T011 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
