---
spec_id: 022-owner-comment-and-new-categories
status: Active
description: Plan for the owner comment column and new-category proposals
updated: 2026-11-06
---

# Implementation Plan: Owner comment column and proposals for new categories

**Branch**: `spec/022-owner-comment-and-new-categories` | **Spec**: [spec.md](spec.md) | **Depends on**: 007, 010, 020, 021

## Summary

`comment` column in `TriageRow` / `TableFile` (migration, owner-only), `domain.triage.resolve_category` and the state `awaiting_category`, `triage.release` returning `proposals`, the flow `propose_category`, proposal note rendering in `domain.session_plan`, backlog and summary messages.

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| II. Hexagonal core | Yes | Pure functions in `pkpipe.domain`; the vault tools stay the only writers |
| III. Configuration over code | Yes | The pipeline never edits categories; the owner adds them through draft, validate, eval, publish |
| IV. Untrusted content | Yes | The proposal session is the existing tool-less `misc.evaluate` session behind the output guard; text is neutralised before it is written |
| V. Code decides | Yes | Normalisation, slug rule, state, paths and the note are code; the model only evaluates |
| VI. Human in the loop | Yes | A new category exists only when the owner publishes it; the comment is the owner's |
| VII. Public / private separation | Yes | Owner free text (`comment`) never leaves the vault: not in tool results, notifications, traces, logs |
| IX. Measured change | Yes | No new LLM step: `misc.evaluate` is covered by its dataset; code paths are tested |
| X. Observable by default | Yes | Counts only on spans and in notifications |
| XI. Design as diagram and code | Yes | The proposal path is one more branch of Process Inbox Items (drawio, PNG, Mermaid together) |
| Others | n/a | |

## Structure

- `pkpipe/obsidian/models.py`, `tables.py`, `lists.py` (column, migration, comment rules), `vault.py` (read_triage without comment)
- `pkpipe/domain/triage.py` (`awaiting_category`, `resolve_category`), `domain/session_plan.py` (proposal note)
- `pkpipe/steps/triage_code.py` (`triage.release`, `triage.backlog`, `triage.proposal_rows`), `steps/session_plan.py` (`category.proposal_*`)
- `packs/learning-inbox/flows/propose_category.json`, `process_released.json`, `triage.json`
- docs: configuration, flows, operations, security; BPMN Process Inbox Items

## Risks

- Widening rewrites every row once: cells are re-serialised (edge blanks of a cell are layout, not content); rows that do not parse stay byte for byte
- The 3 live rows with owner text in `topic`: moved by the rule on the first write after the upgrade; owner text in the `topic` of an image row is not moved
