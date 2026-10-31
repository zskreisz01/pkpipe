---
spec_id: 016-system-tool-candidates
title: System tool candidates
status: Draft
description: Entities and storage for system tool candidates.
updated: 2026-10-31
---

# Data Model: 016-system-tool-candidates

**Spec:** [spec.md](spec.md)

Notation: **NEW** = introduced by this spec · **EXISTING** = already in the codebase.

## Entities

### NEW `ToolCandidate`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `tool` | str | yes |  |
| `evaluation_note` | str | yes | Link |
| `component` | str | yes | C4 component name |
| `benefit` | str | yes |  |
| `effort` | enum | yes | S · M · L |
| `status` | enum | yes | candidate · investigating · trial · adopted · rejected |
| `reason` | str | no | Required when rejected |

## Diagram

```mermaid
erDiagram
    ToolCandidate {
        str tool
        str evaluation_note
        str component
        str benefit
        enum effort
        enum status
        str reason
    }
```

## Storage

- Pydantic models in `src/pkpipe`; JSON Schema exported by `pkpipe validate`.
- Persisted rows: SQLite (app DB) or vault files as stated in the spec.
