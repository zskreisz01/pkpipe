---
spec_id: 008-secure-fetch
title: Secure fetch
status: Active
status_reason: "Implemented and gated; Prompt Guard 2 is a gated model that is not installed on the reference machine yet, so the classifier adapter is verified with a fake backend and its live test skips until the owner installs it (then: run tests/integration/security and the security eval with the classifier)."
description: Reusable process that turns a URL into a clean JSON extract: URL filter, Lightpanda fetch with Chromium fallback, sanitizing, Prompt Guard 2 scan, Reader session without tools and a code-made verdict.
relevant_when:
  - secure fetch
  - Lightpanda
  - Prompt Guard
  - Reader
  - canary
  - prompt injection
  - web-fetch MCP
related: [009-output-guard-and-quarantine, 010-category-sessions]
updated: 2026-11-01
---

# Feature Specification: Secure fetch

**Feature Branch**: `spec/008-secure-fetch`
**Created**: 2026-10-31
**Status**: Active (see status_reason)
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [research.md](research.md) · [data-model.md](data-model.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Clean extract for sessions (Priority: P1)

Category sessions receive a validated JSON extract instead of raw HTML.

**Independent Test**: Flow test with fixture pages.

**Acceptance Scenarios**:

1. **Given** a normal page, **When** secure fetch runs, **Then** the result is a schema-valid extract
2. **Given** the Reader output, **When** passed on, **Then** no raw page text reaches a session with tools

### User Story 2 - Injections are stopped (Priority: P1)

Pages with hidden or explicit injections end in a security flag.

**Independent Test**: Security eval (`security.jsonl`).

**Acceptance Scenarios**:

1. **Given** hidden HTML instructions, **When** fetched, **Then** sanitizing removes them or the classifier flags them
2. **Given** the canary appears in Reader output, **When** verdict runs, **Then** a security flag is raised

### Edge Cases

- Non-HTML content (PDF, video page) → typed extract with metadata only.
- Timeout → fallback once, then failed state (not a security flag).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: URL filter: deny-list, private network block, size limit.
- **FR-002**: Fetch via the `web-fetch` MCP (Lightpanda, private networks blocked, JS evaluate disabled); fallback Playwright + Chromium on error.
- **FR-003**: Sanitizer removes hidden elements, comments, zero-width and Unicode tag characters; regex heuristics score override attempts.
- **FR-004**: Prompt Guard 2 (86M) scans 512-token chunks.
- **FR-005**: Reader session: no tools, per-run canary, datamarking, output `{extract, facts, injection_suspected, evidence}`.
- **FR-006**: Verdict is code: any of heuristics, classifier, canary or `injection_suspected` → security flag.

### Key Entities

- **FetchResult**
- **ReaderExtract**
- **Verdict**

## Success Criteria *(mandatory)*

- **SC-001**: Detection ≥ 90% and false positives ≤ 5% on the security dataset.
- **SC-002**: Lightpanda fallback rate measured and reported.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

### Session 2026-11-01 (implementation)

- Q: How are private networks blocked for redirects and requests the page makes? → A: Both browsers send every request through a filtering proxy of the web-fetch server, which applies the URL filter after DNS resolution and connects to the checked address. A private sub-request or redirect hop makes the fetch `blocked`.
- Q: Lightpanda's MCP or its CLI? → A: The web-fetch MCP server exposes only `fetch_page` (+ `fetch_stats`); Lightpanda runs as `lightpanda fetch --dump html` per page with a clean environment. Lightpanda's own MCP (with `evaluate`, `click`, `fill`) is not used.
- Q: Is an oversized page a security flag? → A: Yes, as in the process model (size is part of the URL check). A timeout or failure after the fallback is a failed fetch, not a flag.
- Q: What if Prompt Guard 2 is not installed? → A: Fail closed: the verdict flags `classifier` (`classifier_unavailable`) unless the owner sets `security.prompt_guard.required: false`.
- Q: How do tests reach a local fixture server when loopback is blocked? → A: A test-only allowance for exact loopback `host:port` pairs: a port of the run (`fetch_test_loopback`) or `PKPIPE_WEBFETCH_TEST_LOOPBACK` for the server process. It is not a config key, so no pack or overlay can set it.
- Q: Non-HTML content? → A: PDF, image, audio and video pages (content type, `og:type` video or a video host) give a typed extract with metadata only (url, kind, content type, title); no Reader session.

## Out of Scope

- Authenticated pages.
