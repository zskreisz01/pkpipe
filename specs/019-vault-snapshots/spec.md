---
spec_id: 019-vault-snapshots
title: Vault snapshots and restore
status: Draft
status_reason: Gate 1 open; clarifications 1 and 2 need the owner's decision
description: Before every run that can write to the vault the pipeline saves a snapshot of the files it manages and of the folders it writes to; the owner can undo a run (dry run first) and the restore refuses to overwrite what the owner changed since.
relevant_when:
  - snapshot
  - restore
  - undo a run
  - rollback of data
  - production incident
related: [004-obsidian-vault-mcp, 005-flow-engine, 007-triage-and-release, 014-config-ui-and-app-mcp]
updated: 2026-11-04
---

# Feature Specification: Vault snapshots and restore

**Feature Branch**: `spec/019-vault-snapshots`
**Created**: 2026-11-04
**Status**: Draft
**Input**: Owner request: if something goes wrong in production, the data must be recoverable. A snapshot before each run and a function that restores the previous state replace a shadow mode and an event store (both dropped for now). `docs/operations.md` release ladder, `runbook/release.md` rollback.
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **Snapshot per run, restore per run.** A daily snapshot would also undo what the owner did in between (ticks, overrides, edits). So the unit is the run: "restore the state before run X". Restoring to before an older run is a chain: later runs are undone first. *Open: confirm.*
2. **Scope source.** The scope is derived from the vault policy (spec 004: managed files, quarantine folder and the folders of `write_note`, `append_note`, `move_note`), so it follows any vault layout. It does not depend on the folder names of the public pack. *Open: confirm that attachments (images) are out of scope; the pipeline never writes them.*
3. **Not covered, by design.** The app DB, the learning gateway's job store, Open Notebook content and messages already sent. A restore marks the run `reverted` in its own table and says what stays.
4. **No git dependency.** The vault is a git repository today, but the snapshot is plain files so it works without one and cannot interfere with the owner's own commits and tooling.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every writing run is undoable (Priority: P1)

**Independent Test**: Integration test on a temp vault with the real vault tools and the run service.

**Acceptance Scenarios**:

1. **Given** a flow that may write to the vault, **When** its run starts, **Then** a snapshot with the run id exists before the first write
2. **Given** a run that changed a managed file, created two notes and moved one, **When** the owner restores it, **Then** the managed file has its old bytes, the created notes are gone (moved to the snapshot's trash folder, not deleted) and the moved note is back
3. **Given** a read-only flow, **When** it runs, **Then** no snapshot is taken

### User Story 2 - Restore never destroys the owner's work (Priority: P1)

**Independent Test**: Same fixture; a file edited after the run.

**Acceptance Scenarios**:

1. **Given** a restore, **When** no `--apply` is given, **Then** it prints the plan (restore, remove, move back, skip with reasons) and changes nothing
2. **Given** a file the owner edited after the run ended (current hash differs from the hash the run left), **When** the restore is applied, **Then** that file is skipped and listed as a conflict; `--force` overwrites it after saving the current bytes in the trash folder
3. **Given** a restore, **When** it is applied, **Then** it takes a snapshot of the current state first, so a restore is undoable too

### User Story 3 - Manage snapshots (Priority: P2)

**Acceptance Scenarios**:

1. **Given** snapshots older than the retention (default 14 days), **When** the prune runs, **Then** they are removed; the newest snapshot of each flow is kept
2. **Given** the owner, **When** they list snapshots, **Then** they see run, flow, time, size, file count and status (`kept`, `restored`, `pruned`)

### Edge Cases

- Two runs overlap: the engine already allows one active run per flow; snapshots of different flows are independent. A restore of a run that is not the newest touching a file reports a conflict for that file.
- A run fails half way: its snapshot is still valid; the restore undoes the partial writes.
- Snapshot cannot be written (disk full, permissions): the run does not start and the error names the cause. Writing without a way back is not allowed.
- The vault changes during the snapshot (Obsidian autosave): per file the hash of the copied bytes is recorded, so the manifest never claims a state it did not copy.
- Symlinks and hidden folders: never followed, never copied (same rules as the vault policy).
- Sync-conflict copies (`(conflict)` names, spec 004): listed, never restored over or removed.
- Notes in the `journal` data class are outside every write scope; flows with that class never take part (they write no vault notes besides their own managed files).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Before the first node of a run whose flow can write to the vault (declared by the flow's tools: `write_note`, `append_note`, `move_note`, `update_triage`, `upsert_processing_list`, `update_improvements`, `update_tool_candidates`, `quarantine_note`), the run service takes a snapshot, via a new `SnapshotPort`. A failing snapshot stops the run before it starts.
- **FR-002**: A snapshot holds full copies of the managed files and of every `.md` file below the write scope's folders, plus a manifest (path, size, sha256, mtime) of the same files and a record of the policy it was derived from. Attachments are not copied.
- **FR-003**: The end of a run records, per path it left different from the snapshot, the sha256 it left behind (`post` manifest). This is what a later restore compares with to detect the owner's changes.
- **FR-004**: `pkpipe snapshot list|show <run>|prune` and `pkpipe restore <run> [--apply] [--force]`; the same actions are owner actions of the UI and the app MCP (`restore_run`, owner token only, audit-logged). `restore` is a dry run unless `--apply`.
- **FR-005**: A restore plan has four verbs: `restore` (write the snapshot bytes), `remove` (move a file the run created into `<snapshot>/trash/`), `move_back` (put a moved note back), `skip` (conflict or missing, with the reason). Nothing is deleted; `--force` first saves the current bytes in the trash.
- **FR-006**: Snapshots live in `$PKPIPE_DATA_DIR/snapshots/<run id>/`, mode 0700, never in the vault, never in the config overlay, never in a repository. Retention is `snapshots.retention_days` (default 14) and `snapshots.max_bytes`; pruning keeps the newest snapshot per flow.
- **FR-007**: Restore and snapshot span names carry run id, file counts and byte totals; never paths or note text (constitution X; paths can name private notes).
- **FR-008**: A restored run gets status flag `reverted` in a `snapshots` table of the app DB; its run record is not rewritten.
- **FR-009**: `pkpipe doctor` reports the snapshot directory (free space, newest snapshot, size).

### Key Entities

- **Snapshot**: run id, flow id, taken at, policy digest, manifest, copies.
- **Restore plan**: ordered list of `{verb, path, reason}`.
- See [data-model.md](data-model.md).

## Success Criteria

- **SC-001**: For every writing flow of the pack, the integration test "run, then restore" ends with a vault identical (byte for byte, same file set) to the one before the run.
- **SC-002**: A snapshot of a vault with 1 000 notes in scope takes under 2 seconds on the reference machine and adds no model call.
- **SC-003**: A restore applied twice changes nothing the second time.
- **SC-004**: An owner edit made after a run is never overwritten without `--force`.
