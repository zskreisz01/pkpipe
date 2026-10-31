---
spec_id: 004-obsidian-vault-mcp
title: obsidian-vault MCP server
status: Completed
description: Technology options and decision for obsidian-vault mcp server.
updated: 2026-10-31
---

# Research: 004-obsidian-vault-mcp

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| Own FastMCP server | Exact tools, allowlist, no running Obsidian | Code to maintain | Chosen |
| Obsidian Local REST API plugin + MCP | Uses Obsidian's index | Requires Obsidian running | Rejected |
| Generic filesystem MCP | Ready-made | No triage semantics, broad access | Rejected |

## R-2 – Decision and rationale

The chosen option is the one marked in the table; it keeps the core independent (constitution II) and the component replaceable by configuration (constitution I, III).

## R-3 – Risks and open points

- Validate the choice with a spike before the spec moves to Active.
