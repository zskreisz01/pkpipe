---
spec_id: 021-image-input
status: Active
description: Tasks for image input
updated: 2026-11-05
---

# Tasks: Image input and the image description session

Format: `- [ ] T001 [USx] Description` · `[USx]` = user story

## Phase: User Story 3 (P1, foundation)

- [ ] T001 [US3] `read_image` in the vault policy and server; refusal codes; traversal, symlink, type and size tests
- [ ] T002 [US3] `attachments_dir` read-only in the layout validation and snapshot scope (FR-010)

## Phase: User Story 1 (P1)

- [ ] T003 [US1] `ImageInput`, `ModelPort` extension, fake vision model
- [ ] T004 [US1] OpenAI adapter: `image_url` parts, `vision` and `max_tokens` profile fields, stable error
- [ ] T005 [US1] `image.prepare` (EXIF removal, scaling, hash, pixel limit) and the cache table
- [ ] T006 [US1] Session `describe_image`: schema, prompt, step defaults; retry then `needs_owner`
- [ ] T007 [US1] Triage image branch (images after the text items), `topic` column
- [ ] T007a [US1] `image.budget`: estimate, per-image update, stop rule, timing table, report; fake clock tests and the property test (SC-004)
- [ ] T007b [US1] Per-image timeout (`per_image_timeout_s`) and the notification text for deferred images

## Phase: User Story 2 (P1)

- [ ] T008 [US2] Data class `image`: loopback only, no egress, no trace content (validate and compile)
- [ ] T009 [US2] Output guard and canary on `visible_text` and `topic`; URL policy for `urls`
- [ ] T010 [US2] Leak test: traces, logs, notifications (SC-003)

## Phase: User Story 4 (P2)

- [ ] T011 [US4] Synthetic image generator and dataset; suite `images`: code checks against the known truth, a text judge (different family) only for `topic`; 20-item calibration first
- [ ] T011a [US4] Phase silo: the text phase and the image phase each use one profile and the run switches once (SC-005); unload hint for the text model between the phases; eval runner supports record-then-judge passes so the judge never shares memory with the subject (check; else small task)
- [ ] T012 [US4] Live probe test (marked `live`): the SC-002 repeat on synthetic images

## Phase: Polish

- [ ] T013 [P] Docs (configuration, operations: model memory and throughput, security), CHANGELOG
- [ ] T014 [P] BPMN: drawio, PNG, Mermaid together; the "Describe images" sub-process with the time-budget loop and the per-image timer (see the plan) (diagram drawn 2026-11-05)
- [ ] T015 Release notes: spec 020 sentences on images updated (done in this draft)
