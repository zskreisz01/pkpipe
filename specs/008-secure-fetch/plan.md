# Implementation Plan: Secure fetch

**Branch**: `spec/008-secure-fetch` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

Reusable process that turns a URL into a clean JSON extract: URL filter, Lightpanda fetch with Chromium fallback, sanitizing, Prompt Guard 2 scan, Reader session without tools and a code-made verdict.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Lightpanda (MCP), Playwright, transformers (Prompt Guard 2), selectolax/BeautifulSoup for sanitizing.
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

`src/pkpipe/security/{sanitize.py,classifier.py,canary.py,verdict.py}`, `packs/learning-inbox/flows/secure_fetch.json`, `evals/datasets/security.sample.jsonl`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
