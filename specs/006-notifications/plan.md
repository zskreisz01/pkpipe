# Implementation Plan: Notification port and channels

**Branch**: `spec/006-notifications` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

NotifyPort with Discord webhook, Teams workflow webhook and Apprise adapters, event-to-channel routing and message templates per channel type.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: httpx, apprise, Jinja2.
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

`src/pkpipe/adapters/notify_*.py`, `packs/learning-inbox/templates/`.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
