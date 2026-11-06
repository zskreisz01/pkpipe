---
spec_id: 017-web-search
status: Completed
description: Tasks for the web-search MCP server
updated: 2026-11-03
---

# Tasks: web-search MCP server

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 1 (P1)

- [x] T001 [US1] Service: query validation, URL-policy filter, text cleaning, caps, error codes (`pkpipe.websearch.service`)
- [x] T002 [US1] SearXNG backend + server (`pkpipe.websearch.server`, `mcp_servers/web_search`)
- [x] T003 [US1] SearXNG settings with JSON API, compose wiring, launchd example, docs
- [x] T004 [US1] Process test with stub SearXNG and ToolPort adapter

## Phase: User Story 2 (P2)

- [x] T005 [US2] Brave backend + unit tests
