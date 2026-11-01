---
spec_id: 013-evaluation-framework
title: Evaluation framework and gates
status: Completed
description: Technology options and decision for evaluation framework and gates.
updated: 2026-11-01
---

# Research: 013-evaluation-framework

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| Own runner + pytest | Simple, fits gates | Less UI | Chosen |
| DeepEval | Many metrics | Extra dependency | Optional |
| Phoenix experiments | Traces + evals | Backend-specific | Optional view |

## R-2 – Decision and rationale

The chosen option is the one marked in the table; it keeps the core independent (constitution II) and the component replaceable by configuration (constitution I, III).

## R-3 – Risks and open points

- Validated: the own runner (`pkpipe.evals`) runs all suites with pytest-independent CLI and records/replays model
  responses; Phoenix / Langfuse see eval runs through OTLP spans with `eval.run_id`. DeepEval was not needed.
