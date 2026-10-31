---
spec_id: 003-observability
title: OpenTelemetry tracing and metrics
status: Draft
description: Technology options and decision for opentelemetry tracing and metrics.
updated: 2026-10-31
---

# Research: 003-observability

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| Phoenix (self-hosted) | Single container, traces + evals | Another service to run | Default for development |
| Langfuse | Already used by the owner, datasets | Heavier stack | Supported via OTLP |
| Vendor SDK tracing | Rich UI features | Lock-in | Rejected (constitution I, X) |

## R-2 – Decision and rationale

The chosen option is the one marked in the table; it keeps the core independent (constitution II) and the component replaceable by configuration (constitution I, III).

## R-3 – Risks and open points

- Validate the choice with a spike before the spec moves to Active.
