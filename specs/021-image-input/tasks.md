---
spec_id: 021-image-input
status: Completed
description: Tasks for image input
updated: 2026-11-05
---

# Tasks: Image input and the image description session

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 3 (P1, foundation)

- [x] T001 [US3] `read_image` in the vault policy and server; refusal codes; traversal, symlink, type and size tests
- [x] T002 [US3] `attachments_dir` read-only in the layout validation and snapshot scope (FR-010)

## Phase: User Story 1 (P1)

- [x] T003 [US1] `ImageInput`, `ModelPort` extension, fake vision model
- [x] T004 [US1] OpenAI adapter: `image_url` parts, `vision` and `max_tokens` profile fields, stable error
- [x] T005 [US1] `image.prepare` (EXIF removal, scaling, hash, pixel limit) and the cache table
- [x] T006 [US1] Session `describe_image`: schema, prompt, step defaults; retry then `needs_owner`
- [x] T007 [US1] Triage image branch (images after the text items), `topic` column
- [x] T007a [US1] `image.budget`: estimate, per-image update, stop rule, timing table, report; fake clock tests and the property test (SC-004)
- [x] T007b [US1] Per-image timeout (`per_image_timeout_s`) and the notification text for deferred images

## Phase: User Story 2 (P1)

- [x] T008 [US2] Data class `image`: loopback only, no egress, no trace content (validate and compile)
- [x] T009 [US2] Output guard and canary on `visible_text` and `topic`; URL policy for `urls`
- [x] T010 [US2] Leak test: traces, logs, notifications (SC-003)

## Phase: User Story 4 (P2)

- [x] T011 [US4] Synthetic image generator and dataset; suite `images`: code checks against the known truth, a text judge (different family) only for `topic`; 20-item calibration first (the judge is not gated: `judge_mean` stays out of the gate until the owner has scored 20 topics, see the implementation notes in spec.md)
- [x] T011a [US4] Phase silo: the text phase and the image phase each use one profile and the run switches once (SC-005); unload hint for the text model between the phases; eval runner supports record-then-judge passes so the judge never shares memory with the subject (check; else small task)
- [x] T012 [US4] Live probe test (marked `live`): the SC-002 repeat on synthetic images

## Phase: Polish

- [x] T013 [P] Docs (configuration, operations: model memory and throughput, security), CHANGELOG
- [x] T014 [P] BPMN: drawio, PNG, Mermaid together; the "Describe images" sub-process with the time-budget loop and the per-image timer (see the plan) (diagram drawn 2026-11-05)
- [x] T015 Release notes: spec 020 sentences on images updated (done in this draft)
