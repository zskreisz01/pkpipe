---
spec_id: 014-config-ui-and-app-mcp
title: Config UI and app MCP server
status: Completed
description: JSON API of the config UI backend (bearer token or login session).
updated: 2026-11-01
---

# Contract: HTTP API

Owner token (bearer) or login session; the agent token only reaches `/mcp` and `/api/health`. JSON errors are
`{"error": "..."}` with 401 (no token), 403 (scope, CSRF, cross-origin), 404, 409 (rules refuse), 421 (Host),
422 / 400 (invalid input).

| Method and path | Purpose |
| --- | --- |
| `GET /api/health` | liveness (no auth) |
| `GET /api/flows`, `GET /api/runs`, `GET /api/runs/{id}`, `POST /api/runs {flow, inputs?}` | runs (202, background) |
| `GET /api/triage`, `POST /api/triage/{id}/confirm`, `.../override {category}`, `.../release-quarantine` | owner decisions |
| `GET /api/config/files`, `GET /api/config/file?path=` | editable files; effective text and overlay hash |
| `POST /api/drafts`, `GET /api/drafts[/{id}]`, `DELETE /api/drafts/{id}` | drafts |
| `PUT /api/drafts/{id}/files {path, content, base_hash?, rev?}` | save a file into the draft (409 on conflict) |
| `POST /api/drafts/{id}/categories {id, label, subflow, para_path, triage_hint, rev?}` | add a category entry |
| `POST /api/drafts/{id}/validate`, `.../smoke`, `GET .../diff` | validation, eval gate dry run, diff |
| `POST /api/drafts/{id}/publish {summary?, override_reason?}` | validate, gate, commit (409 with the gate report when blocked) |
| `GET /api/publishes`, `GET /api/publishes/{id}/diff`, `POST /api/publishes/{id}/rollback` | history, diff, rollback |
| `GET /api/audit` | owner action log |

Browser pages: `/`, `/triage`, `/runs`, `/runs/{id}`, `/config`, `/config/edit?path=`, `/config/form?path=` (schema-driven form, spec 026), `/publishes/{id}`,
`/audit`, `/login`. HTMX fragments live under `/ui/...` and need the `X-CSRF-Token` header; spec 026 adds `/ui/config/form/save`, `/ui/config/form/action` and `/ui/config/cron-preview`.
