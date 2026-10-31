# Implementation Plan: obsidian-vault MCP server

**Branch**: `spec/004-obsidian-vault-mcp` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

A FastMCP server over the vault folder for reading the INBOX, writing notes to PARA paths and maintaining the triage file and processing list, with path allowlists per tool.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: FastMCP, python-frontmatter, pathlib; file locks via `fcntl`.
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

`mcp_servers/obsidian_vault/`, `src/pkpipe/obsidian/` (parsers shared with tests).

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
