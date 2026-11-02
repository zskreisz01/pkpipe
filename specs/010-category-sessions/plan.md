# Implementation Plan: Category session plans and Process Other Resource

**Branch**: `spec/010-category-sessions` | **Date**: 2026-11-01 | **Spec**: [spec.md](spec.md)

## Summary

Executes per-category session plans from config (Tool with six sessions, Marketing, Misc and overlay categories), stores notes in PARA folders and maintains the processing list.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Flow `other_resource.json` with the `session_plan` step (the plan is data: the step runs its sessions in order; no generated subgraph, see the spec clarifications).
**Testing**: pytest with port fakes, eval suite
**Target**: single machine, local model server

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| I. Local-first, model-agnostic | Yes | |
| II. Hexagonal core | Yes | |
| III. Configuration over code | Yes | |
| IV. Untrusted content isolation | Yes | |
| V. Code decides | Yes | |
| VI. Human in the loop | Yes | |
| VII. Public / private separation | Yes | |
| VIII. Small, isolated sessions | Yes | |
| IX. Measured change | Yes | |
| X. Observable by default | Yes | |
| XI. Design as diagram and as code | Yes | |
| XII. Tooling and language | Yes | |

## Project Structure

`packs/learning-inbox/{categories.json,instructions/,prompts/system/,schemas/,flows/other_resource.json}`, `src/pkpipe/steps/session_plan.py` (step + code functions), `src/pkpipe/domain/session_plan.py` (validation, note rendering), `src/pkpipe/security/query_guard.py`, `src/pkpipe/evals/category_sessions.py` (sessions as eval targets), `evals/datasets/sessions/<category>/<session>.jsonl`, `evals/rubrics/`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
