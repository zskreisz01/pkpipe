---
spec_id: 029-classifier-evidence-and-review
title: Classifier evidence in every flag, and a review hold for injection-detector-only warnings
status: Completed
status_reason: Implemented 2026-11-08 on branch spec/029-classifier-evidence-and-review (from dev at v0.1.7); suite, ruff green; awaiting the owner's approval to publish
description: A quarantined item was only an id with the reason code classifier_threshold. The owner could not find the page, see which model fired or tell a real finding from the DeBERTa injection detector's known false positives on markup-heavy pages. Every classifier flag now carries the scores of both models, the safety label, the chunks over the threshold and fixed sentences that explain them; the quarantine note, the log, the alert and the triage page show the page address. When only the injection detector fires and the safety model says Safe (heuristics and Reader clean), the item is held for the owner instead of quarantined; the owner accepts the warning per item.
relevant_when:
  - quarantine
  - classifier
  - DeBERTa
  - Qwen3Guard
  - false positive
  - held for review
  - accept warning
  - secure fetch verdict
related: [008-secure-fetch, 009-output-guard-and-quarantine, 014-config-ui-and-app-mcp, 027-retry-failed-items, 028-cache-any-version-and-protected-domains]
updated: 2026-11-08
---

# Feature Specification: Classifier evidence in every flag, and a review hold

**Feature Branch**: `spec/029-classifier-evidence-and-review` (from `dev` at v0.1.7)
**Status**: Completed (2026-11-08)
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **Cause.** In the first production run 5 GitHub and Hugging Face pages were quarantined with `classifier_threshold`. Re-scoring two of their READMEs: the DeBERTa injection detector gave 0.99 on the first chunks (centred HTML, image tags, badges), Qwen3Guard-Gen said `Safe` (0.004) on every chunk. The owner could find neither the page (the note was only the item id) nor the reason (no score was recorded: the log said `null`).
2. **Evidence, never text.** `pkpipe.security.evidence` builds, from the scores alone, per model: kind (`injection` or `safety`), top score, chunk count, chunks over the threshold (at most 20 listed), label and categories (safety). A *reading* summarises both: `injection_detector_only`, `both_over_threshold`, `safety_model_only`, `none_over_threshold`, `single_model`. `interpret()` turns it into fixed sentences (what each model measures, where it fired, what the combination means). No page text enters it.
3. **Where it shows.** The secure-fetch verdict has `evidence`; each classifier flag has `score` and `detail`. `security.record_flag` writes both to `malicious_urls.jsonl` (`score`, `detail`) and hands `url` and the flags to `quarantine_note`, whose front matter gets `source_url`, `security_flag.scores` and `security_flag.classifier`, and whose body gets the address (inline code, inert) and a *Why it was flagged* table with the sentences. The alert says `Evidence: classifier 0.99 (deberta... 0.99, Qwen3Guard... 0.00 Safe)`. The triage page shows the address, flags, scores and sentences on every quarantined row (`AppServices.quarantine_summary`, from the front matter, never the body).
4. **Review hold.** With `security.classifier.review.enabled` (pack default true) the verdict is `review` instead of `flagged` only when **all** hold: the only flag is `classifier` with `classifier_threshold`; the reading is `injection_detector_only` (a safety model exists and says `Safe` below the threshold); the heuristics are clean; the Reader ran and raised nothing (no `injection_suspected`, no canary leak, valid output). Any other combination keeps the quarantine. A held item is not quarantined, its domain is not deny-listed, nothing is written to `malicious_urls.jsonl`. `other_resource` runs `security.hold_for_review`: the processing list line is `failed` with the reason *held for your decision: only the injection detector fired (classifier warning)*, and the address and evidence are kept in the app DB table `classifier_reviews` (`ClassifierReviewPort`).
5. **The owner accepts, per item.** `AppServices.accept_warning` (UI button *Accept warning and process*, `POST /api/triage/{id}/accept-warning`, app MCP `accept_classifier_warning`, CLI `pkpipe retry --accept-warning ITEM`) sends the item through the next release run (a spec 027 retry, bounded by `triage.retry_max`) and records the waiver. The verdict then returns `clean` for the same finding (`scores.classifier_waived`). The waiver covers only this finding: any other flag (heuristic, Reader, canary, URL filter, a second finding) still flags the page, and the output guard checks everything the sessions write. Held items are excluded from *Retry all failed*: a plain retry would only hold them again.
6. **Security trade-off.** An attack that only the DeBERTa detector recognises is no longer quarantined at once; it waits for the owner. It still does not reach a session: the item is stopped before the Reader's extract is used. The review rule never applies to the output guard (spec 009), to a single-model classifier, or when the classifier is unavailable. The security eval treats `review` as held (not clean), so its detection metric is unchanged; its classifier cassettes ignore `classifier.review` in their identity (it changes what a verdict does, not what the models score).

## Functional Requirements

- **FR-001**: `security/evidence.py`: `classifier_evidence`, `interpret`, `review_applies`.
- **FR-002**: `evaluate(...)` returns `Verdict.evidence`; status `review` and `Verdict.review` under the conditions of clarification 4 when `review_enabled`; `classifier_waived` turns the same finding into `clean`.
- **FR-003**: `scan_page` keeps per-model chunk scores, label and categories; classifier flags carry `score` and `detail`.
- **FR-004**: `MaliciousUrlEntry.detail`; `quarantine_front_matter` / `quarantine_body` / `quarantine_note(url=...)`; the alert and its payload carry the evidence line.
- **FR-005**: `ClassifierReviewPort` (`hold`, `review_of`, `held`, `waive`, `waived`); table `classifier_reviews`; step `security.hold_for_review`; flow edge `secure_fetch -> hold_review` in `other_resource`.
- **FR-006**: `AppServices.accept_warning`, `review_summary`, `quarantine_summary`; rows `review` / `quarantine`; `failed_summary.held`; UI, HTTP, app MCP and CLI actions; held items skipped by `retry_failed`.
- **FR-007**: config `security.classifier.review.enabled` (model default false, pack true).
- **FR-008**: BPMN pages *Secure Fetch* (outcome *Held for the owner*) and *Process Other Resource* (*Hold for the owner (classifier warning only)*), PNG and Mermaid.

## Success Criteria

- **SC-001**: a quarantined item shows its address, scores and reading on the triage page and in its note (`test_classifier_review`).
- **SC-002**: only the exact conditions of clarification 4 hold an item; every other combination quarantines (`test_classifier_evidence`, `test_source_discovery_flows`).
- **SC-003**: an accepted warning lets the same page through and nothing else (`test_classifier_evidence`, `test_source_discovery_flows`).

> **Note (spec 032):** `security.classifier.review.mode: auto_pass` lets exactly the case held here (injection detector alone, safety model Safe, heuristics clean, Reader ran and not suspicious) pass without waiting for the owner, logged as an `auto_passed` event with this evidence. The default stays `hold`; every other finding still quarantines.
