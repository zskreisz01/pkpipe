---
spec_id: 033-pipeline-overview
status: Completed
description: Tasks of the pipeline overview
updated: 2026-11-10
---

# Tasks: The pipeline overview

- [x] T001 `domain/pipeline.py` (buckets, `build_overview`, `assemble`, `render_markdown`, `normalize`, `position`); tests
- [x] T002 `PipelineStatsPort`, `AppStore` helpers (`last_event_times`, `run_item_counts`), fake; `learning/jobs_view.py`, `scheduler.next_fire_times`; tests
- [x] T003 Layout key `pipeline_file`, policy tool `write_pipeline`, `Vault.write_pipeline`, MCP tool, snapshot scope, pack allow-list and layout; tests
- [x] T004 Config `triage.pipeline` (model default off, pack on); steps `pipeline.build` / `pipeline.write_failed`; ports wiring in `app.py`
- [x] T005 Flow `pipeline_overview`; calls in `triage` and `process_released`; flow tests with fakes (write, idempotent, switch, failing calls)
- [x] T006 `AppServices.pipeline_overview`, `item_history.where`, `GET /api/pipeline`, `/pipeline`, card actions, landing page, navigation, CSS (phone); tests incl. reconciliation
- [x] T007 Owner MCP tool `pipeline_overview`, CLI `pkpipe pipeline`; tests
- [x] T008 BPMN Process Inbox Items (drawio, PNG, Mermaid), `docs/flows.md`
- [x] T009 Docs (owner manual, user guide, operations, configuration, security, glossary, architecture, vault README), CHANGELOG 0.1.13, version, lockfile, specs README
