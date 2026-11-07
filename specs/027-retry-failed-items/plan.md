---
spec_id: 027-retry-failed-items
status: Completed
description: Plan for the owner's retry of failed items
updated: 2026-11-07
---

# Implementation Plan: The owner retries failed items

**Branch**: `spec/027-retry-failed-items` | **Spec**: [spec.md](spec.md) | **Depends on**: 007, 009, 012, 014, 023, 024

## Summary

A pure guard `check_retry` in `domain/triage.py`; file operations `failed_processing_items` and `retry_item` in `obsidian/lists.py`; `Vault.failed_items` (read only, also a vault MCP tool) and `Vault.owner_retry` (owner channels only); a retry counter in the app DB (`AppStore.retry_count`, `bump_retry`); `AppServices.retry_item`, `retry_failed`, `failed_summary`; HTTP routes, UI controls, the app MCP tool `retry_items`, the CLI `pkpipe retry`; the report todo.

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| II. Hexagonal core | Yes | The guard is pure domain code; the vault adapter and the services call it |
| III. Configuration over code | Yes | `triage.retry_max`; nothing is written to the versioned config |
| IV. Untrusted content | Yes | No text is read or sent: ids, categories, states and counts only |
| V. Code decides | Yes | The guard, the counts and the writes are code; no model call |
| VI. Human in the loop | Yes | Only the owner channels retry; the pipeline still cannot leave `processed`; the owner's earlier decision is not asked again, it is not changed |
| VII. Public / private separation | Yes | Nothing personal |
| VIII. Local first | Yes | Local files and the local app DB |
| IX. Measured change | Yes | No LLM step; tests prove the guard and the end-to-end retry |
| X. Observable by default | Yes | Vault tool spans carry the tool name; audit has ids and counts |
| XI. Design as diagram and code | Yes | Process Inbox Items: Retry failed items (drawio, PNG, Mermaid) |
| Others | n/a | |

## Risks

- A retry of an item whose cause is not fixed fails again: bounded by `retry_max`.
- The failed line is the only marker of a failure (the triage row says `processed` either way): a hand-edited line that says `failed` makes an item retryable; that is the owner's own file and decision.
