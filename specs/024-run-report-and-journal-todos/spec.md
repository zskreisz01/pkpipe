---
spec_id: 024-run-report-and-journal-todos
title: Run report after every run, and todos in the daily journal
status: Completed
status_reason: Implemented 2026-11-07 on branch spec/024-run-report-and-journal-todos (from dev at v0.1.4, unreleased; T001-T008); suite, ruff and mypy green; awaiting the owner's approval to publish as 0.1.5
description: After every triage and release run the pipeline sends one report (what happened, items processed per category, what waits in the triage, evaluations / notebooks / podcasts created, and todos with counts) as the daily_summary notification; the todos are also written, one line each (date - todo - item count - link), into a marked block of the day's daily journal by a vault tool that is off unless the policy grants the journal folder; pkpipe.__version__ has one source.
relevant_when:
  - notification
  - run report
  - daily summary
  - todo
  - journal
  - discord
related: [006-notifications, 007-triage-and-release, 012-learning-gateway, 016-system-tool-candidates, 019-vault-snapshots, 020-vault-layout-and-inbox-list, 022-owner-comment-and-new-categories, 023-web-triage-surface]
updated: 2026-11-07
---

# Feature Specification: Run report after every run, and todos in the daily journal

**Feature Branch**: `spec/024-run-report-and-journal-todos` (from `dev` at v0.1.4)
**Created**: 2026-11-07
**Status**: Completed
**Input**: Owner request (translated): after every run a summary on Discord: what happened in the latest run, items processed per category, items awaiting approval in the triage; evaluations created and notebooks and podcasts created; todos with counts (items awaiting approval, notebook materials and podcasts still to process, tool evaluations to read). The todos also go into the day's Obsidian daily journal, in a separate section, in the form `date - todo - related item count - link`.
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **One report, one event.** Both the 20:00 `triage` and the 06:00 `process_released` flow end with the same subflow `run_report`. It sends the report as the existing `daily_summary` notification, so every deployment's routes, channels and templates keep working and one run is one message. The report replaces the earlier one-line messages (`triage_ready` after triage, the `daily_summary` line after release). `report.enabled: false` restores them exactly (the earlier "no mail when nothing is new" rule included); `enabled` is true by default because the owner wants a summary after *every* run.
2. **What the report may contain.** Counts, category ids, run ids and note links only, the rule of the triage message. Never an item's text, title, comment or URL. A note link is a wikilink made only from plain path characters; a name that is not plain (it comes from a page title or a model) is dropped, not escaped.
3. **Todos.** `approval`: rows the owner still has to decide (unconfirmed and not overridden, a decision on a category that is not configured, a proposed new category that is still not configured). `notebooks`: decided learning-material items that are not processed yet (states `suggested`, `pending`, `released`): the next 06:00 run generates their notebook and podcast; the gateway generates inside the item's run, so there is no "job running" count. `tool_reads`: notes of `report.tool_category` that carry `pkpipe_item` in the front matter and no `reviewed: true`; the owner marks a note read by adding that key. A todo with count zero is omitted. A new-category proposal waiting for the next run is not an approval todo (nothing to decide).
4. **Journal block.** A new vault tool `write_journal_todos(day, lines, heading)` sets the text between `<!-- pkpipe-todo:begin -->` and `<!-- pkpipe-todo:end -->` below the heading `report.journal_todos.heading` (default `## pkpipe todo`) in the file `layout.journal_path(day)`. It is idempotent per day (the block is replaced as a whole), changes no other byte, appends heading and block to a note that has none (below an identical existing heading), creates a missing note with front matter `type: journal`, refuses damaged markers, and writes only for today or yesterday. It never reads the journal out (the answer is path, line count, created).
5. **Off by default, granted on purpose.** The journal is the most sensitive note (design rules 5.7). The tool has no default allowlist: the vault policy must grant the journal folder in `allow.write_journal_todos`, and `report.journal_todos.enabled` (default false in the pack) must be on. A grant that does not cover `layout.journal_pattern` is a policy error found by `pkpipe validate` and at server start.
6. **No model, no snapshot.** The report and the lines are built by code; no model call, no trace content. The journal folder stays outside every vault snapshot (spec 019 never copies journals): `write_journal_todos` is classified as a writing tool (the flows that call it are writing flows) but its folder is excluded from the scope, because each run regenerates the block; restoring a run leaves it.
7. **A report never fails a run.** The calling flows ignore an error of the subflow; inside it a failing count or journal write only drops that part (the journal step falls through to the notification).
8. **One version source.** `pkpipe.__version__` reads the installed distribution's metadata; `pyproject.toml` is the only place the version is written. Tag 0.1.4 shipped with a stale hard-coded `__version__`; a test now compares both.
9. **Status: Completed (2026-11-07).** Gate 2: tasks done, suite green, docs and BPMN (drawio, PNG, Mermaid) updated, consistency checklist walked.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A report after every run (Priority: P1)

