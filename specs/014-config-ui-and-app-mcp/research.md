---
spec_id: 014-config-ui-and-app-mcp
title: Config UI and app MCP server
status: Completed
description: Technology options and decision for config ui and app mcp server.
updated: 2026-11-01
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

- Spike result: HTMX 2 (vendored, 51 KB, no CDN) with Jinja2 fragments covers triage, runs, config editing,
  validate / smoke / publish and rollback in a real browser (tests/e2e/test_ui_browser.py). The strict CSP
  (`script-src 'self'`, no inline styles) forced `allowEval: false`, class-based styles and the headers-only CSRF
  token (`hx-headers`).
- Monaco is deferred: a textarea is enough for Markdown prompts and small JSON files; Monaco would need vendoring
  several MB for offline use.
- The app reads and writes the vault through the `pkpipe.obsidian` library in-process (same file locking as the
  vault MCP server) instead of through the MCP server: owner decisions must not be exposed on the server that
  LLM sessions can reach.
