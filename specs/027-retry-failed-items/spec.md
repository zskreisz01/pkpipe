---
spec_id: 027-retry-failed-items
title: The owner retries failed items
status: Completed
status_reason: Implemented 2026-11-07 on branch spec/027-retry-failed-items (from dev at v0.1.5, unreleased; T001-T006); suite, ruff and mypy green; awaiting the owner's approval to publish
description: An item whose processing failed (page not fetched, learning service down or over quota) ends as processed with a failed line in the processing list and could never be retried. The owner now sends it through the next release run again - UI button per row and a confirmed bulk action, app MCP tool retry_items, CLI pkpipe retry - bounded by triage.retry_max (default 3) per item; the state machine stays strict and the run report gets a todo for failed items.
relevant_when:
  - retry
  - failed item
  - processed
  - processing list
  - release
  - owner action
related: [007-triage-and-release, 009-security-quarantine, 014-config-ui-and-app-mcp, 023-web-triage-surface, 024-run-report-and-journal-todos]
updated: 2026-11-07
---

# Feature Specification: The owner retries failed items

**Feature Branch**: `spec/027-retry-failed-items` (from `dev` at v0.1.5)
**Status**: Completed (2026-11-07)
**Companions**: [data-model.md](data-model.md), [contracts/](contracts/)

## Clarifications

