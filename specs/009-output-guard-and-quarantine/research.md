---
spec_id: 009-output-guard-and-quarantine
title: Output guard and quarantine
status: Draft
description: Technology options and decision for output guard and quarantine.
updated: 2026-10-31
---

# Research: 009-output-guard-and-quarantine

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| Qwen3Guard | Multilingual, severity levels, Apache-2.0 | Jailbreak detection only on input | Harmful-content check |
| Prompt Guard 2 on outputs | Catches instruction-like text | Binary label | Injection check on outputs |
| LLM self-check with the main model | No extra model | Attackable, slow | Optional second opinion only |

## R-2 – Decision and rationale

The chosen option is the one marked in the table; it keeps the core independent (constitution II) and the component replaceable by configuration (constitution I, III).

## R-3 – Risks and open points

- Validate the choice with a spike before the spec moves to Active.
