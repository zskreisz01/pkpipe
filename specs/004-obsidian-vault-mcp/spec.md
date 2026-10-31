---
spec_id: 004-obsidian-vault-mcp
title: obsidian-vault MCP server
status: Draft
description: A FastMCP server over the vault folder for reading the INBOX, writing notes to PARA paths and maintaining the triage file and processing list, with path allowlists per tool.
relevant_when:
  - Obsidian
  - vault
  - MCP
  - INBOX
  - PARA
  - frontmatter
related: [007-triage-and-release, 010-category-sessions]
updated: 2026-10-31
---

# Feature Specification: obsidian-vault MCP server

**Feature Branch**: `spec/004-obsidian-vault-mcp`
**Created**: 2026-10-31
**Status**: Draft
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [research.md](research.md) · [data-model.md](data-model.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent reads and writes the vault safely (Priority: P1)

Flows read INBOX items and write notes only inside allowed folders.

**Independent Test**: Tests on a temporary vault.

**Acceptance Scenarios**:

1. **Given** a write outside the allowlist, **When** `write_note` is called, **Then** it is rejected
2. **Given** concurrent edits, **When** writing, **Then** the write is atomic (temp file + rename)

### User Story 2 - Structured lists (Priority: P1)

The triage file and processing list are read and written as structured rows.

**Independent Test**: Round-trip tests.

**Acceptance Scenarios**:

1. **Given** an Inbox-Triage.md with checkboxes, **When** parsed, **Then** confirm/override values are returned per item
2. **Given** a list update, **When** written, **Then** manual edits in other rows are preserved

### Edge Cases

- Obsidian Sync conflict files (`… (conflict).md`) → ignored and reported.
- Triage file edited while the agent writes → retry with fresh read.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Tools: `list_folder`, `read_note`, `write_note`, `append_note`, `move_note`, `read_triage`, `update_triage`, `upsert_processing_list`.
- **FR-002**: Every tool MUST check paths against an allowlist from config; `..` and absolute paths outside the vault MUST be rejected.
- **FR-003**: Writes MUST be atomic and keep YAML front matter intact.
- **FR-004**: The server MUST run over Streamable HTTP on 127.0.0.1.

### Key Entities

- **TriageRow**
- **ProcessingListRow**
- **Note** (path, front matter, body)

## Success Criteria *(mandatory)*

- **SC-001**: Round-trip parse/write of the triage file is lossless on 20 fixture files.
- **SC-002**: Path traversal tests all rejected.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

## Out of Scope

- Running Obsidian or its plugins.
