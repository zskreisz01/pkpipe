# Implementation Plan: Flow engine: compiler, step executors, run service, scheduler

**Branch**: `spec/005-flow-engine` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

Compiles flow JSON into LangGraph StateGraphs, executes typed steps through ports, persists runs with a SQLite checkpointer, pins the config version per run and triggers flows from schedules.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: LangGraph (StateGraph, Send, SQLite checkpointer), APScheduler 3, Pydantic flow schema.
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

`src/pkpipe/engine/{compiler.py,runs.py,registry.py}`, `src/pkpipe/steps/*`, `src/pkpipe/scheduler/`, `packs/learning-inbox/flows/*.json`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
