# Implementation Plan: Config UI and app MCP server

**Branch**: `spec/014-config-ui-and-app-mcp` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

FastAPI + HTMX UI for editing config (draft, validate, smoke eval, publish to git), viewing runs and triage, and an MCP server so chat agents can run flows and confirm items.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: FastAPI, uvicorn, Jinja2, HTMX (vendored), FastMCP, Playwright (dev, e2e). Monaco deferred.
**Testing**: pytest with port fakes
**Target**: single machine, local model server

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| I. Local-first, model-agnostic | Yes | |
| II. Hexagonal core | Yes | |
| III. Configuration over code | Yes | |
| IV. Untrusted content isolation | Yes | |
| V. Code decides | Yes | |
| VI. Human in the loop | Yes | |
| VII. Public / private separation | Yes | |
| VIII. Small, isolated sessions | Yes | |
| IX. Measured change | Yes | |
| X. Observable by default | Yes | |
| XI. Design as diagram and as code | Yes | |
| XII. Tooling and language | Yes | |

## Project Structure

`src/pkpipe/api/` (`security.py` access control, `workspace.py` drafts, `publish.py` git publish and rollback,
`services.py` use cases, `app.py` routes, `templates/`, `static/`), `src/pkpipe/mcp/server.py` (app MCP tools),
`src/pkpipe/adapters/app_store_sqlite.py` (drafts, publishes, audit), `src/pkpipe/serve.py` (`pkpipe serve`),
owner writers in `pkpipe.obsidian.lists` and rules in `pkpipe.domain.triage`. Tests: `tests/unit/api`,
`tests/integration/api`, `tests/e2e/test_ui_browser.py`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
