---
spec_id: 013-evaluation-framework
title: Evaluation framework and gates
status: Completed
description: Five eval levels with datasets, code-first scoring, an LLM judge with calibration, recorded LLM responses for CI, and the publish gate used by the config UI.
relevant_when:
  - eval
  - dataset
  - LLM judge
  - gate
  - cassette
  - regression
related: [014-config-ui-and-app-mcp]
updated: 2026-11-01
---

# Feature Specification: Evaluation framework and gates

**Feature Branch**: `spec/013-evaluation-framework`
**Created**: 2026-10-31
**Status**: Completed
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [research.md](research.md) · [data-model.md](data-model.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Measure categorization (Priority: P1)

The owner sees triage accuracy and confusion per category.

**Independent Test**: `pkpipe eval --suite triage`.

**Acceptance Scenarios**:

1. **Given** the triage dataset, **When** the suite runs, **Then** accuracy, per-category F1 and a confusion matrix are reported

### User Story 2 - Gate a change (Priority: P1)

A change to an instruction cannot be published if its eval drops below the gate.

**Independent Test**: Gate tests.

**Acceptance Scenarios**:

1. **Given** a failing session eval, **When** publishing, **Then** publish is blocked unless overridden with a reason

### User Story 3 - CI without a model (Priority: P2)

Flow tests run in CI with recorded responses.

**Independent Test**: CI run.

**Acceptance Scenarios**:

1. **Given** cassettes, **When** CI runs, **Then** flow tests pass without network or model

### Edge Cases

- Judge unavailable → step eval skipped and publish blocked for LLM steps.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Suites: deterministic, step, flow, security, production metrics.
- **FR-002**: Judge profile differs from the model under test.
- **FR-003**: Pairwise A/B judging for instruction changes.
- **FR-004**: Reports as JSON + Markdown; every run traced with `eval.run_id`.

### Key Entities

- **EvalCase**
- **EvalRun**
- **Gate**

## Success Criteria *(mandatory)*

- **SC-001**: Gate thresholds stored in config and enforced in publish.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

### Session 2026-11-01 (implementation)

- Q: Which judge model? → A: A different model than the one under test (FR-002): the public pack uses
  `google/gemma-4-12b` (`PKPIPE_JUDGE_MODEL`); `pkpipe validate` rejects a judge profile used by an LLM step and the
  runner refuses a judge that resolves to the same endpoint and model.
- Q: Where are the gates? → A: `evals.json` (layered config, schema exported); thresholds only move upwards.
- Q: Dataset gaps? → A: `context_leak.jsonl` (query guard, spec 011) and `improvement_extract.jsonl` (spec 015) are
  added with those specs; the security suite measures the current pattern guard (gate 0.80) until the classifier guard
  of spec 009 raises it to 0.90.
- Status: Completed. Measured live (qwen3.5-9b-mlx, judge gemma-4-12b) – see docs/evaluation.md.

## Out of Scope

- Hosted eval platforms.
