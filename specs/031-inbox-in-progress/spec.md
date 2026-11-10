---
spec_id: 031-inbox-in-progress
title: The triage shows how a processed item went, and unfinished items live in INBOX-InProgress.md
status: Completed
status_reason: Implemented 2026-11-10 on branch spec/031-inbox-in-progress (from dev at v0.1.10); suite, ruff green; awaiting the owner's approval to publish
description: The triage state `processed` only means the entry was worked on, but the page showed it like a success, and the INBOX list kept the entries of items that failed, were held for the owner or were quarantined with no explanation. The triage now shows the outcome of a processed row (done, ready, failed, held for you, not finished) with a Result filter. After every release run the entry of every item that was worked on but is not closed moves out of INBOX.md into one row of the machine-owned table INBOX-InProgress.md, where a tool-less local model session writes the situation, the cause and the next step from pkpipe's own records only. Closed items leave the table and are archived; reopened items return to the list.
relevant_when:
  - INBOX-InProgress
  - unfinished items
  - processed label
  - triage outcome
  - failed items
  - held items
  - quarantine
  - inbox list
related: [020-vault-layout-and-inbox-list, 023-web-triage-surface, 027-retry-failed-items, 029-classifier-evidence-and-review, 030-item-event-log]
updated: 2026-11-10
---

# Feature Specification: INBOX-InProgress and the triage outcome

**Feature Branch**: `spec/031-inbox-in-progress` (from `dev` at v0.1.10)
**Status**: Completed (2026-11-10)
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **Cause.** After the first days of production the owner saw items in `INBOX.md` that pkpipe had processed, and a triage page that listed failed items as `processed`. Nothing said which entries were closed and which were still a problem. Reading the vault showed that closed entries (`done`, `ready`) are archived as intended, and that what stays is exactly what is not finished (failed, held, quarantined, awaiting a category, not yet triaged images).
2. **Decision (owner, 2026-11-10): fix the label in the triage in every case.** `processed` is the state of the entry (worked on), not a result. The page shows the outcome derived from the processing list, the held review and the quarantine, with a *Result* filter. The stored states and the state machine are unchanged (API and files stay compatible; every row also carries the new `outcome` and `outcome_label`).
3. **Decision (owner, 2026-11-10): a table `INBOX-InProgress.md` in the INBOX folder.** What was processed but not closed leaves `INBOX.md` and becomes one row of this table, with context. **An LLM step reads everything the processes know about the item** and writes into the row what the situation is. Not in the table: items still awaiting a category, pending or suggested (nothing was processed yet) and items that are done or ready.
4. **Unfinished** means: triage state `processed` (or `released` for a queued retry) with a processing-list status `failed` or `unprocessed`; or held for the owner's classifier decision; or `quarantined`. **Closed** means `processed` with `done` or `ready`. Code decides this (`pkpipe.domain.in_progress.is_unfinished` / `is_closed`), never the model.
5. **What the model sees.** Only pkpipe's own records: triage state, processing status and fixed failure reason, the owner's retry count and limit, the classifier evidence of a hold (reading, model names, scores, labels), and the notable events of the item's history (spec 030: failed steps, verdicts, source search outcomes, quarantine, hold). Never page text, note text, image content or descriptions. The resource (an address, an embed target, the owner's own line) is data in the input and the instruction says to ignore any instruction in it. The session has no tools, uses the default (local, loopback) model profile and runs in a flow of data class `image`, which enforces loopback and content-free traces.
6. **Output.** Schema-validated JSON `{situation, cause, next_step}` with length limits (240 / 80 / 200); code cuts them again and escapes the table cell (pipes, newlines, markup). If the model fails or the answer is unusable a **deterministic fixed sentence** built from the cause code and the facts is written, so the table never blocks and the move still happens.
7. **Idempotent and cheap.** A hash of the facts is stored in the row (`facts`); an unchanged item keeps its row and is not described again. At most `triage.in_progress.max_rows_per_run` (40) rows are described per run; the rest get the fixed sentence with a blank hash and are asked again next run.
8. **Moving the entry, and every path that used it.** The entry text is kept exactly in the row's `entry` cell (a code span; escaped so it is restorable byte for byte; duplicates of one resource are joined). Order is crash safe: the row is written first, the entry leaves the list second. Findings, and what was done about each:
   - *Retry, accept-warning, release* (owner actions) and the next release run work from the **triage row** (resource, kind), not from the list entry; nothing needed changing there.
   - *Re-triage of a released quarantine item* looked the item up in the parsed list and silently skipped it when the entry was gone; it now falls back to its triage row (`_row_item`).
   - *`inbox.consume`* reported an entry that already moved as `not_found`; it now reads the table (`read_moved`) and reports it as `moved_ids`.
   - *List sync*: triage adds a row only for an id it does not know; an entry that moved (or comes back) never creates a duplicate row.
   - *Closing*: when an item becomes closed, its row leaves the table and its kept entry is appended to the dated archive file exactly like a consumed entry (`archive_ids`). A moved entry is **not** archived while the item is unfinished.
   - *Reopening*: a released quarantine item whose state is pending/suggested/awaiting_category again has its kept entry put back at the end of `INBOX.md` (unless the list already has it) and its row removed (`restore_ids`). This happens in the next release run's table step; until then the entry is in the row, not lost.
   - Items of kind `note` (a file in the INBOX folder, no list entry) get a row but nothing is moved.
