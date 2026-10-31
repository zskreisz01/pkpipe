---
spec_id: 002-model-port
title: ModelPort and OpenAI-compatible model adapter
status: Draft
description: Technology options and decision for modelport and openai-compatible model adapter.
updated: 2026-10-31
---

# Research: 002-model-port

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| LM Studio structured output | OpenAI-compatible `response_format` with `json_schema` | Depends on server support | Use it; validate locally anyway |
| Grammar-constrained decoding only | Strong guarantees | Server-specific | Rejected: not portable |
| Prompt-only JSON | Works everywhere | Unreliable with 9B models | Fallback only |

## R-2 – Decision and rationale

The chosen option is the one marked in the table; it keeps the core independent (constitution II) and the component replaceable by configuration (constitution I, III).

## R-3 – Risks and open points

- Validate the choice with a spike before the spec moves to Active.
