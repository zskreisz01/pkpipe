---
spec_id: 031-inbox-in-progress
status: Completed
description: Tasks of the triage outcome label and the INBOX-InProgress table
updated: 2026-11-10
---

# Tasks: INBOX-InProgress

- [x] T001 `domain/in_progress.py` (classification, outcome, table render/parse, entry encoding, hash, fallback) + unit tests
- [x] T002 Layout key `inbox_in_progress_file`, managed names, snapshot scope, tool paths, policy tool names, tool sets
- [x] T003 Vault: `read_in_progress`, `read_processing_list`, `inbox_in_progress` (+ MCP tools, pack `mcp_servers.json`, test runtime) + tests
- [x] T004 `triage.in_progress` config model; `steps/in_progress_code.py`; `item_retries` port; `in_progress` item events
- [x] T005 Flows `inbox_in_progress`, `describe_in_progress`; session prompt, instruction, schema; `process_released` wiring
- [x] T006 `inbox.consume` and re-triage tolerate a moved entry; flow tests for retry, release and unchanged facts
- [x] T007 Triage `outcome` / `outcome_label`, filter, pill, API; tests
- [x] T008 Eval dataset (12), rubric, cassettes
- [x] T009 BPMN `Process Inbox Items` (drawio + PNG + Mermaid), `docs/flows.md`
- [x] T010 Docs (configuration, operations, user guide, owner manual, security, glossary, architecture), CHANGELOG 0.1.11, version, `specs/README.md`
