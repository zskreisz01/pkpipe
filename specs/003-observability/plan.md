# Implementation Plan: OpenTelemetry tracing and metrics

**Branch**: `spec/003-observability` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

OTLP traces and metrics for flows, steps, LLM, tool and HTTP calls, with pkpipe-specific attributes, to Langfuse or Phoenix. Content capture configurable and off for private data.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: opentelemetry-sdk, opentelemetry-exporter-otlp, openinference-instrumentation-langchain, opentelemetry-instrumentation-fastapi/httpx.
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

`src/pkpipe/telemetry/`, `tests/test_telemetry.py`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
