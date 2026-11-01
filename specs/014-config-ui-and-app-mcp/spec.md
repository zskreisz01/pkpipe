---
spec_id: 014-config-ui-and-app-mcp
title: Config UI and app MCP server
status: Completed
description: FastAPI + HTMX UI for editing config (draft, validate, smoke eval, publish to git), viewing runs and triage, and an MCP server so chat agents can run flows and confirm items.
relevant_when:
  - config UI
  - FastAPI
  - HTMX
  - publish
  - app MCP
  - Hermes
related: [013-evaluation-framework, 005-flow-engine, 004-obsidian-vault-mcp, 006-notifications]
updated: 2026-11-01
---

# Feature Specification: Config UI and app MCP server

**Feature Branch**: `spec/014-config-ui-and-app-mcp`
**Created**: 2026-10-31
**Status**: Completed
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [research.md](research.md) · [data-model.md](data-model.md) · [contracts/](contracts/)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Edit config safely (Priority: P1)

The owner edits a session instruction, runs the smoke eval and publishes.

**Independent Test**: API tests + browser e2e (`tests/integration/api/test_config_api.py`,
`tests/e2e/test_ui_browser.py`).

**Acceptance Scenarios**:

1. **Given** a draft, **When** publish is requested, **Then** validation and smoke eval run before a git commit
2. **Given** a publish, **When** done, **Then** the diff and rollback are available

### User Story 2 - Control from chat (Priority: P2)

A chat agent lists triage items and confirms them over MCP.

**Independent Test**: MCP contract tests over real HTTP (`tests/integration/api/test_app_mcp.py`).

**Acceptance Scenarios**:

1. **Given** `confirm_item(id)`, **When** called, **Then** the triage file is updated
2. **Given** the agent token, **When** an owner action is called, **Then** it is refused (owner actions need the owner token)

### Edge Cases

- Concurrent edits → optimistic lock on file hash (disk) and on the draft revision (two tabs).
- A publish whose files have uncommitted edits in the overlay repository is refused, never swept into a commit.
- A flow that already has an active run is not started a second time.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Access only on localhost / Tailscale with a token. The listener binds to `127.0.0.1` by default;
  only loopback and Tailscale addresses are accepted. Every request needs a bearer token or a login session, the
  `Host` header must be on the allowlist (DNS rebinding), cookie requests that change state need the CSRF header
  and a same-origin `Origin`, and responses carry a strict CSP.
- **FR-002**: App MCP tools: `run_flow`, `list_triage`, `confirm_item`, `override_item`, `get_run`,
  `release_quarantine`. `confirm_item`, `override_item` and `release_quarantine` are **owner actions**: they need
  the owner token, are audited and follow the triage domain rules; the agent token may only `run_flow`,
  `list_triage` and `get_run`.
- **FR-003**: Config edits are drafts in the app DB on the private overlay (never the public pack). Publish =
  validation (same loader as `pkpipe validate`) → spec 013 `PublishGate` → one git commit containing only the
  draft's files; blocked or invalid drafts write nothing.
- **FR-004**: Every publish keeps its diff and can be rolled back (`git revert`, validated before it is committed).
- **FR-005**: Runs and triage views; runs can be started from the UI; run detail never shows step output values.
- **FR-006**: `pkpipe serve` starts the API, UI, scheduler and app MCP server in one process; publish and
  rollback re-sync the schedules.

### Key Entities

- **Draft** – set of file changes with base hashes and a revision number ([data-model.md](data-model.md))
- **Publish** – a git commit made from a draft (or a rollback commit) with its gate decision
- **Audit entry** – every owner action (UI or MCP) with actor and target

## Success Criteria *(mandatory)*

- **SC-001**: A new category can be created end-to-end from the UI (form → draft → validate → smoke eval →
  publish → selectable in triage → rollback), verified in a real browser.
- **SC-002**: No request reaches the UI or MCP without the right token, and no state change is possible by a
  cross-site request (access tests, `test_http_access.py`).

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

### Session 2026-11-01 (implementation)

- Q: How is `confirm_item` over MCP authorized? → A: Confirm, override and quarantine release are the owner's
  decisions (constitution VI). They are authorized by the **owner token**; a separate **agent token** (read +
  `run_flow`) exists for chat agents that must not decide. Every decision is written to the audit log with its
  actor (`ui`, `mcp:owner`, `mcp:agent`). The vault MCP server that sessions can reach still refuses
  confirm/override; the owner writer is a separate function used only by the UI and the authenticated app MCP.
- Q: What does the UI edit? → A: The private overlay, which must be its own git repository. A pack-only file is
  edited as an overlay copy. Pushing the commit is not done by pkpipe (owner action).
- Q: Monaco editor? → A: Deferred. The editor is a plain textarea with server-side validation; vendoring Monaco
  offline (several MB) is a later UI polish.
- Q: Does this change the BPMN process? → A: No. Confirm/override/release in the UI or chat are additional
  channels for the owner decisions already in the model.

### Session 2026-11-01 (review)

- Q: Is triage text returned over MCP raw external content? → A: Yes, a resource is an INBOX file name or URL, so
  every `resource` (and a failed run's `error`) in an app MCP result is datamarked with random delimiters per call
  (constitution IV); refused owner actions of the agent token are audited.
- Q: Does the scheduler of `pkpipe serve` follow the one-active-run rule? → A: Yes, cron ticks go through the same
  check as the UI and MCP and are skipped while the flow runs.
- Q: What if the draft changes while its gate runs? → A: Publish is refused; only what was gated is committed. The
  publish record keeps every measured gate metric.

## Out of Scope

- Multi-user access.
- Monaco editor, live collaborative editing, pushing to a remote.
- Adding released false positives to the security eval dataset automatically (recorded in the audit log only).
