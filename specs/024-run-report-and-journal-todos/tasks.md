---
spec_id: 024-run-report-and-journal-todos
status: Completed
description: Tasks for the run report and the journal todos
updated: 2026-11-07
---

# Tasks: Run report after every run, and todos in the daily journal

Format: `- [x] T001 [USx] Description`

## Phase: Setup

- [x] T001 Spec, plan, data model, tasks

## Phase: User Story 3 (journal block)

- [x] T002 [US3] `domain/journal_todos.py` (block rule, line and heading checks) with property-style tests
- [x] T003 [US3] Vault tools `write_journal_todos`, `unreviewed_notes`; policy tool list, default allow, grant check in `layout_errors`; snapshot classification; journal outside the snapshot scope; security tests (outside the markers, no read-out, no grant, dates, damaged markers, span without text)

## Phase: User Story 1 and 2 (report and todos)

- [x] T004 [US1] `domain/run_report.py`, `steps/report_code.py`, `triage.backlog` `attention`; unit tests (counts, rendering, truncation, wikilinks)
- [x] T005 [US1] `report.json` config model, core default, pack default; subflow `run_report`; `triage` and `process_released` call it; templates; flow eval cases adapted; legacy path under `report.enabled: false`
- [x] T006 [US2] Integration tests on a real vault: report per run, todos, `reviewed: true`, journal on/off/not granted, a failing report never fails the run, no item text in any message or line

## Phase: Polish

- [x] T007 [US4] `__version__` from metadata and the test against `pyproject.toml`; version 0.1.5
- [x] T008 Docs (flows, configuration, operations, security, user guide, glossary), BPMN drawio + PNG + Mermaid, CHANGELOG, spec index; consistency walk
