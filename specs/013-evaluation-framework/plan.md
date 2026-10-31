# Implementation Plan: Evaluation framework and gates

**Branch**: `spec/013-evaluation-framework` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

Five eval levels with datasets, code-first scoring, an LLM judge with calibration, recorded LLM responses for CI, and the publish gate used by the config UI.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: pytest, own runner; optional Phoenix experiments / Langfuse datasets via OTLP.
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

`src/pkpipe/evals/`, `evals/{datasets,rubrics,cassettes}/`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
