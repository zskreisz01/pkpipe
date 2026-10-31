# Implementation Plan: Repository foundation and documentation site

**Branch**: `spec/001-foundation-and-docs` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

Repository skeleton, layered config loader, CLI, CI and the Docusaurus documentation site with the final process model and C4 views. The base every other spec builds on.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Docusaurus 3 (Node 20), GitHub Actions (`astral-sh/setup-uv`, `actions/deploy-pages`), Pydantic 2 for schemas.
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

`pyproject.toml`, `src/pkpipe/{cli.py,engine/registry.py}`, `packs/learning-inbox/`, `.github/workflows/{ci,pages}.yml`, `website/`, `docs/`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