**Acceptance Scenarios**:

1. **Given** the 20:00 triage, **Then** the owner gets one message: new suggestions per category, images described and deferred, what waits for them, the todos with counts
2. **Given** the 06:00 release, **Then** the message lists items processed per category, quarantined and failed counts, created evaluations, notebooks and podcasts, and the todos
3. **Given** nothing new, **Then** a report is still sent (counts zero, "nothing open" when no todo)
4. **Given** `report.enabled: false`, **Then** the earlier messages are sent as before
5. **Given** a channel that fails, **Then** the run is still `done` and the other channels get the report

### User Story 2 - Todos with counts (Priority: P1)

1. **Given** 14 undecided rows, 2 decided learning items and 3 unread tool evaluations, **Then** the report has three todo lines with those counts and links (the triage UI or file, the processing list, the newest unread evaluation)
2. **Given** the owner adds `reviewed: true` to an evaluation, **Then** the next report counts one fewer
3. **Given** a count of zero, **Then** that todo is not shown

### User Story 3 - Todos in the daily journal (Priority: P2)

1. **Given** the option on and the folder granted, **When** a run reports, **Then** today's journal has one block `- date - todo - N items - link` per open todo, and a second run the same day replaces it
2. **Given** an existing journal, **Then** every byte outside the markers is unchanged, also after several runs
3. **Given** no grant, **Then** the write is refused, the report is still sent and the run is `done`
4. **Given** damaged markers, **Then** nothing is written and the journal is untouched

### User Story 4 - A trustworthy version (Priority: P3)

1. **Given** a release, **Then** `pkpipe --version` equals the version in `pyproject.toml` and a test fails when they differ

### Edge Cases

- A very long report (many categories, long notes): the detail lines shrink, the run line, the "waiting" line and the todo block stay; at most 1800 characters (Discord 2000).
- A journal pattern outside the PARA folders: the owner grants it for reading and for `write_journal_todos`.
- A run just after midnight: the vault allows today and yesterday.

## Requirements *(mandatory)*

- **FR-001**: subflow `run_report` and the code steps `report.settings` / `report.run_summary` (`steps/report_code.py`); both flows end with them; the notification is `daily_summary` with `title`, `awaiting`, `pending`, `todos`, `new`, `processed`, `quarantined`, `failed`, `evaluations`, `notebooks`, `podcasts`
- **FR-002**: `report.json` (`ReportConfig`: `enabled`, `tool_category`, `journal_todos.enabled|heading`), validated, core defaults, pack default
- **FR-003**: counts, category ids, run ids and plain note links only; wikilinks from plain characters; the UI link only from the configured `ui.public_url`
- **FR-004**: vault tools `unreviewed_notes(folder, category)` (read-only; count and newest path) and `write_journal_todos(day, lines, heading)` (rules in clarification 4); `domain/journal_todos.py` holds the write rule
- **FR-005**: `allow.write_journal_todos` has no default; `layout_errors` checks the journal pattern against a grant
- **FR-006**: the journal folder stays outside the snapshot scope; both tools are classified (writing / read-only)
- **FR-007**: the report text is at most 1800 characters and keeps header, waiting line and todo block when cut
- **FR-008**: a failing report never fails the run
- **FR-009**: `pkpipe.__version__` from distribution metadata; test against `pyproject.toml`
- **FR-010**: Templates for Discord, Teams and Apprise show the report's `title`; docs, user guide, security, BPMN (drawio, PNG, Mermaid)

## Success Criteria

- **SC-001**: after a triage run and a release run on a synthetic vault the owner has two messages with the counts above and `payload` numbers that match the vault (tests)
- **SC-002**: a journal write never changes a byte outside the markers (property tests over varied notes), never returns journal text, and is refused without a grant
- **SC-003**: no item text, comment or INBOX URL appears in any message or journal line of an end-to-end run
- **SC-004**: with `report.enabled: false` the earlier messages and the no-nagging rule hold (existing tests keep their expectations under the flag)
