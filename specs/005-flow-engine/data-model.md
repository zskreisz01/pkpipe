---
spec_id: 005-flow-engine
title: Flow engine: compiler, step executors, run service, scheduler
status: Draft
description: Entities and storage for flow engine: compiler, step executors, run service, scheduler.
updated: 2026-10-31
---

# Data Model: 005-flow-engine

**Spec:** [spec.md](spec.md)

Notation: **NEW** = introduced by this spec · **EXISTING** = already in the codebase.

## Entities

### NEW `Run`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `run_id` | str | yes | UUID |
| `flow_id` | str | yes | Flow name |
| `config_version` | str | yes | Effective config hash |
| `trigger` | enum | yes | schedule · manual · mcp · ui |
| `status` | enum | yes | running · waiting · done · failed |
| `started_at` | datetime | yes |  |
| `ended_at` | datetime | no |  |

### NEW `FlowNode`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | str | yes | Unique in flow |
| `type` | enum | yes | Step type |
| `bpmn_element` | str | no | BPMN element name for traces |
| `config` | object | yes | Step-type specific |

## Diagram

```mermaid
erDiagram
    Run {
        str run_id
        str flow_id
        str config_version
        enum trigger
        enum status
        datetime started_at
        datetime ended_at
    }
    FlowNode {
        str id
        enum type
        str bpmn_element
        object config
    }
```

## Storage

- Pydantic models in `src/pkpipe`; JSON Schema exported by `pkpipe validate`.
- Persisted rows: SQLite (app DB) or vault files as stated in the spec.
