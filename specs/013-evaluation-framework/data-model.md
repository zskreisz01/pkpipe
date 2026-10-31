---
spec_id: 013-evaluation-framework
title: Evaluation framework and gates
status: Draft
description: Entities and storage for evaluation framework and gates.
updated: 2026-10-31
---

# Data Model: 013-evaluation-framework

**Spec:** [spec.md](spec.md)

Notation: **NEW** = introduced by this spec · **EXISTING** = already in the codebase.

## Entities

### NEW `EvalCase`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | str | yes |  |
| `suite` | str | yes |  |
| `input` | object | yes |  |
| `expected` | object | no | Label or must_include / must_not |
| `source` | enum | yes | synthetic · override · release · golden |

## Diagram

```mermaid
erDiagram
    EvalCase {
        str id
        str suite
        object input
        object expected
        enum source
    }
```

## Storage

- Pydantic models in `src/pkpipe`; JSON Schema exported by `pkpipe validate`.
- Persisted rows: SQLite (app DB) or vault files as stated in the spec.
