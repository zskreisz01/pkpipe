# Implementation Plan: Context packs and weekly refresh

**Branch**: `spec/011-context-packs` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

Builds compact personal and enterprise context packs weekly from project notes (enterprise anonymized, local model only), keeps manual edits, and provides the public system context pack.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Flow `context_refresh.json`; query guard as a code step before web search tools.
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

`packs/learning-inbox/flows/context_refresh.json`, `src/pkpipe/security/query_guard.py`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
