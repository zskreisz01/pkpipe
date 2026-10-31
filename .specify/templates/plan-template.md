# Implementation Plan: [FEATURE]

**Branch**: `spec/[###-feature-name]` | **Date**: [DATE] | **Spec**: [spec.md](spec.md)

## Summary

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: [...]
**Storage**: [...]
**Testing**: pytest (+ eval suite where LLM steps are involved)
**Target**: single machine (macOS / Linux), local model server

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| I. Local-first, model-agnostic | | |
| II. Hexagonal core | | |
| III. Configuration over code | | |
| IV. Untrusted content isolation | | |
| V. Code decides | | |
| VI. Human in the loop | | |
| VII. Public / private separation | | |
| VIII. Small, isolated sessions | | |
| IX. Measured change | | |
| X. Observable by default | | |
| XI. Design as diagram and as code | | |
| XII. Tooling and language | | |

## Project Structure

[files and modules touched]

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
