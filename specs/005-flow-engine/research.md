---
spec_id: 005-flow-engine
title: Flow engine: compiler, step executors, run service, scheduler
status: Completed
description: Technology options and decision for flow engine: compiler, step executors, run service, scheduler.
updated: 2026-11-01
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

- Validate the choice with a spike before the spec moves to Active. **Done (2026-11-01):** a spike with
  `SqliteSaver`, `Send` fan-out and `Command` routing showed (1) a subgraph invoked inside a fan-out task resumes from
  its own checkpoint after `kill -9`, (2) items finished before the crash are not re-run (pending writes), (3)
  `max_concurrency` bounds parallel items, (4) several fan-out tasks routing to one join node run it once.
- APScheduler 3 `configure()` resets unspecified options (time zone); the scheduler passes it again. Re-adding an
  unchanged job would recompute its next fire time and lose a run missed while the app was down, so sync keeps it.
