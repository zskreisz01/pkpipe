---
spec_id: 019-vault-snapshots
status: Draft
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
  "taken_at": "2026-11-04T20:00:01+01:00",
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

`snapshots(run_id PRIMARY KEY, flow_id, taken_at, bytes, files, status CHECK IN ('kept','restored','pruned'), restored_at)`.
