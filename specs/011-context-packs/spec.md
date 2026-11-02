---
spec_id: 011-context-packs
title: Context packs and weekly refresh
status: Completed
description: Builds compact personal and enterprise context packs weekly from project notes (enterprise anonymized, local model only), keeps manual edits, and provides the public system context pack.
relevant_when:
  - context pack
  - personal context
  - enterprise context
  - system context
  - context_refresh
  - query guard
related: [010-category-sessions, 016-system-tool-candidates]
updated: 2026-11-02
---

# Feature Specification: Context packs and weekly refresh

**Feature Branch**: `spec/011-context-packs`
**Created**: 2026-10-31
**Status**: Completed
**Companions**: [data-model.md](data-model.md)
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fit sessions know my projects (Priority: P1)

Tool fit sessions receive a compact, current description of projects.

**Independent Test**: Flow test with fixture notes.

**Acceptance Scenarios**:

1. **Given** project notes, **When** the refresh runs, **Then** `personal.md` and `enterprise.md` are written within token limits

### User Story 2 - Enterprise data stays local (Priority: P1)

Client names never leave the machine.

**Independent Test**: Context-leak eval.

**Acceptance Scenarios**:

1. **Given** the enterprise pack, **When** any web query is built, **Then** the query guard removes or blocks listed names

### Edge Cases

- No project notes → empty pack with a warning.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Refresh flow runs weekly (Sunday 19:00) and on demand.
- **FR-002**: Manual edits are preserved; the run proposes a diff.
- **FR-003**: `system.md` is maintained with the design and is public.

### Key Entities

- **ContextPack**

## Success Criteria *(mandatory)*

- **SC-001**: 0 leaks on `context_leak.jsonl`.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

### Session 2026-11-02 (implementation)

- Q: How do the two branches run in parallel and join? → A: `context_refresh` lists the scopes (`context.scopes`, configurable in the node's args: id, kind, folder, pack, budgets) and fans out over them with `for_each` + `max_parallel` 2 into the subflow `context_pack` (`on_item_error: continue`), then one summary and one notification. Compiled flows have no real join node, a fan-out is the join.
- Q: What does "anonymized, local model only, enforced in code" mean? → A: Three layers. (1) The flows declare the data class `context`; validation and every run's compile (also for the sessions of subflows) refuse a model profile, fallback or environment value that is not loopback, an `egress` tool, a session with tools and a non-context subflow, and a flow that calls a `context.*` function without the class fails validation (an overlay cannot drop the class), and each function checks the class again. (2) The session only summarizes: code collects the names (front matter, labelled lines, titles, names the session reports that occur in the notes, the owner's blocked terms), scrubs them from the generated text, verifies with the query guard's own matching and drops a line that still matches. (3) A summary that trips the injection heuristics is not written. A name only in the prose that the session neither hides nor reports cannot be known to code; the leak eval measures it (0 leaks in 130 live cases) and the owner reviews each diff.
- Q: How are manual edits kept? → A: The pack starts with a hash of its generated part. Unchanged hash: the refresh replaces the generated part and keeps everything below `<!-- pkpipe:manual -->`. Different hash or a hand-written file: the pack is left alone, the new version goes to `contexts/<pack>.md.proposed` and the diff to the vault note `2-Areas/Context-Review/<pack>.md`; the owner merges or `mv`s it. The decision is made when writing, against the file as it is then.
- Q: Where are packs written? → A: The private overlay (`<overlay>/contexts/`, through the new `ContextStorePort` limited to `contexts/` and `security/`); the public pack is never written and a run without an overlay fails its scopes naming `PKPIPE_OVERLAY_DIR`. `system.md` (FR-003) ships in the public pack, describes the components and stack of the design, and is a reserved name no scope may generate.
- Q: The query guard? → A: The guard of spec 010 is unchanged (it blocks, whole words, case/accent/punctuation insensitive). The refresh feeds it: the pack ships `security.query_guard.terms_file = security/query_guard_terms.txt`, and each refresh appends the enterprise names in every form (full name, short form, last name) to the overlay's copy before the pack is written, never removing a line.
- Q: Empty folder? → A: A missing or empty folder renders the empty pack with a warning (as the spec says); a vault server that is down fails the scope instead of blanking a good pack.
- Q: Which 010 sessions use the packs? → A: `fit_personal` and `fit_enterprise` already name `personal` and `enterprise`; the sessions now see the pack without the bookkeeping comment lines, and a refreshed overlay removes their "context missing" skip. The system relevance session of spec 016 will use `system`.
- Q: Results? → A: `context_leak` (26 synthetic cases, qwen3.5-9b-mlx, five live runs 2026-11-02): leaks 0, raw leaks of the session 4 to 15 % (all caught), retained 0.85 to 0.96; sessions suite 85 items (12 new: personal 5.0, enterprise 4.33 judge mean), schema 1.0, checks 85/85, judge mean 4.906. See [Evaluation](../../docs/evaluation.md#measured-with-the-reference-model).

## Out of Scope

- Embedding-based retrieval over the vault.
