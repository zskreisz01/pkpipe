---
spec_id: 014-config-ui-and-app-mcp
title: Config UI and app MCP server
status: Completed
description: Drafts, publishes and audit log tables of the config UI; token file; draft file format.
updated: 2026-11-01
---

# Data model: 014-config-ui-and-app-mcp

Tables live in the app DB next to `runs` (`pkpipe.adapters.app_store_sqlite`).

```mermaid
erDiagram
    DRAFT ||--o| PUBLISH : "published as"
    PUBLISH ||--o| PUBLISH : "reverted_by"
    DRAFT { text draft_id PK
            text status "open|published|discarded"
            int rev
            json files }
    PUBLISH { text publish_id PK
              text draft_id FK
              text commit_sha
              text parent_sha
              text kind "publish|rollback"
              json files
              text summary
              json decision
              text reverted_by }
    AUDIT { int seq PK
            text at
            text actor "ui|mcp:owner|mcp:agent"
            text action
            text target
            json detail }
```

## Draft

`files` maps an editable path to `{"content": str | null, "base_hash": str}`. `content: null` deletes the file
from the overlay. `base_hash` is the sha256 of the **overlay** file when the draft first touched it (`absent` for a
new file); publish and validate fail when the file on disk no longer matches. `rev` increments on every save; a
save with an older `rev` fails (two tabs).

Editable paths: `<models|mcp_servers|channels|schedules|categories|security|telemetry|evals>.json`,
`flows/<id>.json`, and `.md` / `.json` / `.txt` below `prompts/`, `instructions/`, `contexts/`, `schemas/`,
`templates/`. Maximum 256 KiB, no NUL bytes.

## Publish

`decision` is the gate report at publish time (`status` passed / overridden / unchanged, `affected`, `failures`,
`override_reason`, `eval_run_id`, `metrics` = measured value, operator and threshold of every gated metric per suite). A rollback row has `kind = rollback` and `decision = {"status": "rollback"}`;
the reverted publish gets `reverted_by`.

## Tokens

`<data dir>/tokens.json` (mode 0600): `{"owner": "...", "agent": "..."}`, generated on first `pkpipe serve`.
`PKPIPE_TOKEN` / `PKPIPE_AGENT_TOKEN` (at least 24 characters, different) override the file.
