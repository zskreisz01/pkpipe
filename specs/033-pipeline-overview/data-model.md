---
spec_id: 033-pipeline-overview
status: Completed
description: Data model of the pipeline overview, its buckets and the vault note
updated: 2026-11-10
---

# Data Model: The pipeline overview

**Buckets** (code, stage): `waiting_confirmation` (triaged), `awaiting_category` (triaged), `released` (released), `waiting_quota` (released), `done`, `ready`, `held`, `failed`, `unprocessed`, `result_unknown` (processed), `quarantined` (stopped). The bucket of a row follows its state: `suggested`/`pending` -> waiting_confirmation; `awaiting_category`; `quarantined`; `released` -> `waiting_quota` when its notebook job waits for a quota window, else `released`; `processed` -> the spec 031 outcome (done, ready, held, failed, not finished, or result unknown without a processing-list line).

**Overview** (`AppServices.pipeline_overview()`, `GET /api/pipeline`):
- `generated_at` (UTC), `ui_url`, `recent_days`
- `funnel`: `{captured, total, buckets: {code: n}, stages: [{stage, label, count, buckets: [{code, label, count}]}]}`; `total` equals the triage row count
- `runs`: last run per flow `{flow, run_id, status, trigger, started, ended, items, failed_steps}`
- `needs_you`: `[{item_id, bucket, stage, label, kind, category, resource, reason, situation, next_step, retries, retry_limit, at_retry_limit, auto_passed, action: {kind, label} | null, ...}]`, ordered held, quarantined, failed, unprocessed, awaiting_category, waiting_confirmation (only rows that need a category); `reason` is a stable code (`held_classifier`, `quarantined`, `retry_limit`, `needs_category`, `new_category_proposal`, a cause code of spec 031, ...)
- `waiting`: `{released: [item], waiting_quota: [item with waiting_until], generating: [{item_id, since}], next_runs: [{flow, cron, at}]}`
- `done_recent`: `[{item_id, category, note, link, podcast, finished_at}]` for done and ready items with an event in the last `recent_days`
- `where`: `{inbox_list, in_progress, processing_list, quarantine, pipeline_file, results: [{folder, index}]}`
- `items` (only with `with_items`): the record of every triage row by id (the item history reads `position`)

**Item position** (`item_history.where`): `{bucket, stage, label, reason, situation, next_step, waiting_until, auto_passed}`.

**Note** (`layout.pipeline_file`, starts with `# Pipeline overview`): header, a line starting with `Generated:` (ignored when comparing), the sections of FR-002 as plain Markdown tables, links to `<ui_url>/items/<id>` and `<ui_url>/pipeline#item-<id>`.

**Vault tool** `write_pipeline(text) -> {file, written}`; policy default allow-list: the INBOX folder.

**Port** `PipelineStatsPort`: `retry_counts()`, `held()`, `item_ids_with_event(kind)`, `last_event_times()`, `run_item_counts(run_ids)`.

**Config**: `layout.pipeline_file` (null: `<inbox_dir>/Pipeline.md`); `triage.pipeline`: `enabled` (model default false, pack true), `recent_days` (14, 1 to 90).
