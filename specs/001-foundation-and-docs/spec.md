---
spec_id: 001-foundation-and-docs
title: Repository foundation and documentation site
status: Active
description: Repository skeleton, layered config loader, CLI, CI and the Docusaurus documentation site with the final process model and C4 views. The base every other spec builds on.
relevant_when:
  - repository layout
  - pyproject
  - CI
  - Docusaurus
  - config loader
  - docs
related: []
updated: 2026-10-31
---

# Feature Specification: Repository foundation and documentation site

**Feature Branch**: `spec/001-foundation-and-docs`
**Created**: 2026-10-31
**Status**: Active
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Developer can build and test the project (Priority: P1)

A contributor clones the repo and gets a green build with one command.

**Independent Test**: `uv sync && uv run pytest && uv run ruff check` in a clean clone.

**Acceptance Scenarios**:

1. **Given** a clean clone, **When** `uv sync` and `uv run pytest` run, **Then** all tests pass
2. **Given** a pull request, **When** CI runs, **Then** ruff and pytest gate the merge

### User Story 2 - Config layers merge predictably (Priority: P1)

The engine loads the public pack and an optional private overlay into one effective config.

**Independent Test**: Unit tests on `engine.registry` with a temporary overlay.

**Acceptance Scenarios**:

1. **Given** an overlay category with an existing id, **When** loading, **Then** fields merge key by key
2. **Given** `enabled: false`, **When** loading, **Then** the category is removed
3. **Given** overlay schedules, **When** loading, **Then** they are added, not replacing the pack's

### User Story 3 - Design is published as a website (Priority: P2)

Visitors read architecture, process model and specs on GitHub Pages.

**Independent Test**: `npm run build` in `website/` and the Pages workflow on `main`.

**Acceptance Scenarios**:

1. **Given** a push to `main` touching `website/` or `docs/`, **When** the pages workflow runs, **Then** the site is deployed
2. **Given** a Mermaid block, **When** the site renders, **Then** the diagram is drawn (theme-mermaid)

### Edge Cases

- Overlay directory missing → load the pack only, no error.
- Duplicate category ids inside one layer → validation error naming the file.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The repository MUST use the layout in `docs/architecture.md` (`src/pkpipe`, `packs/`, `mcp_servers/`, `evals/`, `docs/`, `website/`, `specs/`).
- **FR-002**: The config loader MUST merge core defaults, the public pack and an optional overlay directory (`PKPIPE_OVERLAY_DIR`); lists of objects merge by `id` or `flow`.
- **FR-003**: The CLI MUST expose `serve`, `run <flow>`, `eval --suite`, `validate`.
- **FR-004**: CI MUST run ruff and pytest on every pull request; a separate workflow MUST build and deploy the Docusaurus site to GitHub Pages.
- **FR-005**: The Docusaurus site MUST include `docs/` as content, render Mermaid (`@docusaurus/theme-mermaid`), and serve images from `docs/img/`.
- **FR-006**: `docs/design/` MUST contain the BPMN and C4 draw.io sources; `docs/img/` the PNG exports.

### Key Entities

- **Config layer**: a directory of JSON + Markdown files (pack or overlay).
- **Effective config**: the merged result with a version hash.

## Success Criteria *(mandatory)*

- **SC-001**: A clean clone passes `uv sync && uv run pytest` in under 2 minutes.
- **SC-002**: The GitHub Pages site shows every process page with its image and Mermaid diagram.
- **SC-003**: Adding an overlay category requires no code change.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

## Out of Scope

- The config UI (spec 014).
- Publishing to PyPI.
