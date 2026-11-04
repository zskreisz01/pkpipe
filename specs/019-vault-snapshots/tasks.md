---
spec_id: 019-vault-snapshots
status: Completed
description: Tasks for vault snapshots and restore
updated: 2026-11-04
---

# Tasks: Vault snapshots and restore

Format: `- [x] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [x] T001 [US1] `SnapshotPort` and the scope derivation from `VaultPolicy`
- [x] T002 [US1] File adapter: manifest, copies, post manifest
- [x] T003 [US1] Run service: take before / finish after for vault-writing flows; a failing snapshot stops the run
- [x] T004 [US1] Round-trip test for every writing flow of the pack (SC-001)

## Phase: User Story 2 (P1)

- [x] T005 [US2] Restore plan (restore / remove / move_back / skip) and dry run
- [x] T006 [US2] Apply with conflict detection, trash, `--force`, snapshot of the current state first
- [x] T007 [US2] CLI `pkpipe restore`; UI and app MCP owner action `restore_run` with audit log

## Phase: User Story 3 (P2)

- [x] T008 [US3] `pkpipe snapshot list|show|prune`, retention, `snapshots` table, `reverted` flag
- [x] T009 [US3] `pkpipe doctor` snapshot section

## Phase: Polish

- [x] T010 BPMN (drawio, PNG, Mermaid), `docs/operations.md`, runbook rollback section
