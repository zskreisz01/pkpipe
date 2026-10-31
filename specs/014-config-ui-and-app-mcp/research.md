---
spec_id: 014-config-ui-and-app-mcp
title: Config UI and app MCP server
status: Draft
description: Technology options and decision for config ui and app mcp server.
updated: 2026-10-31
---

# Research: 014-config-ui-and-app-mcp

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| HTMX + Jinja | No SPA build | Less rich | Chosen |
| React SPA | Rich editors | Build chain | Rejected for now |

## R-2 – Decision and rationale

The chosen option is the one marked in the table; it keeps the core independent (constitution II) and the component replaceable by configuration (constitution I, III).

## R-3 – Risks and open points

- Validate the choice with a spike before the spec moves to Active.
