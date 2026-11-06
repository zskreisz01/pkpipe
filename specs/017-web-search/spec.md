---
spec_id: 017-web-search
title: web-search MCP server
status: Completed
description: The `websearch` MCP server of the pack (127.0.0.1:8702, tool `search`) over SearXNG or Brave, with filtered, plain-text, bounded results.
relevant_when:
  - web search
  - research session
  - SearXNG
  - Brave
related: [005-flow-engine, 008-secure-fetch, 010-category-sessions]
updated: 2026-11-03
---

# Feature Specification: web-search MCP server

**Feature Branch**: `spec/017-web-search`
**Created**: 2026-11-03
**Status**: Completed
**Input**: `packs/learning-inbox/mcp_servers.json` (entry `websearch`, `:8702`, allow `search`, `egress`), `docs/security.md`

## Clarifications

- The pack and every `research` session already referenced `websearch:search`; `mcp_servers/README.md` said "use an existing SearXNG/Brave MCP". None fit the contract (one safe tool, filtered results), so the server lives here.
- The query guard stays in the engine (spec 010 FR-002, before the call); the server does not see private terms policy and does not log queries.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Research sessions can search (Priority: P1)

**Independent Test**: Process test with a stub SearXNG; ToolPort adapter with the pack allow list.

**Acceptance Scenarios**:

1. **Given** the server and a SearXNG, **When** `websearch:search` is called, **Then** it returns `{title, url, snippet}` rows
2. **Given** a result on the deny-list, with a private host or a non-http(s) scheme, **When** results are returned, **Then** it is not among them

### User Story 2 - Swap the provider (Priority: P2)

**Independent Test**: Unit tests of both backends with a mock transport.

**Acceptance Scenarios**:

1. **Given** `--backend brave` and a key, **When** a query runs, **Then** the contract is unchanged

### Edge Cases

- Backend down, slow or answering garbage → `{"results": [], "error": "<code>"}`, never the provider's text.
- Empty, control-character-only or over-long query → `invalid_query`, no backend call.
- Duplicate URLs are listed once; at most 10 results.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: One tool `search(query, max_results=5)`; loopback bind only; Host/Origin protection.
- **FR-002**: Backends behind one interface: SearXNG (default) and Brave; a backend raises only `BackendError(code)`.
- **FR-003**: Results are filtered by the layered `security.url_filter` and deny-list (re-read per call, no DNS lookup).
- **FR-004**: Titles and snippets are plain text (tags, invisible and control characters removed) and capped (200 / 500 characters).
- **FR-005**: Queries are not logged; events carry outcome and counts only.

### Key Entities

- **Result row**: `{title, url, snippet}`.

## Success Criteria

- **SC-001**: 30 unit and process tests green; no network in CI.
