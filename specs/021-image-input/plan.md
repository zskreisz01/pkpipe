---
spec_id: 021-image-input
status: Draft
description: Plan for image input and the image description session
updated: 2026-11-05
---

# Implementation Plan: Image input and the image description session

**Branch**: `spec/021-image-input` | **Spec**: [spec.md](spec.md) | **Depends on**: 020

## Summary

Extend `ModelPort` with optional images (adapter builds `image_url` parts), add `read_image` to the obsidian-vault server, a code step `image.prepare` (Pillow, optional extra `images`), a tool-less `describe_image` session with its schema and Reader-style prompt, a description cache in the app DB, and the data class `image` with its validation. The triage flow gets an image branch before the category session.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`); Pillow as optional extra `images`
**Testing**: fake vision model scripted by digest; images rendered in test code; vault tool tests with a temp attachments folder, traversal and symlink cases; trace/log leak test; live eval case marked `live`
**Operations**: the vision profile is a separate model from the text model; with 32 GB the two share memory, so the image batch runs as its own step with the text model idle (documented in `docs/operations.md`); throughput measured at about 53 s per image

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| I. Local-first | Yes | Loopback profiles only for data class `image` |
| II. Hexagonal core | Yes | Port extended, adapter builds the HTTP shape; the core handles `ImageInput` only |
| III. Configuration over code | Yes | `images.json`, profile `vision` |
| IV. Untrusted content | Yes | Tool-less session, validated JSON, output guard, canary |
| V. Code decides | Yes | Preparation, cache, caps and `needs_owner` rules are code; the model only describes |
| VI. Human in the loop | Yes | Failure, flag or deferral leaves the item to the owner |
| VII. Public / private separation | Yes | Synthetic images only; the owner's images never enter a repository |
| IX. Measured change | Yes | Eval suite `images` with a different judge model |
| X. Observable by default | Yes | Spans with counts, bytes and cache hits; no content |
| XI. Design as diagram and as code | Yes | BPMN: triage gets "Describe image" (sub-process: read, prepare, describe, guard) (drawio, PNG, Mermaid) |
| Others | n/a | |

## Structure

- `pkpipe/ports/ports.py` (`ImageInput`, `ModelPort.complete(images=…)`), `pkpipe/adapters/model_openai.py`, fake model
- `pkpipe/obsidian` (`read_image`, policy tool list), `mcp_servers/obsidian_vault`
- `pkpipe/steps/image_code.py` (`image.prepare`, cache lookup/store), session `describe_image` in the pack (prompt `viewer.md`, schema, step defaults)
- `pkpipe/domain/layout.py` (`attachments_dir` read-only), `engine/config_models.py` (`vision`, `images.json`, data class `image`), compiler checks
- `pkpipe/evals` suite `images`
- docs: configuration, operations (model memory, throughput), security, BPMN pages

## Risks

- Reasoning models spend the whole token budget; mitigated by `max_tokens`, one retry and an owner fallback; a non-reasoning vision model is the default
- Throughput on a memory-starved machine: cap per run, cache, batch step
- Pillow decoding of hostile files: size and pixel limits (decompression bomb guard) before decoding
- Text in images that looks like a URL or command: it is data; URLs go through the URL policy before any fetch
