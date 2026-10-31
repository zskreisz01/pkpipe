---
spec_id: 012-learning-gateway
title: learning-gateway MCP and Process Learning Material
status: Draft
description: Entities and storage for learning-gateway mcp and process learning material.
updated: 2026-10-31
---

# Data Model: 012-learning-gateway

**Spec:** [spec.md](spec.md)

Notation: **NEW** = introduced by this spec · **EXISTING** = already in the codebase.

## Entities

### NEW `NotebookJob`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `item_id` | str | yes |  |
| `backend` | enum | yes | open_notebook · notebooklm_browser · fake |
| `nb_id` | str | no |  |
| `job_id` | str | no |  |
| `status` | enum | yes | generating · ready · failed |
| `audio_path` | str | no |  |
| `link` | str | no |  |
| `reason` | str | no |  |

## Diagram

```mermaid
erDiagram
    NotebookJob {
        str item_id
        enum backend
        str nb_id
        str job_id
        enum status
        str audio_path
        str link
        str reason
    }
```

## Storage

- Pydantic models in `src/pkpipe`; JSON Schema exported by `pkpipe validate`.
- Persisted rows: SQLite (app DB) or vault files as stated in the spec.
