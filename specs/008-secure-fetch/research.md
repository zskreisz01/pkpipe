---
spec_id: 008-secure-fetch
title: Secure fetch
status: Draft
description: Technology options and decision for secure fetch.
updated: 2026-10-31
---

# Research: 008-secure-fetch

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| Lightpanda | Low memory, MCP, Markdown output | Beta, partial Web APIs, AGPL | Primary fetcher, separate process |
| Playwright + Chromium | Full web platform | Heavy | Fallback |
| Plain HTTP + Trafilatura | Fast | No JS | Used for static pages if configured |

## R-2 – Decision and rationale

The chosen option is the one marked in the table; it keeps the core independent (constitution II) and the component replaceable by configuration (constitution I, III).

## R-3 – Risks and open points

- Validate the choice with a spike before the spec moves to Active.
