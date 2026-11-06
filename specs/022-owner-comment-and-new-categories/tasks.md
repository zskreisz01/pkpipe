---
spec_id: 022-owner-comment-and-new-categories
status: Active
description: Tasks for the owner comment column and new-category proposals
updated: 2026-11-06
---

# Tasks: Owner comment column and proposals for new categories

Format: `- [ ] T001 [USx] Description`

## Phase: User Story 1 (comment)

- [ ] T001 [US1] `comment` in `TriageRow` and `TableFile` (always wide), owner-only update rule, round-trip tests (long text, `\|`, wikilinks, blanks), property test
- [ ] T002 [US1] Column migration on write (widen, `topic` of non-image rows into `comment` once), maintenance call `update_triage([])`, triage flow node
- [ ] T003 [US1] `read_triage` of the vault server and the app MCP leave `comment` out; leak tests (tool result, notification, spans, logs)

## Phase: User Story 2 (new categories)

- [ ] T004 [US2] `resolve_category` (normalise, slug rule) and the state `awaiting_category`; release and backlog use them
- [ ] T005 [US2] Flow `propose_category`, proposal note with the `categories.json` draft, state rows; `process_released` wiring; no config written
- [ ] T006 [US2] Messages: backlog, daily summary

## Phase: Polish

- [ ] T007 [P] Docs (configuration, flows, operations, security), BPMN Process Inbox Items (drawio, PNG, Mermaid), CHANGELOG
