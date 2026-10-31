# Implementation Plan: learning-gateway MCP and Process Learning Material

**Branch**: `spec/012-learning-gateway` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

An MCP server that hides the learning backend (Open Notebook with local TTS by default, NotebookLM via browser automation optional, fake for tests) and the learning-material process with polling and timeout.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Open Notebook REST (`/api/notebooks`, `/api/sources`, `/api/podcasts/generate`, `/api/podcasts/jobs/{id}`), Speaches or Kokoro-FastAPI TTS, notebooklm-mcp for the browser backend.
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

`mcp_servers/learning_gateway/{server.py,backends/{open_notebook.py,notebooklm_browser.py,fake.py}}`, `packs/learning-inbox/flows/learning_material.json`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
