---
spec_id: 020-vault-layout-and-inbox-list
status: Partially completed (FR-009 pending spec 019)
description: Plan for the configurable vault layout and the list-style INBOX
updated: 2026-11-04
---

# Implementation Plan: Configurable vault layout and list-style INBOX

**Branch**: `spec/020-vault-layout-and-inbox-list` | **Spec**: [spec.md](spec.md)

## Summary

A pure `domain/inbox_list.py` (parser, normaliser, remover) and `domain/layout.py` (`VaultLayout`); `VaultPolicy.layout`; the obsidian-vault MCP gets `inbox_remove_entries` and exposes the layout; steps and flows read paths from the layout.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`); standard library (`re`, `urllib.parse`)
**Testing**: golden test on a scrubbed real INBOX, property test for the remover, grep test for literals, two-layout flow test

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| I. Local-first | Yes | |
| II. Hexagonal core | Yes | Parser is domain code; file access only through the vault port |
| III. Configuration over code | Yes | This spec turns constants into configuration |
| V. Code decides | Yes | Parsing, identity and removal are code |
| VI. Human in the loop | Yes | Images and unknown kinds go to the owner |
| VII. Public / private separation | Yes | Fixture is synthetic, shaped like the real list; the real INBOX stays private |
| IX. Measured change | Yes | SC-001..003 |
| XI. Design as diagram and as code | Yes (done) | BPMN capture and triage pages: "Parse INBOX list" and "Remove processed entry" tasks (drawio, PNG, Mermaid) |
| Others | n/a | |

## Structure

- `pkpipe/domain/inbox_list.py`, `pkpipe/domain/layout.py`
- `pkpipe/obsidian/policy.py` (`layout`), `mcp_servers/obsidian_vault` (`inbox_remove_entries`)
- `pkpipe/steps/triage_code.py` (`inbox.parse_list`, `select_new` sources), `session_plan.py`, `domain/session_plan.py`, `domain/tool_candidate.py`, `domain/improvement.py` (paths from the layout)
- pack flows: path values become layout references
- docs: configuration, vault layout, BPMN pages

## Risks

- Removal races with the owner editing in Obsidian: matching by raw text and a re-read before write; the snapshot of spec 019 is the safety net
- Entry heuristics (continuation lines) misread an odd entry: unknown shapes become kind `text` for the owner, never dropped
