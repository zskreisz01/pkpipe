---
spec_id: 004-obsidian-vault-mcp
title: obsidian-vault MCP contract
status: Active
description: Tool contract, file formats and policy of the obsidian-vault MCP server.
updated: 2026-10-31
---

# Contract: obsidian-vault MCP server

Server `obsidian-vault` (FastMCP). Entry: `python mcp_servers/obsidian_vault/server.py` or
`python -m pkpipe.obsidian.server`. Streamable HTTP on `127.0.0.1:8701` (`/mcp`); `--transport stdio` for local
clients and tests. Non-loopback `--host` values abort (FR-004). Host and Origin headers are validated strictly
(`421` for a non-loopback `Host`, `403` for a foreign browser `Origin`), so a web page cannot reach the vault
through DNS rebinding.

## Policy (`--config policy.json`)

```json
{
  "vault_dir": "/path/to/vault",
  "allow": { "write_note": ["0-INBOX", "3-Resources"] },
  "triage_file": "0-INBOX/Inbox-Triage.md",
  "processing_list": "0-INBOX/Manual-Resource-Processing-List.md"
}
```

`allow` maps a tool name to folder prefixes (vault relative, whole path segments). Unlisted tools use the defaults
(PARA folders; list files in `0-INBOX`). A path is rejected when it is absolute, has `..`, `.`, empty or hidden
segments, a backslash or a colon, is not `.md`, is outside the tool's prefixes, or resolves (through symlinks)
outside the vault or the prefixes. The triage file and processing list cannot be touched by the generic note tools;
the check is case- and Unicode-normalisation-insensitive (macOS APFS) and also catches symlinks to them. Sync
conflict files are never written, appended to or moved.

## Tools

| Tool | Arguments | Result |
| --- | --- | --- |
| `list_folder` | `path` | `{notes, folders, conflicts}`; hidden entries skipped, sync conflict files in `conflicts` |
| `read_note` | `path` | `{front_matter, front_matter_error, body, conflict}` |
| `write_note` | `path, body, front_matter?, overwrite=false` | atomic create; `front_matter` omitted keeps an existing block byte for byte |
| `append_note` | `path, text` | appends to an existing note, front matter untouched |
| `move_note` | `src, dst` | moves inside allowed folders, never overwrites |
| `read_triage` | – | `{rows[TriageRow], invalid, conflicts}` |
| `update_triage` | `rows[]` | upsert by `item_id` (or hash of `resource`); omitted fields keep their value; all-or-nothing; see rules below |
| `upsert_processing_list` | `rows[ProcessingListRow]` | upsert by `item_id`; other rows untouched |

Errors are MCP tool errors (`isError`) with a message that is safe to show to the model.

### `update_triage` rules (constitution V, VI)

- `confirm` and `override` are the owner's decisions, made in the file. A row may repeat their current value but
  never change it; new rows cannot set them.
- New rows start `suggested`, and their `item_id` is `sha256(resource.strip())[:12]` (one item per resource).
- The `resource` of an existing row cannot change.
- State changes follow `pkpipe.domain.triage.TRANSITIONS`: `released` only when the row is confirmed or
  overridden, `processed` only from `released`, and `processed` and `quarantined` are never left by the pipeline
  (leaving quarantine is an owner decision).

## File formats

Triage file: one markdown table, other content is preserved verbatim.

```
| confirm | item | resource | suggested | override | state |
| --- | --- | --- | --- | --- | --- |
| [x] | 8f4d3cd2910d | https://example.invalid/a | tool |  | released |
```

Processing list columns: `item | category | note | nb_id | link | status` (`note` holds the Obsidian link).
Literal `|` in a cell is written `\|`. Only changed rows are re-serialised; line endings are kept.
Writes are temp file + `fsync` + rename under a directory `flock`, retried on a fresh read when the file changed.
`move_note` hard-links the destination before unlinking the source, so a note that appears at the destination
during the move (Obsidian does not take our lock) is never overwritten.
