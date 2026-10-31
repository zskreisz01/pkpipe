# Implementation Plan: Self-improvement cycle (overlay)

**Branch**: `spec/015-self-improvement-cycle` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

Daily extraction of improvement items from journal notes with a local session without tools, weekly evidence collection and verdict proposals, owner review, and system changes through the eval gate. Ships as an optional overlay-enabled flow.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Flows `improvement_extract.json`, `improvement_review.json` (enabled via overlay schedules).
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

`packs/learning-inbox/flows/`, `src/pkpipe/domain/improvement.py`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
