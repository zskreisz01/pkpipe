---
spec_id: 005-flow-engine
title: Flow engine: compiler, step executors, run service, scheduler
status: Draft
description: Technology options and decision for flow engine: compiler, step executors, run service, scheduler.
updated: 2026-10-31
---

# Research: 005-flow-engine

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| LangGraph | Checkpoints, Send fan-out, interrupt | Learning curve | Chosen |
| Prefect / Temporal | Mature workflow engines | Heavy for one machine | Rejected |
| Hermes agent as engine | Already running | No deterministic graph, hard to eval | Kept as chat front-end only |

## R-2 – Decision and rationale

The chosen option is the one marked in the table; it keeps the core independent (constitution II) and the component replaceable by configuration (constitution I, III).

## R-3 – Risks and open points

- Validate the choice with a spike before the spec moves to Active.
