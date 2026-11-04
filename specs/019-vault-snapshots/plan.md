---
spec_id: 019-vault-snapshots
status: Draft
description: Plan for vault snapshots and restore
updated: 2026-11-04
---

# Implementation Plan: Vault snapshots and restore

**Branch**: `spec/019-vault-snapshots` | **Spec**: [spec.md](spec.md)

## Summary

A `SnapshotPort` (take, finish, plan_restore, apply_restore, list, prune) with a file-system adapter, called by the run service before and after a run of a vault-writing flow. The scope comes from `VaultPolicy`. CLI, UI and app MCP are thin entry points.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`); standard library only (`hashlib`, `shutil`, `os`)
**Testing**: pytest on a temp vault with the real vault tools; fake `SnapshotPort` for run service tests; property test: random run, restore, identical tree

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| I. Local-first | Yes | Local files only |
| II. Hexagonal core | Yes | New port, adapter outside the core; the run service only knows the port |
| III. Configuration over code | Yes | `snapshots.retention_days`, `max_bytes`; scope from the vault policy |
| V. Code decides | Yes | Plan and conflict rules are code; no model involved |
| VI. Human in the loop | Yes | Dry run by default, owner token to apply, `--force` explicit |
| VII. Public / private separation | Yes | Data directory only; nothing private in a repository |
| IX. Measured change | Yes | SC-001 as a test for every writing pack flow |
| X. Observable by default | Yes | Spans with counts, no paths |
| XI. Design as diagram and as code | Yes | BPMN: a "Take snapshot" task before every writing flow and a "Restore run" process (drawio, PNG, Mermaid) |
| Others | n/a | |

## Structure

- `pkpipe/ports`: `SnapshotPort`
- `pkpipe/adapters/snapshot_files.py`: copy, manifest, post manifest, plan, apply, prune
- `pkpipe/engine/runs.py`: take before the first node (flows whose tools can write), finish after
- `pkpipe/cli.py`, `pkpipe/api`, `pkpipe/mcp`: `snapshot`, `restore`, `restore_run`
- `docs/operations.md`, `runbook` guidance, BPMN pages

## Risks

- A flow that writes through a tool this spec does not list: the writing-tools list is derived from the vault policy's tool names, and a test fails when a new vault tool is not classified.
- Snapshot size grows with notes in scope: bounded by `max_bytes` and retention; the manifest-only fallback for files over a size limit is a clarification for Gate 1.
