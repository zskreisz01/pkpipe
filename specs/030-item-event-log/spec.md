---
spec_id: 030-item-event-log
title: One history per item (append-only event log) and process start buttons on the overview
status: Completed
status_reason: Implemented 2026-11-08 on branch spec/030-item-event-log (from dev at v0.1.8); suite, ruff green; awaiting the owner's approval to publish
description: The history of one item was spread over run checkpoints, the owner audit log, retry counters, the review table, the malicious-URL log, the vault notes and the notebook jobs, so a quarantined or failed item could not be looked up by its id. Every step that touches an item now appends one event to an append-only log in the app DB (plus events for the secure-fetch verdict with the classifier scores, source searches, quarantine and hold); the owner's audited actions are merged in when the history is read. The owner reads one timeline per item in the UI, over HTTP, in the app MCP and on the CLI. The overview also gets start buttons for the top-level processes.
relevant_when:
  - item history
  - event log
  - event sourcing
  - audit
  - traceability
  - item timeline
  - start a process
  - overview buttons
related: [014-config-ui-and-app-mcp, 023-web-triage-surface, 025-find-and-evaluate-urlless-items, 027-retry-failed-items, 029-classifier-evidence-and-review]
updated: 2026-11-08
---

# Feature Specification: Item event log and process start buttons

**Feature Branch**: `spec/030-item-event-log` (from `dev` at v0.1.8)
**Status**: Completed (2026-11-08)
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **Cause.** After the first production runs the owner could not find a quarantined item by its id and could not tell why a failed item failed or what had been done to it: runs, audit rows, retries, held warnings, the malicious-URL log, vault notes and notebook jobs each knew a piece.
2. **Decision (owner, 2026-11-08): an append-only event log next to the state, not full event sourcing.** The log *explains* what happened; it is **not the source of truth** of any state (the vault, the triage table, the run store and the app DB tables stay authoritative). Nothing is rebuilt from events and no flow reads them. This keeps the flows and stores unchanged; the log can later feed projections if event sourcing is ever wanted.
3. **What is logged.** The flow compiler appends one `step` event per node execution that has an item (`step` = node id, `outcome` done/failed, `detail`: step type, BPMN element, duration in ms, error type and short message of a failed step). Steps add their own events through `StepContext.event`: `verdict` (secure-fetch status, flag reason codes, the classifier evidence of spec 029, the page address), `source` (source search result code or the found URLs), `quarantined` (address, domain, reasons, whether the domain was deny-listed or protected) and `held` (spec 029 hold with its evidence). Owner actions (confirm, override, retry, accept warning, release quarantine, bulk retry, and their refusals) are already in the audit log (spec 014); `AppServices.item_history` merges the audit rows whose target is the item, so nothing is written twice.
4. **No text, ever.** Events hold ids, numbers, codes and addresses of public pages. They never hold page, note, journal, context or image text. A flow of a private data class (`journal`, `context`, `image`) logs content-free events: kind, step, outcome, time, no `detail`. Detail values are cut to 300 characters. Recording never stops a run: any error of the log is only logged.
5. **Reading it.** `AppServices.item_history(id)` returns the triage row, retries used, the held warning, the quarantine summary and the timeline (events and owner actions in time order, each with a fixed sentence). Channels: UI `/items` (search by id, recently active items) and `/items/{id}` (phone friendly; classifier scores expandable), a `history` link on every triage row, HTTP `GET /api/items/{id}/history`, the app MCP **owner** tool `item_history` (read only; it is not offered to the agent token because addresses come from untrusted pages) and CLI `pkpipe item [ID] [--json]` (no id: recently active items).
6. **Process start buttons.** The overview page gets a *Start a process* card with one button per top-level process in `AppServices.LAUNCH_FLOWS` (`triage`, `process_released`, `context_refresh`, `improvement_extract`, `improvement_review`), each with its description and a confirmation. A button is disabled while a run of that flow is active (the launcher already refuses a second active run). Subflows that need an item are never offered. The buttons use the existing run start (`POST /ui/runs`, audited).

## Functional Requirements

- **FR-001**: `ItemEventPort` (`record`, `events_of`, `recent_item_ids`); AppStore table `item_events`; `pkpipe.domain.item_events` (`emit`, `safe_detail`, `describe`, `timeline`).
- **FR-002**: the compiler logs a `step` event per item step; `StepContext.event`; port `item_events` in `ports_for`.
- **FR-003**: events `verdict`, `source`, `quarantined`, `held`.
- **FR-004**: private data classes log content-free events; a failing log never fails a run.
- **FR-005**: `AppServices.item_history`, `recent_items`, `launchable_flows`; UI `/items`, `/items/{id}`, overview card; HTTP `GET /api/items/{id}/history`; app MCP owner tool `item_history`; CLI `pkpipe item`.

## Success Criteria

- **SC-001**: a flow run leaves one event per item step, failures with their error type; a private flow leaves none of the detail (`tests/unit/engine/test_compiler.py`, `test_item_events.py`).
- **SC-002**: the history of an item lists its events and the owner's actions in time order; an unknown id is 404 (`test_item_history.py`).
- **SC-003**: the overview shows the start buttons for the top-level processes and none for subflows; the triage row links to the history (`test_item_history.py`).
