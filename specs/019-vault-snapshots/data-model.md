---
spec_id: 019-vault-snapshots
status: Completed
description: Snapshot directory layout, manifest and restore plan formats
updated: 2026-11-04
---

# Data model: Vault snapshots

## Directory

```
$PKPIPE_DATA_DIR/snapshots/<run_id>/
├── manifest.json        pre-run manifest
├── post.json            written at the end of the run: hashes the run left behind
├── files/<relative path>   copies of the files in scope (pre-run bytes)
└── trash/<relative path>   files a restore moved out of the vault
```

## manifest.json

```json
{
  "version": 1,
  "run_id": "r-2026-11-04-2000-triage",
  "flow_id": "triage",
  "taken_at": "2026-11-04T20:00:01.123456+01:00",
  "policy_digest": "sha256 of the effective write scope",
  "scope": ["1-Inbox", "4-Resources/Tools"],
  "files": [{"path": "1-Inbox/Inbox-Triage.md", "size": 1234, "sha256": "…", "mtime": 1793818800, "copied": true}]
}
```

`post.json` has the same file entries for the paths that differ from the manifest after the run (`state`: `modified`, `created`, `removed`, `moved_from`).

## Restore plan

```json
{"run_id": "…", "applied": false, "steps": [
  {"verb": "restore", "path": "1-Inbox/Inbox-Triage.md"},
  {"verb": "remove", "path": "4-Resources/Tools/x-ab12cd34ef56.md"},
  {"verb": "move_back", "from": "6-Archive/Inbox/ab12-note.md", "to": "1-Inbox/note.md"},
  {"verb": "skip", "path": "1-Inbox/INBOX.md", "reason": "changed_since_run"}
]}
```

## SQLite (app DB)

`snapshots(run_id PRIMARY KEY, flow_id, taken_at, bytes, files, status CHECK IN ('kept','restored','pruned'), reverted INTEGER, restored_at)`; a pruned snapshot keeps its row.

## Config: `snapshots.json`

`retention_days` (14), `max_bytes` (1 GiB), `max_file_bytes` (5 MiB, larger notes are manifest-only).

The manifest also records `managed` (managed files) and `exclude` (excluded folders), and a file entry `conflict: true` for a sync-conflict copy (listed, never copied or restored). A restore plan step has `outcome` (`done`, `skipped:<reason>`) once applied, `save_current` under `--force`, the plan has `scope` (the manifest's folders) and `safety_snapshot` (the snapshot of the state before the restore, id `restore-<run>-<time>-<n>`).

A skip reason `overlapping_run:<run>` marks a path that another run, overlapping in time, changed too. A file `<snapshot>/.restoring.<token>` exists per running restore (mtime refreshed per step; prune leaves the snapshot alone while one is fresh). Prune also keeps the snapshots of active runs. Every restore step that overwrites a file saves its current bytes in `trash/` first.
