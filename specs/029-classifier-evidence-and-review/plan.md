---
spec_id: 029-classifier-evidence-and-review
status: Completed
description: Plan for classifier evidence and the review hold
updated: 2026-11-08
---

# Implementation Plan: Classifier evidence and review hold

**Branch**: `spec/029-classifier-evidence-and-review` | **Spec**: [spec.md](spec.md) | **Depends on**: 008, 009, 014, 027

## Summary

Pure `security/evidence.py`; `evaluate` gains `review_enabled` / `classifier_waived` and returns `evidence`; `secure_fetch` keeps per-model numbers and builds flag dicts with score and detail; `security.record_flag`, `quarantine_note`, `quarantine_front_matter/body` carry the evidence; `ClassifierReviewPort` (AppStore); step `security.hold_for_review` and flow edge in `other_resource`; `AppServices.accept_warning` with UI, HTTP, app MCP and CLI; triage rows show review and quarantine evidence.

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| II. Hexagonal core | Yes | New port `ClassifierReviewPort`; the steps know only the port |
| V. Security | Yes, with a stated trade-off | The decision stays code; no page text is stored or shown; the review needs four independent conditions and the owner's acceptance; see spec clarification 6 |
| XI. Documentation | Yes | BPMN drawio + PNG + Mermaid, docs, specs 008/009/014/027, CHANGELOG |
