---
spec_id: 026-config-editor-forms
status: Completed
description: Tasks for the config editor forms
updated: 2026-11-07
---

# Tasks: Config editor forms in the UI

Format: `- [x] T001 [USx] Description`

## Phase: Setup

- [x] T001 Spec, plan, data model, tasks, index

## Phase: Form engine

- [x] T002 [US1] `api/forms.py`: schema to field tree (objects, maps, arrays, scalars, enums, nullable, JSON fallback), parse with per-field errors, round trip test for every pack section
- [x] T003 [US2] Row actions on the flat form (add, remove, move) with path check; map keys; minimal overlay (`minimize`); merged-layer validation with row mapping by merge key

## Phase: UI

- [x] T004 [US1] Routes and templates: form page, save into the draft, action fragment, tabs between form and JSON, file list links
- [x] T005 [US2] Pickers (flows, categories, events, channels, env names), links from sessions to instruction, system and context files, token hint in the instruction editor with `config-forms.js`
- [x] T006 [US4] Cron preview with the scheduler's parser
- [x] T007 [US3] Secrets: `env:NAME` only in the form and in `check_content`, literals never rendered, set / not set pill

## Phase: Polish

- [x] T008 Integration tests (pages, actions, errors, secrets, escaping, CSRF, broken JSON), docs (user guide, configuration, security), CHANGELOG, `api/forms.py` on the mypy list
