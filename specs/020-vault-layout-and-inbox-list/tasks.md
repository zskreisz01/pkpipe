---
spec_id: 020-vault-layout-and-inbox-list
status: Completed
description: Tasks for the configurable vault layout and the list-style INBOX
updated: 2026-11-04
---

# Tasks: Configurable vault layout and list-style INBOX

Format: `- [x] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [x] T001 [US1] `domain/inbox_list.py`: parser, kinds, normalisation, tracking parameters
- [x] T002 [US1] Scrubbed real-INBOX fixture and golden test (SC-001)
- [x] T003 [US1] `inbox.parse_list` step; `inbox.select_new` with the list source; `kind`, `needs_owner` in the triage rows
- [x] T004 [US1] Image items: owner decides the category

## Phase: User Story 2 (P1)

- [x] T005 [US2] `VaultLayout` and `VaultPolicy.layout`; validation against the allowlists (FR-008)
- [x] T006 [US2] Replace the literals (`4-Archive/Inbox`, `2-Areas/Agent-Tasks`, tool candidates, improvement, quarantine, journal) with layout values
- [x] T007 [US2] `journal_pattern` with year folders
- [x] T008 [US2] Two-layout flow test and the literal-grep test (SC-002)

## Phase: User Story 3 (P2)

- [x] T009 [US3] `remove_entries` in the domain, property test (SC-003)
- [x] T010 [US3] `inbox_remove_entries` vault tool and the archive file
- [x] T011 [US3] Triage flow: remove after processing

## Phase: Polish

- [x] T012 [P] Docs (configuration, vault layout), CHANGELOG
- [x] T013 [P] BPMN: drawio, PNG, Mermaid together
- [x] T014 Spec 019 scope from the layout: provides `VaultLayout.snapshot_roots()` only; no consumer exists until spec 019 is added (FR-009 partly met)
