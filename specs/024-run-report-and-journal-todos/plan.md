---
spec_id: 024-run-report-and-journal-todos
status: Completed
description: Plan for the run report and the journal todos
updated: 2026-11-07
---

# Implementation Plan: Run report after every run, and todos in the daily journal

**Branch**: `spec/024-run-report-and-journal-todos` | **Spec**: [spec.md](spec.md) | **Depends on**: 006, 007, 012, 016, 019, 020, 022, 023

## Summary

Pure rules in `domain/run_report.py` (counts, todo lines, rendering, wikilinks) and `domain/journal_todos.py` (the block write rule); two code steps in `steps/report_code.py`; the subflow `run_report` (`read_triage`, `unreviewed_notes`, `report.run_summary`, `write_journal_todos`, notify) called by `triage` and `process_released`; two vault tools; the `report` config; the `daily_summary` templates; one version source.

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| II. Hexagonal core | Yes | Steps use the tool and notify ports; the block rule is a pure domain function the vault adapter calls |
| III. Configuration over code | Yes | `report.json`; the vault policy grants the journal; no config is written by the pipeline |
| IV. Untrusted content | Yes | The report holds counts, ids and plain note links; note names that are not plain are dropped; no model sees the journal or the report input |
| V. Code decides | Yes | Todos, counts, links and the journal block are code |
| VI. Human in the loop | Yes | The report only informs; the todos do not decide anything |
| VII. Public / private separation | Yes | No personal data; the owner's journal folder and heading are overlay and policy |
| VIII. Local first / data classes | Yes | The journal is written only by a mechanical, grant-gated tool and never read out |
| IX. Measured change | Yes | No LLM step; flow eval cases adapted to the report text |
| X. Observable by default | Yes | The tool span carries the tool name only; no text |
| XI. Design as diagram and code | Yes | Process Inbox Items: Send run report (drawio, PNG, Mermaid) |
| Others | n/a | |

## Structure

- `pkpipe/domain/journal_todos.py`, `domain/run_report.py`, `domain/layout.py` (default allow, journal grant check), `domain/vault_snapshot.py` (tool classification)
- `pkpipe/obsidian/vault.py`, `obsidian/server.py`, `obsidian/policy.py`, `obsidian/scope.py` (journal outside the scope)
- `pkpipe/steps/report_code.py`, `steps/triage_code.py` (`attention` of `triage.backlog`), `engine/config_models.py`, `engine/registry.py`
- `packs/learning-inbox/flows/run_report.json`, `triage.json`, `process_released.json`, `report.json`, `mcp_servers.json`, `templates/daily_summary.*`
- `evals/datasets/flows/*` (expected notification text), `pkpipe/__init__.py`, `pyproject.toml`
- docs (flows, configuration, operations, security, user guide, glossary), BPMN Process Inbox Items

## Risks

- A journal folder copied into snapshots: excluded in `derive_scope` for this tool, with a test
- A note name (page title) in a link: reduced to plain characters or dropped
- Owners who relied on the quiet evening: `report.enabled: false` keeps it
- A report that fails: swallowed by the calling flow, tested
