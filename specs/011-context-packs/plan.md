# Implementation Plan: Context packs and weekly refresh

**Branch**: `spec/011-context-packs` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

Builds compact personal and enterprise context packs weekly from project notes (enterprise anonymized, local model only), keeps manual edits, and provides the public system context pack.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Flows `context_refresh.json` and `context_pack.json` (data class `context`); the query guard of spec 010 (`security.query_guard`) fed by the refresh.
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

`packs/learning-inbox/{flows/context_refresh.json,flows/context_pack.json,contexts/system.md,instructions/context/,schemas/context_*.json,security/query_guard_terms.txt}`, `src/pkpipe/domain/context_pack.py` (names, scrub, merge, budget), `src/pkpipe/steps/context_code.py` (`context.*` code functions), `ContextStorePort` + `adapters/context_store_files.py`, `evals` suite `context_leak` (`evals/datasets/context_leak.jsonl`), `src/pkpipe/security/query_guard.py` (spec 010).

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
