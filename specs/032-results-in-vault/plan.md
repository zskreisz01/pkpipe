---
spec_id: 032-results-in-vault
status: Completed
description: Plan for the results in the vault, NotebookLM only and auto-pass
updated: 2026-11-10
---

# Implementation Plan: Results in the vault

**Branch**: `spec/032-results-in-vault` | **Spec**: [spec.md](spec.md) | **Depends on**: 012, 020, 025, 029, 030, 031

## Summary

Pure `domain/results.py` renders the learning note, the image note and the index; `learning/failures.py` classifies a driver failure and reads `nlm usage`. The NotebookLM backend classifies, retries a transient error, checks the quota before it creates anything and can resume a parked notebook; the gateway keeps a quota-refused job as `waiting_quota`. The engine ships the `nlm` CLI driver. The vault server gets two tools (`write_results_index`, `import_audio`). The flows write the notes; `process_released` refreshes the indexes. The verdict gets an `auto_pass` mode of the existing spec 029 condition.

## Constitution Check

- **Code decides (V):** which notes are written, the paths, the quota decision and the auto-pass condition are code; no model is involved in this spec.
- **No untrusted text without a guard (IV, VIII):** the image note derives from untrusted content and passes the output guard; the learning note holds only our own facts; the index is built inside the vault server and returns no text; auto-pass is the same narrow condition as the hold and still needs the Reader and the output guard.
- **Ports and adapters (II):** the NotebookLM driver is an adapter behind `NotebookLMDriver`; vault work is in the vault adapter; steps use tools and `ctx.event`.
- **Private data (VI):** the audio copy reads only from owner-listed folders; nothing of the private vault enters the public repo.
- **BPMN (XI):** Process Learning Material, Process Other Resource, Secure Fetch and Process Inbox Items are updated (drawio, PNG, Mermaid, `docs/flows.md`); C4 labels of the learning service changed (Open Notebook deprecated); no new component.

## Risks

- `source_discovery.from_images` default flips to false: an overlay that relied on it (`enabled: true`) must set `from_images: true` to keep searching images. Stated in the changelog.
- The private overlay's own `nlm_driver` lacks `usage()`: it keeps working (no pre-check), but should switch to `pkpipe.learning.drivers.nlm_cli:make_driver`.
- A result index is written into folders the vault policy allows for `write_results_index` (defaults to the write folders of the layout); a policy that narrows it makes that folder's index fail softly (counted, never stops a run).
