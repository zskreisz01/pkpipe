---
spec_id: 017-web-search
status: Completed
description: Plan for the web-search MCP server
updated: 2026-11-03
---

# Implementation Plan: web-search MCP server

**Branch**: `spec/017-web-search` | **Date**: 2026-11-03 | **Spec**: [spec.md](spec.md)

## Summary

`pkpipe.websearch` (`backends.py`, `service.py`, `server.py`) with a thin `mcp_servers/web_search/server.py`; SearXNG settings and compose wiring in `deploy/`.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`); FastMCP, httpx (already dependencies)
**Testing**: pytest; mock transport for backends, a stub SearXNG on loopback for the process test

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| I. Local-first, model-agnostic | Yes | SearXNG local by default; Brave is an opt-in egress |
| II. Hexagonal core | Yes | An outbound adapter behind the MCP ToolPort; the core is untouched |
| III. Configuration over code | Yes | Server option and `mcp_servers.json` |
| IV. Untrusted content isolation | Yes | Snippets are untrusted: datamarked by the engine, plain and bounded here |
| V. Code decides | Yes | Filtering and caps are code |
| VI. Human in the loop | n/a | |
| VII. Public / private separation | Yes | No personal data; queries are not logged |
| VIII. Small, isolated sessions | n/a | |
| IX. Measured change | Yes | Tests in CI |
| X. Observable by default | Partly | Outcome/count log events; the engine's tool span already covers the call |
| XI. Design as diagram and as code | Yes | The C4 container already shows `web-search :8702` (label updated); no process change |
