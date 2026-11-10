---
spec_id: 031-inbox-in-progress
status: Completed
description: Data model of the INBOX-InProgress table, the outcome label and the new vault tools
updated: 2026-11-10
---

# Data Model: INBOX-InProgress

**Table `INBOX-InProgress.md`** (machine-owned, header text says so). Columns: `item` (12 hex id), `kind` (link, image, text_link, text, note), `resource`, `category`, `state` (triage state), `situation`, `cause` (code or short phrase), `next step`, `since` (first seen unfinished, kept across updates), `last run` (run id prefix of the last description), `history` (`[history](<ui_public_url>/items/<id>)`), `entry` (the exact INBOX entry in a code span; several entries of one resource are joined by the record separator), `facts` (hash of the facts the situation was written from).

**Outcome** (`in_progress.outcome(state, status, held, failed)` → `(code, label)`): for state `processed`: `held` ("held for you"), `done`, `ready`, `failed`, `unprocessed` ("not finished"), `processed` ("processed (result unknown)" when the processing list has no line); every other state is its own code and label. Rows of `GET /api/triage` carry `outcome` and `outcome_label`; `?outcome=` filters.

**Cause codes**: `held_classifier`, `quarantined`, `no_source_found`, `no_description`, `query_blocked`, `search_failed`, `no_source_url`, `page_not_fetched`, `model_unavailable`, `session_failed`, `unfinished`.

**Facts** (`facts_text` input of the session): `kind`, `resource` (≤300), `category`, `triage_state`, `processing_status`, `failure_reason` (fixed text), `cause`, `owner_retries`, `retry_limit`, `held_review` (`reading`, `threshold`, models with `top_score` and `label`), `events` (≤ `max_events` lines of failed steps, verdicts, source searches, quarantine, hold, earlier descriptions).

**Session output** `in_progress_situation`: `{situation ≤240, cause ≤80, next_step ≤200}`; fallback `{situation, cause = code, next_step = fixed per code}`.

**Vault tools**
- `read_in_progress()` → `{rows, count, file}` (cells unescaped; `entry` decoded).
- `read_processing_list()` → `{items: [{item_id, category, note, link, nb_id, status}], count}`.
- `inbox_in_progress(rows, archive_ids?, restore_ids?)` → `{upserted, moved, archived, restored, dropped, file}`. A row: `item_id`, `kind`, `resource`, `category`, `state`, `situation`, `cause`, `next_step`, `since?`, `last_run`, `facts`, `move_entry`.

**Plan output** (`in_progress.plan`): `enabled`, `has_work`, `describe` (items for the subflow: `id`, `row`, `facts`, `facts_text`), `carried` (rows kept or given the fixed sentence), `archive_ids`, `restore_ids`, `unfinished`, `count_describe`.

**Config**: `layout.inbox_in_progress_file` (default `<inbox_dir>/INBOX-InProgress.md`), `triage.in_progress.{enabled, max_rows_per_run, max_events}`.

**Item history** (spec 030): event kind `in_progress` with outcome `described`, `carried`, `fallback`, `closed`, `reopened` and the cause code.
