# Implementation Plan: Output guard and quarantine

**Branch**: `spec/009-output-guard-and-quarantine` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

Checks every output derived from external content before it is written (Qwen3Guard, Prompt Guard 2, canary) and handles security flags: malicious URL log, deny-list, inert quarantine, alert, owner release.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Qwen3Guard-Gen (0.6B/4B) via transformers or LM Studio, Prompt Guard 2.
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

`src/pkpipe/security/guard.py`, `packs/learning-inbox/flows/handle_security_flag.json`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
