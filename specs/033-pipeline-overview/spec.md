---
spec_id: 033-pipeline-overview
title: One place for the whole system - the pipeline overview (page, API, MCP, CLI and a vault note)
status: Completed
status_reason: Implemented 2026-11-10 on branch spec/033-pipeline-overview (from dev at v0.1.12); suite, ruff green; awaiting the owner's approval to publish
description: The process is elaborate but the owner has little sense of control. What happened, where each item stands and where to look to work on it were spread over the triage page, the runs, the item history, INBOX-InProgress.md, the processing list, the quarantine, the learning notes and the result indexes. One overview, built from pkpipe's own records without a model, now answers four questions in one place - where is everything (a funnel in which every triage row is in exactly one bucket), what needs the owner (cards with the situation, the next step and the audited action), what waits (next run, quota, notebooks being generated, schedules) and what is done (with the link to its result). It is the web page /pipeline (phone friendly, the top of the Overview page), GET /api/pipeline, the owner MCP tool pipeline_overview, the CLI pkpipe pipeline and the machine-owned vault note Pipeline.md that every triage and release run refreshes.
relevant_when:
  - pipeline overview
  - Pipeline.md
  - /pipeline
  - funnel
  - needs you
  - where is it now
  - where do I look
  - control
  - pipeline_file
  - write_pipeline
  - triage.pipeline
related: [014-config-ui-and-app-mcp, 020-vault-layout-and-inbox-list, 023-web-triage-surface, 024-run-report-and-journal-todos, 029-classifier-evidence-and-review, 030-item-event-log, 031-inbox-in-progress, 032-results-in-vault]
updated: 2026-11-10
---

# Feature Specification: The pipeline overview

**Feature Branch**: `spec/033-pipeline-overview` (from `dev` at v0.1.12)
**Status**: Completed (2026-11-10)
**Companions**: [data-model.md](data-model.md)

## Clarifications

- The owner: "the process is elaborate, but my sense of control is low: I do not see in one place what happened, where each item stands and where to look to process it - on the phone either."
- **Decision: one aggregation, many surfaces.** `AppServices.pipeline_overview()` (and the same assembler in the flow step) builds one dict from pkpipe's own records; the page, the API, the MCP tool, the CLI and the note only render it. They cannot disagree.
- **Decision: no model.** Everything is counted and ordered by code. The only free sentences are the *situation* texts of the unfinished-items table (spec 031), which are already schema-checked and escaped; the overview shows them, it never asks again.
- **Decision: no new decision logic.** The buttons call the audited services that exist (accept warning, release quarantine, retry, confirm). The overview does not change a state itself.
- **Decision: the vault note is generated and idempotent.** `Pipeline.md` is rewritten after every triage and release run, but only when its content changed (the `Generated:` line is ignored), so the vault's git history stays quiet. A refresh never fails a run.

## User Scenarios

1. **Where is everything?** The funnel counts every item of the triage table once: *triaged* (waiting for your confirmation, awaiting a category), *released* (next run, waiting for NotebookLM quota), *processed* (done, ready, held, failed, not finished, result unknown) and *stopped* (quarantined). A line before it counts the entries captured in the INBOX list that are not triaged yet. The buckets add up to the triage table.
2. **What needs me?** *Needs you* lists held warnings, quarantined items, failed or unfinished items, items at the retry limit, categories to decide and rows that need a category - in that order - each with the reason code, the situation, the next step, a link to the item history and the button that does the audited action.
3. **What is waiting?** Items released for the next run, notebooks waiting for a quota window (with the time it opens), notebooks and podcasts being generated, and the next fire time of every schedule.
4. **What is done?** Items closed in the last 14 days with the link to the result (vault note, notebook link, podcast status).
5. **Where is this item now?** The item history starts with a *where it is now* line: bucket, stage, situation, next step.
6. **On the phone.** The page is single column under 600 px with large tap targets and no horizontal page scroll; the same overview is the note `Pipeline.md` in Obsidian.

## Requirements

- **FR-001**: `domain/pipeline.py`: `bucket` (one of eleven buckets per triage row), `build_overview`, `assemble` (reads the helper tables through ports; a failing helper leaves its part empty), `render_markdown`, `normalize`/`content_hash`, `position`.
- **FR-002**: Sections of the note, in order: header with the generated time, funnel, last runs (status, started, items touched, failed steps), Needs you, In progress / waiting, Done recently, Where things live. Long lists are cut at 40 rows with a pointer to the page.
- **FR-003**: `AppServices.pipeline_overview(with_items=False)`; `GET /api/pipeline`; UI `/pipeline`, `/pipeline/body`, `POST /ui/pipeline/{item}/{accept|release|retry|confirm}`; the Overview page `/` shows the counters and the first five cards and links to the page; the navigation starts with Pipeline; `item_history` carries `where`.
- **FR-004**: owner MCP tool `pipeline_overview` (the agent token cannot call it); CLI `pkpipe pipeline [--json]`.
- **FR-005**: vault tool `write_pipeline(text)` writes `layout.pipeline_file` (default `<inbox_dir>/Pipeline.md`) only when the text changed, refuses text without the pipeline heading, over 200 kB, and an existing file that is not a pipeline note; policy tool name `write_pipeline` with the layout allow-list default (the INBOX folder); the note is a managed file and part of the vault snapshot scope.
- **FR-006**: flow `pipeline_overview` (subflow, data class `image`): read triage, processing list, unfinished-items table and the INBOX list, `pipeline.build`, `write_pipeline`; called at the end of `triage` and `process_released` before the run report; any failure ends it quietly. Config `triage.pipeline.enabled` (model default false, pack true) and `recent_days`.
- **FR-007**: `PipelineStatsPort` (retry counts, held warnings, items with an event, last event times, per-run item counts) implemented by `AppStore`; `learning.jobs_view.read_jobs` (read only); `scheduler.next_fire_times`.
- **FR-008**: content: ids, codes, URLs and file names as inline code, numbers, times, fixed sentences and the stored situation sentences; every cell escaped; no page, note or image text.

## Success Criteria

- **SC-001**: every triage row is in exactly one bucket and the funnel total equals the triage row count (`test_pipeline.py`, `test_pipeline_overview.py`).
- **SC-002**: two refreshes that differ only in the generated line produce the same note and the second writes nothing (`test_vault_pipeline.py`, `test_pipeline_overview_flow.py`).
- **SC-003**: a failing vault read or write never fails the triage run (`test_pipeline_overview_flow.py`).
- **SC-004**: the card actions use the audited services and refresh the page; the rules' refusals are shown, not raised (`test_pipeline_overview.py`).
- **SC-005**: the page works on a phone (viewport meta, single column, 44 px targets) and the MCP tool is owner only.

## Out of scope

- A new decision logic or a state change by the overview.
- A history chart or statistics over time.
- Writing learning notes or podcasts into the vault (spec 032).
