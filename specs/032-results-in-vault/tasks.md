---
spec_id: 032-results-in-vault
status: Completed
description: Tasks of results in the vault
updated: 2026-11-10
---

# Tasks: Results in the vault

- [x] T001 `learning/failures.py`, `QuotaExceeded` / `AuthExpired`, `WAITING_QUOTA`, `retry_after`; tests
- [x] T002 `NotebookLMBrowserBackend`: classification, bounded retry, quota check, `resume`; gateway park and resume; tests
- [x] T003 Shipped driver `drivers/nlm_cli.py` (`usage()`), default backend `notebooklm_browser`, Open Notebook deprecated; tests
- [x] T004 `learning.create_failed`: `learning_wait` event; item stays released
- [x] T005 `domain/results.py` + `steps/results_code.py` (`learning.audio_plan`, `learning.note`, `image.result_note`, `results.write_indexes`); tests
- [x] T006 Vault tools `write_results_index`, `import_audio`; policy `audio_import_dirs`; MCP, tool sets, snapshot scope, pack allow-list; tests
- [x] T007 Config: `learning.json`, `source_discovery.from_images` default false, `security.classifier.review.mode`
- [x] T008 Flows: `learning_material` (note, audio copy, image branch), `other_resource` (image branch), `write_results_index`, `process_released`; flow tests
- [x] T009 Auto-pass: verdict, step, event, triage flag and badge, history sentence, run report; tests
- [x] T010 BPMN pages (drawio + PNG + Mermaid), `docs/flows.md`, C4 labels
- [x] T011 Docs (configuration, operations, security, owner manual, user guide, glossary, architecture, installation), CHANGELOG 0.1.12, version, `specs/README.md`