1. **The state machine stays strict.** `ItemState.PROCESSED` has no outgoing transition in `TRANSITIONS` (constitution V and VI): pipeline code, and so every vault tool, still cannot leave it. The retry is a separate **owner guard**, `check_retry` in `pkpipe.domain.triage`, that the owner channels call before they write `released`; it is not a transition of the table. A test lists every state pair and proves that exactly one pair (`processed -> released`, with a failed result) is opened by the guard and none by `check_transition`.
2. **What is retryable.** A row is `processed` **and** its line in the processing list (`Manual-Resource-Processing-List.md`, written by the release flows) has the status `failed`. A finished item (`done`, `ready`, `manual`) is final. A **quarantined** item is never retryable, even with a failed line: it first needs the quarantine release (spec 009). An item that was never released is not retryable (nothing failed).
3. **What a retry does.** The row goes `processed -> released`; the owner's `confirm` / `override` stay (the owner decided once, that decision is not asked again); the failed line becomes `unprocessed`, so the next release run writes its new result into **the same line** (the list is keyed by item id: no duplicate). A learning item that already has a `ready` job in the learning gateway reuses it (`active_for_item`, spec 012), so a retry does not create a second notebook. The next release run (06:00 or a manual run) does the rest; the retry does not start a run itself.
4. **Only the owner.** The action is reachable from the owner channels only: the UI (session + CSRF), `POST /api/triage/{id}/retry` and `/api/triage/retry-failed` (owner token; the agent token gets 403 like every other owner endpoint), the app MCP tool `retry_items` (owner token; an agent token is refused and the refusal is audited), and the CLI `pkpipe retry` (the local terminal is the owner's, audit actor `cli`). The vault MCP server, which sessions may use, has no retry tool and `update_triage` still refuses `processed -> released`.
5. **Bounded.** Each item may be retried by the owner at most `triage.retry_max` times (default 3, 1 to 20). The count per item is kept in the app database (`item_retries`), not in the vault: the triage table keeps its columns. At the limit the UI shows "retry limit reached: fix the cause or process it by hand", the API answers 409 naming the limit, and the bulk action skips the item and counts it as `capped`.
6. **Bulk needs a confirm.** "Retry all failed (N)" asks the owner (`hx-confirm` with the counts) and the server requires the confirm too (`confirm=yes` in the form, `{"confirm": true}` in the JSON API); without it the answer is 409 and nothing changes. The bulk action retries every failed, processed, not-capped item and counts what it skipped (a quarantined item with a failed line, a stale id).
7. **No text in the audit or the report.** Audit entries (`retry_item`, `retry_item_refused`, `bulk_retry`) hold the item id, the retry count, counts and the refusal reason (a domain message), never a resource, a note or a failure text. The new vault read tool `failed_items` returns only `{count, items: [{item_id, category}]}`.
8. **Run report todo.** The spec 024 report gets a fourth todo, **Retry failed items - N items - link**, after the others, omitted at zero. N counts items whose processing list line is `failed` and whose triage row is `processed` (ids and states only; a quarantined item is not counted); the link is the triage UI filtered to processed rows (web surface) or the processing list (vault surface). It does not subtract items at the retry limit (the report has no access to the app database); the UI says which ones are capped.
9. **Quota and outages are the typical cause.** A retry after a fixed cause (a web-fetch server added, a NotebookLM quota window that reset) is the intended use; a retry of an item that fails for the same reason fails again and uses one of its retries.

## User Scenarios & Testing

### User Story 1 - Retry one failed item (Priority: P1)
**Acceptance Scenarios**:
1. **Given** a processed item with a failed line, **When** the owner presses Retry (UI), calls `POST /api/triage/{id}/retry`, `retry_items` or `pkpipe retry ITEM`, **Then** the row is `released`, confirm/override are unchanged, the line is `unprocessed`, and the audit has `retry_item`
2. **Given** the next release run, **Then** the item is processed again, the line holds the new result once, and a learning item with a ready job reuses it
3. **Given** a finished item, a quarantined item, an item that was never released or an unknown id, **Then** the retry is refused with a reason and nothing changes

### User Story 2 - Retry all failed items safely (Priority: P1)
1. **Given** failed items, **When** the owner confirms "Retry all failed", **Then** every retryable one goes back to `released`, the answer counts `retried`, `skipped` and `capped`, and one `bulk_retry` audit entry is written
2. **Given** no confirm, **Then** nothing changes (409)

### User Story 3 - A bounded retry (Priority: P1)
1. **Given** an item retried `retry_max` times that failed again, **Then** the UI shows the limit, the API answers 409 naming it, the bulk action counts it as `capped`

### User Story 4 - The report tells (Priority: P2)
1. **Given** failed items, **Then** the next run report has "Retry failed items - N items - link" in the Discord message and the daily journal block

## Requirements

- **FR-001**: `check_retry(state, processing_failed, retries, max_retries)` is the only way out of `processed`; `TRANSITIONS` and `check_transition` are unchanged; a quarantined item is never retryable
- **FR-002**: Vault: `failed_items()` (read only, ids and categories) and `owner_retry(item_id)` (owner channels only, no MCP tool); the retry writes `released` and sets the failed line to `unprocessed`, nothing else
- **FR-003**: `AppServices.retry_item`, `retry_failed`, `failed_summary`; rows of `list_triage` carry `failed`, `retries`, `retryable` for processed rows with a failed line
- **FR-004**: HTTP API `GET /api/triage/failed`, `POST /api/triage/{id}/retry`, `POST /api/triage/retry-failed` (confirm); UI row button and a confirmed bulk action; owner scope and CSRF as the rest of the UI; the agent token is refused
- **FR-005**: App MCP owner tool `retry_items(item_ids?)`; CLI `pkpipe retry [--all | ITEM ...]` (list by default)
- **FR-006**: `triage.retry_max` (default 3, 1 to 20) validated config; the count per item in the app DB table `item_retries`
- **FR-007**: The run report todo `retry` and the read-only vault tool `failed_items` in the pack's tool list and in the snapshot tool classification (read only)
- **FR-008**: Audit entries `retry_item`, `retry_item_refused`, `bulk_retry` without any item text

## Success Criteria

- **SC-001**: a property-style test over all state pairs proves that no pipeline path leaves `processed` or `quarantined` and the guard opens exactly one pair
- **SC-002**: an end-to-end test on a real vault: failed -> fixed -> retried -> `done`, one line in the list, the note in its PARA folder
- **SC-003**: the agent token cannot retry anything over HTTP or MCP, and the refusal is audited
- **SC-004**: the retry never changes `confirm`, `override`, `comment` or any other row
