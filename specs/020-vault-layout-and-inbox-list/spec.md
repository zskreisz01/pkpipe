---
spec_id: 020-vault-layout-and-inbox-list
title: Configurable vault layout and list-style INBOX
status: Completed
description: The vault folders and files the engine uses become one configurable layout, and the INBOX can be a single list file whose bullet entries (links, image embeds, text with a link) are the triage items.
relevant_when:
  - vault layout
  - inbox list
  - INBOX.md
  - folder names
  - journal path
related: [004-obsidian-vault-mcp, 007-triage-and-release, 010-category-sessions, 015-self-improvement-cycle, 016-system-tool-candidates, 019-vault-snapshots]
updated: 2026-11-04
---

# Feature Specification: Configurable vault layout and list-style INBOX

**Feature Branch**: `spec/020-vault-layout-and-inbox-list`
**Created**: 2026-11-04
**Status**: Completed
**Input**: Owner: the INBOX must be parsed as it really is (one list file, entries marked with bullets); the vault structure must be configurable. Real vault: `1-Inbox`, `2-Projects`, `3-Areas`, `4-Resources`, `6-Archive`, `DailyJournals/<year>/`.
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **Real INBOX format** (observed, 92 lines, ~80 entries):
   - YAML front matter, a title and an intro text, then a `---` line; entries follow.
   - An entry starts with a bullet marker (`- `). Kinds: **link** (`- https://…`), **image embed** (`- ![[name.jpg]]`), **text + link** (`- Title` and the URL on the next, unindented line), **plain text**.
   - A line that is not a bullet and not blank continues the previous entry.
   - `<!-- … -->` comments are owner notes: kept, never an entry. The same link can occur twice.
   - Tracking parameters (`utm_*`, `fbclid`, …) are part of the line and are not part of the item identity.
2. **One layout, one place.** Every folder or file the engine names (INBOX folder and list file, triage file, processing list, archive folder, agent-task folder, tool candidates file, improvement file, quarantine folder, journal pattern, attachments folder) comes from one `layout` block of the vault policy. Defaults equal today's constants, so the public pack behaves as before.
3. **Consuming an entry.** After an item is processed, its raw entry is removed from the list and appended to a dated archive file; the rest of the list (header, comments, entries added meanwhile) stays byte-identical. **Decided 2026-11-04 (owner delegated; defaults):** processed entries are removed from the list and appended to a dated archive file (`inbox_archive_pattern`). Quarantined and failed items stay in the list. Alternative (checkbox marker) not taken.
4. **Images.** An image entry is an item (kind `image`) but the local model reads no pixels: it is triaged from file name, date and embed only and the owner decides the category (`needs_owner`). OCR or a vision model is a later spec. **Decided 2026-11-04 (owner delegated; defaults):** image entries are items of kind `image` decided by the owner (`needs_owner`; no model call, suggestion `none`); they are not skipped.
5. **No new write power.** The only new write path is "remove entries from the declared list file"; it is a vault tool of its own, limited to that file, and it is covered by the snapshot of spec 019.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The real INBOX list is triaged (Priority: P1)

**Independent Test**: Integration test with a fixture that is a scrubbed copy of the real INBOX (all four entry kinds, a comment, a duplicate, tracking parameters) and the fake model.

**Acceptance Scenarios**:

1. **Given** the fixture, **When** the triage runs, **Then** each entry is one item; header, comment and duplicate produce no extra items; the image entries have kind `image`
2. **Given** an entry "Title" with the URL on the next line, **Then** the item's resource is the URL and its title is "Title"
3. **Given** the same link with and without `utm_*`, **Then** both have the same item id
4. **Given** an item already in the triage file, **When** the triage runs again, **Then** it is not selected again

### User Story 2 - The vault structure is configuration (Priority: P1)

**Independent Test**: The pack's flows run on two fixtures with different layouts (default and the real one) and only the policy file differs.

**Acceptance Scenarios**:

1. **Given** a `layout` block, **When** any flow runs, **Then** it reads and writes only the configured places; no hard-coded folder remains (a test greps the package for the old constants)
2. **Given** `journal_pattern = "DailyJournals/{yyyy}/{yyyy-MM-dd}.md"`, **When** the improvement flow reads a day, **Then** it opens that path
3. **Given** a layout whose paths are not allowed by the vault policy, **When** the config is validated, **Then** validation fails and names the path and the tool

### User Story 3 - Processed entries leave the list safely (Priority: P2)

**Acceptance Scenarios**:

1. **Given** a processed item, **When** the flow finishes, **Then** its raw lines are gone from the list and present in the dated archive file
2. **Given** the owner added entries after the list was read, **When** entries are removed, **Then** the added lines are kept; entries are matched by their raw text, not by line number
3. **Given** a raw text that no longer matches, **Then** nothing is removed and the item is reported `not_found`

## Requirements *(mandatory)*

- **FR-001**: `VaultPolicy` has a `layout` block with the places of Clarification 2; defaults equal the current constants
- **FR-002**: Flows and steps read places from the layout (a `layout` value available to templates and steps), never from literals
- **FR-003**: `inbox.parse_list` parses the list file by the rules of Clarification 1, configurable: `bullet_markers`, `header_end` pattern, ignored comments, tracking-parameter list
- **FR-004**: Item identity = hash of the normalised resource (the URL without tracking parameters, the embed target, or the text); the item carries `kind`, `title`, `raw`
- **FR-005**: `inbox_remove_entries` (vault tool): input list of raw entries, output removed / not_found; writes the archive file; allowed only for the list file
- **FR-006**: `inbox.select_new` accepts both sources: one note per item (today) and the list file; a layout chooses which
- **FR-007**: `journal_pattern` placeholders `{yyyy}`, `{MM}`, `{dd}`, `{yyyy-MM-dd}`
- **FR-008**: Layout validation at publish: every path is relative, free of `..`, and allowed by the policy for the tool that uses it
- **FR-009**: Spec 019 derives its snapshot scope from the same layout

## Implementation notes (Completed 2026-11-04)

- `domain/layout.py` (`VaultLayout`, `InboxListSettings`), `domain/inbox_list.py`, `layout.json` (core default + pack file), `VaultPolicy.layout` with allowlist validation, vault tool `inbox_remove_entries`.
- `kind` / `needs_owner` are optional triage columns: written when a row needs them (a note-only triage file stays byte-identical).
- A list item has no note: `inbox.source_note` builds the text the flows work from; the flows' note read node is `fetch_note`, the merged result is still `read`.
- Flows name no folder; `categories.json` `para_path` stays category config.
- FR-009: `VaultLayout.snapshot_roots()` is the layout-derived scope; spec 019 itself is not part of this repository yet and uses it when added.

## Success Criteria

- **SC-001**: the real-INBOX fixture yields the expected item list exactly (golden test)
- **SC-002**: no literal of the old PARA names remains in `src/pkpipe` outside defaults and tests
- **SC-003**: remove-entries never changes a byte outside the removed entries (property test)
