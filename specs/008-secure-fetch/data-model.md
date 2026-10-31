---
spec_id: 008-secure-fetch
title: Secure fetch
status: Draft
description: Entities and storage for secure fetch.
updated: 2026-10-31
---

# Data Model: 008-secure-fetch

**Spec:** [spec.md](spec.md)

Notation: **NEW** = introduced by this spec · **EXISTING** = already in the codebase.

## Entities

### NEW `ReaderExtract`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `title` | str | yes |  |
| `summary` | str | yes | ≤ 200 words |
| `facts` | list[str] | yes | Short factual bullets |
| `links` | list[str] | no | Relevant outbound links |
| `injection_suspected` | bool | yes |  |
| `evidence` | str|null | no | Quoted suspicious text |

### NEW `Verdict`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `clean` | bool | yes |  |
| `flags` | list[enum] | yes | heuristic · classifier · canary · reader |
| `scores` | object | no | Per-detector scores |

## Diagram

```mermaid
erDiagram
    ReaderExtract {
        str title
        str summary
        list_str facts
        list_str links
        bool injection_suspected
        str evidence
    }
    Verdict {
        bool clean
        list_enum flags
        object scores
    }
```

## Storage

- Pydantic models in `src/pkpipe`; JSON Schema exported by `pkpipe validate`.
- Persisted rows: SQLite (app DB) or vault files as stated in the spec.
