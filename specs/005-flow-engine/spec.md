---
spec_id: 005-flow-engine
title: Flow engine: compiler, step executors, run service, scheduler
status: Completed
description: Compiles flow JSON into LangGraph StateGraphs, executes typed steps through ports, persists runs with a SQLite checkpointer, pins the config version per run and triggers flows from schedules.
relevant_when:
  - LangGraph
  - StateGraph
  - flow JSON
  - step types
  - checkpoint
  - APScheduler
  - run service
related: [002-model-port, 003-observability]
updated: 2026-11-01
---

# Feature Specification: Flow engine: compiler, step executors, run service, scheduler

**Feature Branch**: `spec/005-flow-engine`
**Created**: 2026-10-31
**Status**: Completed
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [research.md](research.md) · [data-model.md](data-model.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Flows are data (Priority: P1)

A flow defined in JSON runs without code changes.

**Independent Test**: Flow tests with fake ports.

**Acceptance Scenarios**:

1. **Given** a flow JSON with conditional edges, **When** compiled, **Then** the graph routes items by the condition
2. **Given** an unknown step type, **When** validating, **Then** a clear error names the node

### User Story 2 - Runs survive restarts (Priority: P1)

A run waiting on a long job resumes after the app restarts.

**Independent Test**: Kill/restart test with SQLite checkpointer.

**Acceptance Scenarios**:

1. **Given** a run in a wait step, **When** the process restarts, **Then** it resumes from the checkpoint
2. **Given** a config publish during a run, **When** the run continues, **Then** it uses its pinned version

### User Story 3 - Scheduled triggers (Priority: P2)

Flows run at configured times; missed runs execute once on wake.

**Independent Test**: Scheduler tests with a fake clock.

**Acceptance Scenarios**:

1. **Given** `0 20 * * *`, **When** the time passes, **Then** `triage` starts once
2. **Given** the machine slept, **When** it wakes within grace time, **Then** the run happens once

### Edge Cases

- Code step raises → node marked failed, item continues to error edge if defined, else run fails with span error.
- Two schedules for the same flow → validation error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Step types: `llm`, `llm_tools`, `tool`, `code`, `notify`, `subflow`, `wait` (poll + deadline), `guard`.
- **FR-002**: Sessions MUST be isolated: fresh message list, declared inputs only (constitution VIII).
- **FR-003**: `Send`-based fan-out MUST process items in parallel with a configurable limit (default 2).
- **FR-004**: The run service MUST store run id, flow id, config version, trigger and status in SQLite.
- **FR-005**: APScheduler with SQLite job store MUST sync from `schedules.json`; `max_instances=1`, `coalesce=True`.

### Key Entities

- **Flow**
- **Node / Step**
- **Run**
- **Checkpoint**
- **Schedule**

## Success Criteria *(mandatory)*

- **SC-001**: The triage and process_released flows run end-to-end with fake ports.
- **SC-002**: A restart during a wait step loses no item.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

### Session 2026-11-01 (implementation)

- Q: What does a run pin? → A: A config snapshot: the effective JSON (flows included) and the text assets (prompts,
  instructions, contexts, schemas). The config version hash covers both and the snapshot is stored in the app DB, so a
  resume compiles from it, never from disk. Adapters (model profiles, tool servers) are built per snapshot.
- Q: How does the core emit spans without importing OpenTelemetry (constitution II)? → A: New `TracerPort`; the
  adapter `pkpipe.adapters.tracer_otel` maps it onto `pkpipe.telemetry`. `ClockPort` and `RunStorePort` were added the
  same way, each with a fake in `tests/fakes/`.
- Q: How is a wait resumable? → A: Each poll is one checkpointed step that re-enters itself; the absolute deadline and
  poll count live in the checkpoint. Subflows run inside the calling node, so LangGraph nests their checkpoints and a
  resumed run continues each item's subflow where it stopped.
- Q: How do later specs add step types? → A: `@register_step` on a `StepExecutor` subclass with its own Pydantic
  config, listed in `pkpipe.steps.BUILTIN_MODULES` or exposed under the `pkpipe.steps` entry point group.
- Q: Who starts the scheduler? → A: `pkpipe.scheduler.FlowScheduler` is ready; `pkpipe serve` (spec 014) starts it.
  Eval gates for the LLM steps come with the eval framework (spec 013).

## Out of Scope

- Visual flow editor (read-only graph view belongs to spec 014).
