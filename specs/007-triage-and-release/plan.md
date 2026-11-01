# Implementation Plan: Daily triage and morning release

**Branch**: `spec/007-triage-and-release` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

The 20:00 triage flow suggests a category per new INBOX item in an isolated session and notifies the owner; the 06:00 flow releases only confirmed or overridden items and fans them out to the category processes.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Flow JSON `triage.json`, `process_released.json`; step types llm, code, notify, subflow.
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

`packs/learning-inbox/flows/`, `src/pkpipe/domain/triage.py`, `evals/datasets/triage.jsonl`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
