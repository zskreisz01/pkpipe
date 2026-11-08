---
spec_id: 030-item-event-log
status: Completed
description: Data of the item event log
updated: 2026-11-08
---

# Data Model: Item event log

**App DB** table `item_events(seq INTEGER PK AUTOINCREMENT, at TEXT, item_id TEXT, run_id TEXT NULL, flow_id TEXT NULL, step TEXT NULL, kind TEXT, outcome TEXT NULL, detail JSON)`; index `(item_id, seq)`. Append only.

**Kinds and detail** (ids, numbers, codes, public addresses; each value at most 300 characters; `{}` in a private data class):

| kind | outcome | detail |
| --- | --- | --- |
| `step` | `done` / `failed` | `type`, `element` (BPMN), `ms`, `error` (error type and short message) |
| `verdict` | `clean` / `flagged` / `review` / ... | `flags[]` (reason codes), `classifier` (spec 029 evidence), `url` |
| `source` | `ok` or the failure code | `reason`, `found`, `results`, `urls[]` |
| `quarantined` | `flagged` | `url`, `domain`, `reasons`, `deny_listed`, `protected` |
| `held` | `held` | `url`, `classifier` (evidence) |

**History** (`AppServices.item_history`): `{item_id, row, retries, review, quarantine, timeline[]}`; a timeline entry is `{at, kind, step, run_id, flow_id, outcome, detail, text}`; owner actions come from `audit` rows with `target = item_id` as `kind: owner` (`detail.action`, `detail.actor`). The log is explanation, not state.

**Overview** `launchable_flows()`: `[{flow, description, running}]` for `AppServices.LAUNCH_FLOWS`.
