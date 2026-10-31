# Implementation Plan: System tool candidates

**Branch**: `spec/016-system-tool-candidates` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

A sixth Tool session checks whether an evaluated tool could improve pkpipe itself; candidates go to a separate list and are adopted only after a spike that wins an A/B on the eval suite, recorded in an ADR.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Session `system_relevance` + list writer; ADR template.
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

`packs/learning-inbox/instructions/tool/system_relevance.md`, `docs/adr/`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
