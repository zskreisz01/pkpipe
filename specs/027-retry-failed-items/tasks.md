---
spec_id: 027-retry-failed-items
status: Completed
description: Tasks of the owner's retry of failed items
updated: 2026-11-07
---

# Tasks: The owner retries failed items

## Phase 1: Rules and files (US1)
- [x] T001 [US1] `check_retry` in `domain/triage.py`; `failed_processing_items`, `retry_item` in `obsidian/lists.py`; `Vault.failed_items`, `Vault.owner_retry`; tool lists (policy, snapshot classification, pack, stdio test); unit tests incl. the all-state-pairs test

## Phase 2: Owner channels (US1-US3)
- [x] T002 [US3] `triage.retry_max` config; app DB `item_retries`; `AppServices.retry_item`, `retry_failed`, `failed_summary`; rows carry `failed`, `retries`, `retryable`
- [x] T003 [US1, US2] HTTP routes, UI row button and confirmed bulk action, app MCP `retry_items`, CLI `pkpipe retry`; integration tests (API, UI, MCP, CLI, agent token refused, audit)

## Phase 3: Report and end to end (US4)
- [x] T004 [US4] The `retry` todo in the run report and the `failed_items` step in the `run_report` subflow
- [x] T005 [US1] End-to-end test on a real vault: fail, fix, retry, release run, one line, note filed

## Phase: Polish
- [x] T006 Docs (flows, user guide, configuration, operations, security, architecture, glossary), BPMN drawio + PNG + Mermaid, CHANGELOG (Unreleased), spec index, contracts; consistency walk