9. **Vault tools and policy.** New tools `read_in_progress`, `read_processing_list` (read only, no text) and `inbox_in_progress` (the only writer: the table, the list and the dated archive). Allow-lists default from the layout (`inbox_dir`, and `archive_root` for the writer), so a policy file that does not list them needs no change. The table file is a managed file (never an item, not writable by the note tools, in the snapshot scope).
10. **Where in the process.** `process_released`, after the entry removal and before the run report: the new subflow `inbox_in_progress` (code `in_progress.plan`, `describe_in_progress` per changed item, `in_progress.collect`, tool `inbox_in_progress`). The 20:00 triage does not refresh the table: items change from unfinished to closed only in a release run, and owner actions between runs (retry, release) are picked up by the next one.
11. **Config.** `layout.inbox_in_progress_file` (default `<inbox_dir>/INBOX-InProgress.md`) and `triage.in_progress` (`enabled` default false in the model, true in the pack; `max_rows_per_run`; `max_events`).
12. **Out of scope (later spec):** writing learning notes and podcast files into the vault.

## Functional Requirements

- **FR-001**: pure `pkpipe.domain.in_progress`: `is_unfinished`, `is_closed`, `outcome`, `cause_code`, `facts_hash`, `facts_text`, `fallback_row`, `clean_answer`, `render_table`, `parse_table`, `encode_entry` / `decode_entry`.
- **FR-002**: `AppServices.list_triage` adds `outcome` / `outcome_label` and an `outcome` filter; UI pill, *Result* filter, `GET /api/triage?outcome=`; `bulk_confirm` passes the filter through.
- **FR-003**: layout key `inbox_in_progress_file` and property `in_progress`, part of the managed names, the snapshot scope, the tool paths and the overlap checks.
- **FR-004**: vault tools `read_in_progress`, `read_processing_list`, `inbox_in_progress` (+ MCP registration, policy tool names, `READ_ONLY`/`WRITING` tool sets).
- **FR-005**: code functions `in_progress.plan`, `in_progress.row`, `in_progress.fallback_row`, `in_progress.collect`; flows `inbox_in_progress` and `describe_in_progress`; the session `situation_writer` with `in_progress/situation.md` and schema `in_progress_situation`.
- **FR-006**: `process_released` runs the new subflow; `inbox.consume` and the re-triage tolerate a moved entry.
- **FR-007**: an `in_progress` event per row in the item history (spec 030).
- **FR-008**: eval dataset `datasets/sessions/describe_in_progress/situation.jsonl` (12 cases, one is an injected resource line), rubric `in_progress_situation.md`, recorded cassettes.

## Success Criteria

- **SC-001**: the triage never shows a failed or held item as plain `processed` (`test_triage_outcome.py`).
- **SC-002**: after a run with a failing item its entry is in `INBOX-InProgress.md` exactly and not in `INBOX.md`, nothing is archived (`test_inbox_list_flows.py`, `test_vault_in_progress.py`).
- **SC-003**: a retry, a re-run and a quarantine release work with the entry moved; no duplicate entry or row appears (`test_inbox_list_flows.py`).
- **SC-004**: an unchanged item is not described again; a failing model still writes the table (`test_inbox_list_flows.py`).
- **SC-005**: the model input holds no text of the page, note or image (`test_inbox_list_flows.py`, eval dataset).
