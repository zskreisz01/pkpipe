# Implementation Plan: ModelPort and OpenAI-compatible model adapter

**Branch**: `spec/002-model-port` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

A ModelPort with structured (JSON-schema) completions and an adapter for any OpenAI-compatible server, configured by model profiles. Default target: LM Studio with a ~9B instruct model.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: `langchain-openai` (ChatOpenAI) or `openai` client with `base_url`; Pydantic for schema validation; tenacity-style retry implemented in the adapter.
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

`src/pkpipe/ports/ports.py`, `src/pkpipe/adapters/model_openai.py`, `tests/fakes/model.py`, `packs/learning-inbox/models.json`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
