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

- Spike done (2026-11-01, Lightpanda 1.0.2): `lightpanda fetch --json --dump html` renders JavaScript, reports status and headers (content type for PDF), honours `--http-proxy` for HTTP and CONNECT, fails with `ResponseTooLarge` above `--http-max-response-size`. `--block-private-networks` also blocks the proxy connection, so network policy lives in the proxy instead. Chromium routes loopback through the proxy only with `--proxy-bypass-list=<-loopback>`.
- Lightpanda's own MCP exposes ~30 tools including `evaluate`, `click`, `fill`: not used; the web-fetch server has one fetch tool.
- Plain HTTP + Trafilatura is not implemented (Lightpanda covers static pages).
