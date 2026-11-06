---
spec_id: 023-web-triage-surface
status: In Progress
description: Plan for the web triage surface
updated: 2026-11-06
---

# Implementation Plan: Web triage surface

**Branch**: `spec/023-web-triage-surface` | **Spec**: [spec.md](spec.md) | **Depends on**: 014, 019, 020, 021, 022

## Summary

A surface-aware vault library (`obsidian/surface.py`: table block, summary page, migration), config (`layout.triage_data_file`, `triage.json`, `ui.json`, policy `triage_surface`), `owner_note` plumbing (release, session inputs, capture redaction, plan rules), UI/API additions on `AppServices`, host allowlist extras, a launchd template and the user guide.

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| II. Hexagonal core | Yes | Pure text functions in the obsidian adapter package; services call the vault library only |
| III. Configuration over code | Yes | Surface, data file, public URL and hosts are config; the pipeline still never edits versioned config |
| IV. Untrusted content | Yes | The owner note is capped, neutralised, datamarked, only a declared input, never in a system prompt; tool sessions keep the query guard |
| V. Code decides | Yes | Override validity, state, migration and the summary are code |
| VI. Human in the loop | Yes | The UI is the owner's decision surface; the pipeline still cannot confirm or override |
| VII. Public / private separation | Yes | Candidates go to the private overlay; the guide has no private data; private data classes never get the note |
| VIII. Local first / data classes | Yes | The note never reaches a vision, journal or context flow |
| IX. Measured change | Yes | No new LLM step; sessions get an optional extra input (existing eval datasets unchanged) |
| X. Observable by default | Yes | The capture view omits the note; counts only on spans |
| XI. Design as diagram and code | Yes | Process Inbox Items: decide in the web UI or the file, summary page (drawio, PNG, Mermaid) |
| Others | n/a | |

## Structure

- `pkpipe/domain/layout.py`, `engine/config_models.py` (`TriageConfig`, `UiConfig`), `engine/registry.py` (defaults, cross checks), `obsidian/policy.py` (`triage_surface`)
- `pkpipe/obsidian/tables.py` (block helpers), `obsidian/surface.py`, `obsidian/lists.py`, `obsidian/vault.py`
- `pkpipe/steps/triage_code.py`, `steps/session_plan.py`, `steps/llm.py`, `domain/session_plan.py`, `domain/data_classes.py`
- `pkpipe/api/services.py`, `api/app.py`, `api/templates/*`, `api/security.py`, `serve.py`, `mcp/server.py`
- `deploy/launchd/`, `docs/user-guide.md`, docs, BPMN Process Inbox Items

## Risks

- A migration interrupted between the two writes leaves both files with a table: handled by the "surface file wins, add the missing rows" rule
- Owner prose on the retired page: kept below the table in the data file
- `owner_note` to a web search session widens what can leave the machine: only through model-built queries that pass the query guard
