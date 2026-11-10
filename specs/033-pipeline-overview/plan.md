---
spec_id: 033-pipeline-overview
status: Completed
description: Plan for the pipeline overview
updated: 2026-11-10
---

# Implementation Plan: The pipeline overview

**Branch**: `spec/033-pipeline-overview` | **Spec**: [spec.md](spec.md) | **Depends on**: 014, 020, 023, 029, 030, 031, 032

## Summary

Pure `domain/pipeline.py` decides the bucket of every triage row, builds the overview and renders the note. `assemble` is the one assembler: the UI and the services call it with the vault tables read directly and the app DB as `PipelineStatsPort`; the flow step `pipeline.build` calls it with the tables the flow read through the vault tools and the same helper ports from `StepPorts.extra` (`pipeline_stats`, `run_history`, `learning_jobs`, `next_runs`). The vault tool `write_pipeline` writes the note only when it changed.

## Constitution Check

- **Code decides (V):** buckets, order, counts and the note are code; no model is involved.
- **No untrusted text (IV, VIII):** only ids, codes, addresses as inline code, fixed sentences and the stored situation sentences (spec 031, validated, escaped again here); no page, note or image text.
- **Ports and adapters (II):** the helper tables are behind `PipelineStatsPort` and callables built in `app.py`; the core never imports `scheduler` or an adapter.
- **Private data (VI):** the subflow has the data class `image` like the unfinished-items subflow (local only, content-free traces); the note holds no secrets.
- **Owner decides (VI):** the overview changes nothing by itself; the buttons are the existing audited actions.
- **BPMN (XI):** Process Inbox Items gets the task *Refresh the pipeline overview note* (drawio, PNG, Mermaid, `docs/flows.md`). No new component: C4 is unchanged (the overview is part of the app and the vault server).

## Risks

- **A long note:** capped at 40 rows per section; the page shows all.
- **Churn in the vault:** relative times would change on every refresh, so the note holds absolute UTC times only and is compared without its `Generated:` line.
- **Stale or empty helper tables:** each helper read is guarded; the overview is for orientation and never the reason a page or a run fails.
- **Old items:** items closed before the event log (spec 030) have no finish time; they count in the funnel and are not listed under *Done recently*.

## Rollback

`triage.pipeline.enabled: false` stops the note; the page stays (it only reads). The vault tool and the layout key are inert when unused.
