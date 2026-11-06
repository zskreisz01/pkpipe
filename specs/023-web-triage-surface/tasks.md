---
spec_id: 023-web-triage-surface
status: In Progress
description: Tasks for the web triage surface
updated: 2026-11-06
---

# Tasks: Web triage surface

Format: `- [ ] T001 [USx] Description`

## Phase: Setup

- [ ] T001 Spec, plan, data model, tasks; amend spec 022 where it is superseded

## Phase: User Story 3 (one place)

- [ ] T002 [US3] Config: `layout.triage_data_file`, `triage.json`, `ui.json`, policy `triage_surface`, cross checks (tests first)
- [ ] T003 [US3] `obsidian/surface.py`: table block, summary page, migration both directions; vault wiring (tests first, real header variants)

## Phase: User Story 2 (owner note)

- [ ] T004 [US2] `read_triage` exposes `comment`; `owner_note` in release items and proposals; session input `item.owner_note`; capture redaction; private flows; plan rules (tests first)
- [ ] T005 [US2] Query guard canary test for a web search session; override candidates with the reason

## Phase: User Story 1 (web UI)

- [ ] T006 [US1] Services and API: comment edit, new-category override, filters, bulk confirm, image endpoint (tests first)
- [ ] T007 [US1] `/triage` page: all fields, thumbnails, highlights, filters, bulk, proposal link

## Phase: User Story 4 (Tailscale)

- [ ] T008 [US4] `ui.allowed_hosts` / `public_url` in the guard, serve wiring, launchd template, tests

## Phase: Polish

- [ ] T009 [US5] `docs/user-guide.md`, docs (configuration, operations, security, flows, owner manual), BPMN, CHANGELOG, spec status
