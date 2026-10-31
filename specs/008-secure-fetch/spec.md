---
spec_id: 008-secure-fetch
title: Secure fetch
status: Draft
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
updated: 2026-10-31
---

# Feature Specification: Secure fetch

**Feature Branch**: `spec/008-secure-fetch`
**Created**: 2026-10-31
**Status**: Draft
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

## Out of Scope

- Authenticated pages.
