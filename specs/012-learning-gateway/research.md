---
spec_id: 012-learning-gateway
title: learning-gateway MCP and Process Learning Material
status: Draft
description: Technology options and decision for learning-gateway mcp and process learning material.
updated: 2026-10-31
---

# Research: 012-learning-gateway

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| Open Notebook + local TTS | Official REST API, local, MIT | Voice quality below NotebookLM | Default |
| NotebookLM (consumer) via browser automation | Best quality, mobile app | No official API, fragile | Optional backend |
| NotebookLM Enterprise API | Official | Paid Google Cloud product | Not planned |

## R-2 – Decision and rationale

The chosen option is the one marked in the table; it keeps the core independent (constitution II) and the component replaceable by configuration (constitution I, III).

## R-3 – Risks and open points

- Validate the choice with a spike before the spec moves to Active.
