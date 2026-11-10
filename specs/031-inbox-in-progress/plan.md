---
spec_id: 031-inbox-in-progress
status: Completed
description: Plan for the triage outcome label and the INBOX-InProgress table
updated: 2026-11-10
---

# Implementation Plan: INBOX-InProgress and the triage outcome

**Branch**: `spec/031-inbox-in-progress` | **Spec**: [spec.md](spec.md) | **Depends on**: 020, 023, 027, 029, 030

## Summary

Pure `domain/in_progress.py` (classification, labels, table, hash, fallback). `AppServices.list_triage` derives `outcome` from the processing list (new vault read tool) and adds a filter. `process_released` gets one more node: the subflow `inbox_in_progress`. It reads the triage table, the processing list and the table, plans in code, asks a tool-less local session about each new or changed item (subflow `describe_in_progress`, fallback sentence on error), and writes the rows with the single vault writer `inbox_in_progress`, which also moves, archives and restores the kept entries.

## Constitution Check

- **Code decides, the model describes (V):** which items are unfinished/closed/reopened, the cause code, the move, the archive and the restore are code; the model writes three short strings.
- **No untrusted text to a model that writes (IV, VIII):** the input has only our own records; the output is schema-validated, cut and cell-escaped; the flow is of data class `image` (loopback only, content-free traces).
- **Ports and adapters (II):** steps use `ctx.ports.extra` ports (`classifier_reviews`, `item_retries`, `item_events`) and vault tools; the file work is in the vault adapter.
- **Eval (IX):** the new LLM step has a dataset (12 cases), a rubric and cassettes.
- **BPMN (XI):** `Process Inbox Items` has the task *Move unfinished entries to INBOX-InProgress*; PNG, Mermaid and `docs/flows.md` follow. C4 does not change (no new container or component; the vault server gained tools, the table is a vault file).

## Decisions

1. **One writer.** `inbox_in_progress(rows, archive_ids, restore_ids)` is the only tool that touches the table, so the moves are ordered in one place: closed entries are archived before the row is dropped; reopened entries are restored before the row is dropped; moved entries are in the row before they leave the list.
2. **The entry cell is a code span** with `\\`, newline, `|`, backtick and the record separator escaped: it does not render an image embed and decodes byte for byte.
3. **Facts hash over facts that matter.** Timestamps are not in the facts; only failed steps and notable events are, so routine progress does not trigger a new description.
4. **Triage does not refresh the table.** Only a release run changes a closed/unfinished status; owner actions are picked up by the next run.
5. **Consume reads the table itself** (`ctx.resolve("read_moved", required=False)`), so a missing policy permission for the new read tool cannot break the entry removal.

## Risks

- A deployment whose vault policy lists `allow` for the new tools with a narrower folder list would refuse them; the step then fails (the run report says so) and nothing else changes.
- The table is only as fresh as the last release run; the UI shows the live outcome.
