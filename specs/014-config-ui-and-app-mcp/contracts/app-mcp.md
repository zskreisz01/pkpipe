---
spec_id: 014-config-ui-and-app-mcp
title: Config UI and app MCP server
status: Completed
description: Contract of the app MCP server tools, scopes and errors.
updated: 2026-11-01
---

# Contract: app MCP server

Endpoint `http://127.0.0.1:8787/mcp` (Streamable HTTP, same listener as the UI). Header
`Authorization: Bearer <token>` is required on every request; the `Host` header must be on the allowlist.

| Tool | Scope | Arguments | Returns |
| --- | --- | --- | --- |
| `run_flow` | agent, owner | `flow: str`, `inputs: object?` | run record (`run_id`, `flow_id`, `status`, `trigger = mcp`, `config_version`); the flow runs in the background |
| `get_run` | agent, owner | `run_id: str` | run record plus `steps_done` (names only; never output values) |
| `list_triage` | agent, owner | `state: str?` | `rows[]` (`item_id`, `resource` capped at 300 characters, `ai_suggested_category`, `confirm`, `override`, `state`, `category`), `invalid`, `conflicts` |
| `confirm_item` | **owner** | `item_id: str` | updated row |
| `override_item` | **owner** | `item_id: str`, `category: str` | updated row |
| `release_quarantine` | **owner** | `item_id: str` | updated row (`state = pending`) |

Errors are MCP tool errors (`isError`) with a short message: `authentication required`,
`'<tool>' is an owner action and needs the owner token`, `unknown flow`, `unknown run`, `unknown triage item`,
`unknown category`, `an item in state '<state>' cannot be confirmed or overridden`, `item is '<state>', not
quarantined`, `flow '<id>' already has an active run`.

Rules: a triage item in `suggested`, `pending` or `released` can be confirmed or overridden; `quarantined` and
`processed` cannot. An override must name a configured, enabled category. Every owner action and every
`run_flow` is written to the audit log with the actor `mcp:owner` / `mcp:agent`. Resource text is data, not
instructions (it can come from captured external content).
