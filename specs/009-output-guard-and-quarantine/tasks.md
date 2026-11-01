---
spec_id: 009-output-guard-and-quarantine
status: Completed
description: Tasks for Output guard and quarantine
updated: 2026-11-01
---

# Tasks: Output guard and quarantine

**Input**: `specs/009-output-guard-and-quarantine/`

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [x] T001 [US1] Output guard step type (`output_guard`, canary option on sessions, guarded-session validation)
- [x] T002 [US1] Qwen3Guard wrapper (+ ProtectAI DeBERTa and combined backend; the Meta adapter removed, decision 0001)

## Phase: User Story 2 (P1)

- [x] T003 [US2] Flow handle_security_flag (wired into categorize_item, other_resource, learning_material)
- [x] T004 [US2] Quarantine writer (inert): vault tool `quarantine_note`, protected folder, exact restore
- [x] T005 [US2] malicious_urls.jsonl + deny-list (SecurityLogPort, file adapter, counter)

## Phase: User Story 3 (P2)

- [x] T006 [US3] Release action (UI + app MCP): re-queue, note restored, owner only
- [x] T007 [US3] Eval candidate export (`<overlay>/evals/candidates/security.jsonl`)

## Verification

- [x] T008 CI green (ruff, pytest)
- [x] T009 Eval gates passed where applicable (output-guard set, classifier cassette, security suite live)
- [x] T010 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
