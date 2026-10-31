---
spec_id: 013-evaluation-framework
title: Evaluation framework and gates
status: Draft
description: Technology options and decision for evaluation framework and gates.
updated: 2026-10-31
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

- Validate the choice with a spike before the spec moves to Active.
