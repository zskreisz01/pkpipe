---
spec_id: 009-output-guard-and-quarantine
title: Output guard and quarantine
status: Completed
description: Checks every output derived from external content before it is written (run canary, heuristics, Qwen3Guard + ProtectAI DeBERTa) and handles security flags: malicious URL log, deny-list, inert quarantine, alert, owner release with eval candidate.
relevant_when:
  - output guard
  - Qwen3Guard
  - quarantine
  - malicious_urls
  - deny-list
  - security flag
  - output_guard
  - canary
  - release quarantine
related: [008-secure-fetch, 013-evaluation-framework, 014-config-ui-and-app-mcp]
updated: 2026-11-01
---

# Feature Specification: Output guard and quarantine

**Feature Branch**: `spec/009-output-guard-and-quarantine`
**Created**: 2026-10-31
**Status**: Completed
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution
**Companions**: [research.md](research.md) · [data-model.md](data-model.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Nothing harmful lands in the vault (Priority: P1)

A synthesized note with harmful content or embedded instructions is stopped.

**Independent Test**: Security eval on outputs.

**Acceptance Scenarios**:

1. **Given** an output with an injected instruction, **When** guarded, **Then** it is flagged and not written to PARA

### User Story 2 - Flagged items are handled consistently (Priority: P1)

Every flag logs the URL, deny-lists the domain (except protected domains, spec 028), quarantines inertly and alerts.

**Independent Test**: Flow test of `handle_security_flag`.

**Acceptance Scenarios**:

1. **Given** a flag, **When** handled, **Then** a row is appended to `malicious_urls.jsonl` and the item state is quarantined
2. **Given** quarantined content, **When** stored, **Then** it is inside a code block with `security_flag` front matter

### User Story 3 - Owner releases false positives (Priority: P2)

The owner releases an item; it re-enters processing and becomes an eval entry.

**Independent Test**: UI/app-MCP test.

**Acceptance Scenarios**:

1. **Given** a release, **When** confirmed, **Then** the item is re-queued and added to the security dataset candidates

### Edge Cases

- Same domain flagged repeatedly → single deny-list entry, counter incremented. Protected domains (`security.url_filter.protected_domains`, spec 028) are never added.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Output guard runs on every session output from external content before vault writes.
- **FR-002**: Flags: `malicious_content`, `injection_or_jailbreak`, `canary_leak`.
- **FR-003**: Security flag is an error end event per item; other items continue.

### Key Entities

- **SecurityFlag**
- **MaliciousUrlEntry**
- **QuarantineNote**

## Success Criteria *(mandatory)*

- **SC-001**: 0 flagged outputs written to PARA in tests.
- **SC-002**: Every flag produces log row, deny-list entry (not for protected domains, spec 028), quarantine note and alert.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

### Session 2026-11-01 (implementation)

- Q: Which classifiers? → A: Owner decision: no Meta models. `security.classifier` = Qwen3Guard-Gen-0.6B (harm categories, Jailbreak) + ProtectAI deberta-v3-base-prompt-injection-v2 (injection), combined by maximum score, local files only (decision record `docs/decisions/0001-injection-classifier.md`). The output guard reads both: a harm category under `output_guard.block_on` is `malicious_content`, an injection score or a Jailbreak category is `injection_or_jailbreak`.
- Q: Which outputs are guarded? → A: Every session marked `canary: true` (a session over external content). Validation requires an `output_guard` on every path after it. Public pack: the triage session (before the triage file) and the other-resource summary (before the PARA note). The learning branch writes no derived text, so it has none; its blocked URLs go to the same handler.
- Q: How does the guard know the canary? → A: One canary per run (`security.canary.rotate`, derived from the run id so a resumed run keeps it); the session and the guard compute the same value. The check is a deterministic string match on every field (also the hex part). Regression for the canary leak of the 008 review: flow case `triage-canary-leak-quarantined`, eval gate `canary_leaks_unguarded = 0`.
- Q: Is the 008 canary leak real? → A: Yes. 10 live runs of the triage injection set leaked 4/150 (all `inj-11`, an "auditing" request), every leak flagged by the output guard; one instruction line against writing the reference code brought it to 0/150 (docs/evaluation.md).
- Q: What else does the guard check? → A: Active markup (script, iframe, `javascript:`, event handlers) and the secure-fetch heuristics, in code. Without a classifier it fails closed (`classifier.required`).
- Q: Where do `malicious_urls.jsonl` and the deny-list live? → A: In the overlay (`<overlay>/security/`, the deny-list file of `url_filter.deny_list`, started from the pack's list) so the URL filter of every process applies it; without an overlay in the data directory, merged in-process. Repeats increment a `# pkpipe: flagged N` counter on the one entry. Evidence holds reason codes, never page text.
- Q: What is "inert"? → A: The note moves to `0-INBOX/_quarantine/<item id>.md`: verbatim inside a fenced code block (fence longer than any backtick run) with `security_flag` front matter (flag codes only). Only the vault tool `quarantine_note` writes there.
- Q: Flag at triage? → A: The row is written `quarantined` with suggested category `none`; after a release it is triaged again.
- Q: What does a release do? → A: Owner only (UI, app MCP owner token, audited): restore the note exactly, row back to `pending` (re-queued), append an eval case candidate (`source: release`, `label: benign`) to `<overlay>/evals/candidates/security.jsonl`. The note text is never returned to the caller. The deny-list entry stays (Out of Scope).

## Out of Scope

- Automatic unblocking of domains.
