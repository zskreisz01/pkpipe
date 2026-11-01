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

- Core-grade security code: `src/pkpipe/security/{urlfilter,sanitize,heuristics,canary,verdict}.py`
- Steps: `src/pkpipe/steps/secure_fetch.py` (`fetch`, `sanitize`, `reader`; code `secure_fetch.url_check|scan|verdict`)
- Port + adapter: `ClassifierPort` (`pkpipe.ports`), `src/pkpipe/adapters/classifier_promptguard.py` (extra `promptguard`), fake `tests/fakes/classifier.py`
- web-fetch MCP server: `src/pkpipe/webfetch/{proxy,browsers,service,server}.py`, `mcp_servers/web_fetch/server.py`, `deploy/launchd/io.pkpipe.web-fetch.plist.example`
- Pack: `flows/secure_fetch.json`, `prompts/system/reader.md`, `instructions/secure_fetch/read.md`, `schemas/reader_extract.json`, `security/deny_list.txt`
- Evals: `evals/datasets/secure_fetch.jsonl` (security suite), `evals/datasets/sessions/secure_fetch/read.jsonl`, `evals/rubrics/reader_extract.md`

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| `pkpipe.security` is imported by `pkpipe.steps` | URL filter, sanitizer, heuristics, canary and verdict are deterministic security code shared by the steps, the web-fetch server and the eval | Duplicating it per caller, or a port for pure functions. It is held to the core import rule instead (`tests/unit/repo/test_architecture.py`) |
| Lightpanda runs as a subprocess, not in a container | Process isolation per page with a clean environment, and network isolation by the filtering proxy (every request checked after DNS) | A container alone does not stop SSRF to the host's private network; the proxy does, for both browsers |
