---
spec_id: 001-foundation-and-docs
status: Completed
description: Tasks for Repository foundation and documentation site
updated: 2026-10-31
---

# Tasks: Repository foundation and documentation site

**Input**: `specs/001-foundation-and-docs/`

Format: `- [x] T001 [USx] Description` · `[USx]` = user story

## Phase 1: Setup

- [x] T001 Remove build artefacts from the scaffold (`.venv`, caches); `uv lock`
- [x] T002 Add `.github/workflows/ci.yml` (setup-uv, ruff, pytest)

## Phase 2: Foundational

- [x] T003 Pydantic models + JSON Schema export for every config file (`pkpipe validate`)
- [x] T004 Effective-config version hash (sha256 of the merged JSON)

## Phase: User Story 1 (P1)

- [x] T005 [US1] Make ruff and pytest green; add `tests/fakes/` package with port fakes

## Phase: User Story 2 (P1)

- [x] T006 [US2] Merge rules for lists by `id` / `flow`, `enabled: false`; tests for each rule

## Phase: User Story 3 (P2)

- [x] T007 [US3] `npx create-docusaurus@latest website classic --typescript`; docs path `../docs`, routeBasePath `/`, blog off
- [x] T008 [US3] Enable `@docusaurus/theme-mermaid`; url `https://zskreisz01.github.io`, baseUrl `/pkpipe/`
- [x] T009 [US3] `.github/workflows/pages.yml` (Node 20, npm ci, build, upload-pages-artifact, deploy-pages)

## Phase: Polish & cross-cutting

- [x] T010 Link specs index from the docs sidebar
- [x] T011 README badges (CI, Pages)

## Verification

- [x] T012 CI green (ruff, pytest) - same commands verified locally; the GitHub run starts with the first PR
- [x] T013 Eval gates passed where applicable
- [x] T014 Docs, BPMN image and Mermaid updated if behaviour changed (constitution XI)
