# Implementation Plan: Config UI and app MCP server

**Branch**: `spec/014-config-ui-and-app-mcp` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

FastAPI + HTMX UI for editing config (draft, validate, smoke eval, publish to git), viewing runs and triage, and an MCP server so chat agents can run flows and confirm items.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: FastAPI, Jinja2, HTMX, Monaco editor, FastMCP.
**Testing**: pytest with port fakes
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

`src/pkpipe/api/`, `src/pkpipe/mcp/server.py`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
