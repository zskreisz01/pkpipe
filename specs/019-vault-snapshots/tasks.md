---
spec_id: 019-vault-snapshots
status: Draft
description: Tasks for vault snapshots and restore
updated: 2026-11-04
---

# Tasks: Vault snapshots and restore

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [ ] T001 [US1] `SnapshotPort` and the scope derivation from `VaultPolicy`
- [ ] T002 [US1] File adapter: manifest, copies, post manifest
- [ ] T003 [US1] Run service: take before / finish after for vault-writing flows; a failing snapshot stops the run
- [ ] T004 [US1] Round-trip test for every writing flow of the pack (SC-001)

## Phase: User Story 2 (P1)

- [ ] T005 [US2] Restore plan (restore / remove / move_back / skip) and dry run
- [ ] T006 [US2] Apply with conflict detection, trash, `--force`, snapshot of the current state first
- [ ] T007 [US2] CLI `pkpipe restore`; UI and app MCP owner action `restore_run` with audit log

## Phase: User Story 3 (P2)

- [ ] T008 [US3] `pkpipe snapshot list|show|prune`, retention, `snapshots` table, `reverted` flag
- [ ] T009 [US3] `pkpipe doctor` snapshot section

## Phase: Polish

- [ ] T010 BPMN (drawio, PNG, Mermaid), `docs/operations.md`, runbook rollback section
