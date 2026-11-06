---
spec_id: 022-owner-comment-and-new-categories
title: Owner comment column and proposals for new categories
status: Completed
status_reason: Implemented 2026-11-06 on branch spec/022-owner-comment-and-new-categories (T001-T007); suite, ruff and mypy green; awaiting the owner's approval to publish
description: The triage table gets an owner-owned `comment` column the pipeline never writes (superseded by spec 023 for who may read it: it is owner input and reaches the sessions as `owner_note`); an override naming a category that is not configured is normalised (case, spaces) or becomes a new-category proposal note instead of a dead end, and the versioned config is never edited by the pipeline.
relevant_when:
  - triage
  - comment
  - override
  - new category
  - proposal
  - categories
related: [007-triage-and-release, 010-category-sessions, 014-config-ui-and-app-mcp, 019-vault-snapshots, 020-vault-layout-and-inbox-list, 021-image-input]
updated: 2026-11-06
---

# Feature Specification: Owner comment column and proposals for new categories

**Feature Branch**: `spec/022-owner-comment-and-new-categories`
**Created**: 2026-11-06
**Status**: Completed
**Input**: Owner (translated): "An owner comment field is needed. I just filled the topic column with my own comments." and "Could it be that if I override with a category that does not exist, the pipeline creates it?"
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **`comment` is the owner's, `topic` is the pipeline's.** The triage table has two free text columns with different owners. `topic` (spec 021) is written by the pipeline for image rows (a one-line description, neutralised) and may be overwritten by it; for every other row the pipeline never writes it (confirmed in code: only `image.finish_batch` sets a topic). `comment` is written by nobody but the owner in Obsidian: the pipeline never writes, overwrites, trims or clears it, the vault tool refuses an update that names it, and it survives every row rewrite, the column migrations and the image-embed migration.
2. **`comment` is private owner text.** *(Superseded by [spec 023](../023-web-triage-surface/spec.md) clarification 2: the comment is owner input that reaches the sessions only as the declared input `owner_note`; `read_triage` and `list_triage` expose it to the owner channels and the code steps. The write rules below stay.)* It is plain text for the owner. It is not sent to any model, tool, notification, trace or log: the vault MCP tool `read_triage` leaves it out (so no flow state, no session with tools and no agent over the app MCP gets it), notification messages never quote a row, spans carry no row values. It is read only by the owner channels in-process when they rewrite the row.
3. **Migration of older files.** A triage table written without the `comment` column is widened on the next write (a run of the triage flow does one even when it has no new rows: `update_triage` with an empty list is the maintenance call and writes nothing when nothing changes). Widening keeps every row and every cell. One more rule, explicit, idempotent and run only when the header has no `comment` column: a row that is **not an image row** and has text in `topic` gets that text moved into `comment` (the pipeline never wrote it, so it is the owner's); `topic` of an image row stays (pipeline-owned). After the migration the header has `comment`, so the rule never runs again. A safe one-off migration is therefore possible by rule for rows of kind `note`, `link`, `text_link` and `text`; owner text in the `topic` of an **image** row cannot be told from a description and is not moved.
4. **An override that names no configured category is not an error and not an auto-edit.** Categories are versioned config (sessions, `para_path`, instructions); a pipeline that edited them would bypass the draft, validate, eval, publish path (constitution III and IX). So: (a) an obvious variant of a configured id or label (`Misc`, ` misc `, `Learning Material`) is **normalised deterministically in code** to the configured id; the owner's cell is not rewritten. (b) A genuinely unknown value that is a valid slug (`children`: letters, digits and hyphens, at most 40 characters after normalisation) makes the item a **new-category proposal**: it goes through the existing `misc` category's `evaluate` session with the requested name as input and the session output passes the output guard; the pipeline writes a proposal note below `layout.proposals_dir` and sets the row state `awaiting_category`. Nothing is filed into a PARA folder, nothing is deleted, the INBOX entry stays. (c) The notification and the backlog say "N item(s) propose a new category: ..." instead of "name a category that is no longer configured". (d) A value that is no slug at all is still the old dead end (`invalid`, back to the owner).
5. **How the owner then adds the category.** The proposal note holds a ready-to-paste `categories.json` entry (id, label, `subflow`, `para_path`, `triage_hint`, one generic `summarize` session) and the evaluation of the item. The owner adds the entry (and, for more than the generic session, instructions) to the overlay through the config UI draft, validates, evals and publishes. The row waits in `awaiting_category`; when the owner's override (unchanged) names a configured category, the next 06:00 run releases it like any item. The owner may also change the override to an existing category at any time.
6. **Injection safety.** The requested name is owner text, but it is handled as untrusted input anyway: it is reduced to a slug (`[a-z0-9-]`, 40 characters) before it reaches a prompt or a path; the item text and the model's evaluation are datamarked, pass the output guard, and are neutralised (`text_safety`) before they are written into the proposal note; the model never decides a category, a path or a state.

7. **Status: Completed (2026-11-06).** Gate 2: tasks done, suite green, docs and BPMN (drawio, PNG, Mermaid) updated. Decisions taken while implementing: the triage table always has the full header (a note-only file is no longer byte-identical after the first write: the owner needs the `comment` column), the UI override form stays strict (a configured category), `awaiting_category` rows are skipped by the release until the override names a configured category, the evening message notifies only for a **new** proposal, and a failed evaluation leaves the item as it was so the next 06:00 run retries.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The owner writes comments that stay (Priority: P1)

**Independent Test**: Table, vault tool and flow tests on temp vaults.

**Acceptance Scenarios**:

1. **Given** a row with a comment, **When** the pipeline updates the row (state, topic, embed migration, owner decision), **Then** the comment cell is unchanged, also with long text, `\\|`, wikilinks and stray blanks
2. **Given** an update that names `comment`, **Then** the vault tool refuses it (the owner's field)
3. **Given** a table without the `comment` column, **When** any write happens, **Then** the column is added, no row or cell is lost, and non-image rows with a `topic` get it as `comment` exactly once
4. **Given** rows with comments, **When** the notifications run, **Then** no comment text appears *(spec 023: `read_triage` and the owner channels expose it)*

### User Story 2 - An unknown override becomes a proposal (Priority: P1)

**Independent Test**: Domain tests for normalisation, flow tests with the fake model on a temp vault.

**Acceptance Scenarios**:

1. **Given** the override `Misc` (or ` misc `), **When** the release runs, **Then** the item is processed as `misc`
2. **Given** the override `children`, **When** the release runs, **Then** the `misc` evaluate session is called once with the name, a proposal note with a `categories.json` draft exists below `proposals_dir`, the row is `awaiting_category` and the item is not filed
3. **Given** the same row on the next runs, **Then** no second model call and no second note; **when** the owner changes the override to a configured category, **Then** the item is released
4. **Given** an injection in the item, **Then** the guard flags it and the item is quarantined, no note is written
5. **Given** open proposals, **Then** the 20:00 backlog says "N item(s) propose a new category: children, ..." and the 06:00 summary counts them

8. **Retry cap (2026-11-06, owner decision).** Clarification 7 let a failed evaluation retry at every 06:00 run without end. It is now capped at 3 failed evaluations per item and requested name, counted in the app DB (not in the vault: the triage table keeps its columns). After the cap the item is `pending`, listed as given up in the evening message (it notifies and counts as waiting for the owner) with the advice to change the override or add the category by hand. Changing the override to another name resets the count; a successful proposal clears it.

## Requirements *(mandatory)*

- **FR-001**: `TriageRow.comment` and the table column `comment` (last column, owner-owned); `update_triage` refuses a changed or new `comment`; every other write path keeps it byte for byte
- **FR-002**: Column migration on write: widen to the full header; move `topic` of non-image rows into `comment` only when the header had no `comment`; idempotent
- **FR-003**: *(superseded by spec 023)* `read_triage` of the vault server omitted `comment`; spec 023 exposes it to the owner channels and the code steps and passes it to models only as `owner_note`
- **FR-004**: `update_triage` with an empty list is the maintenance call (migration only, no write when unchanged); the triage flow calls it first
- **FR-005**: `triage.resolve_category`: deterministic normalisation (case, spaces, underscores, label) to a configured id, and the slug rule for a new-category request
- **FR-006**: Row state `awaiting_category` (`ItemState`), transitions and owner decision rules; excluded from the release unless the override now names a configured category
- **FR-007**: Flow `propose_category` (misc `evaluate` session with the requested name as input, output guard, proposal note below `layout.proposals_dir`, state row) called by `process_released` for the proposals of `triage.release`
- **FR-008**: Backlog and daily summary messages for proposals; `invalid` keeps its meaning for non-slug values
- **FR-009**: A failed proposal evaluation is counted per item and requested name (`ProposalAttemptsPort`, app DB); after `MAX_PROPOSAL_ATTEMPTS` (3) `triage.release` no longer proposes it: the item stays `pending`, the release and evening messages name it (`proposal_gave_up`), and a changed override starts a fresh count; a success clears it, a quarantine is no failed evaluation

## Success Criteria

- **SC-001**: A comment survives 100 random rewrites of a table (property test) and never appears in any notification or span
- **SC-002**: A migrated live-shaped file loses no row and no cell; running the migration twice changes nothing
- **SC-003**: The versioned config is never written by any step of the proposal flow (test: the overlay and the pack are byte-identical after a run)
