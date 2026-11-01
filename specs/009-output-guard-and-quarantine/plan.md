# Implementation Plan: Output guard and quarantine

**Branch**: `spec/009-output-guard-and-quarantine` | **Date**: 2026-10-31 | **Spec**: [spec.md](spec.md)

## Summary

Checks every output derived from external content before it is written (run canary, markup and heuristics in code, Qwen3Guard + ProtectAI DeBERTa) and handles security flags: malicious URL log, deny-list, inert quarantine, alert, owner release with eval candidate.

## Technical Context

**Language/Version**: Python 3.12+ (`uv`)
**Primary Dependencies**: Qwen3Guard-Gen-0.6B and ProtectAI deberta-v3-base-prompt-injection-v2 via transformers (extra `guard`, local files only; decision record `docs/decisions/0001-injection-classifier.md`).
**Testing**: pytest with port fakes, eval suite
**Target**: single machine, local model server

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| I. Local-first, model-agnostic | Yes | |
| II. Hexagonal core | Yes | |
| III. Configuration over code | Yes | |
| IV. Untrusted content isolation | Yes | |
| V. Code decides | Yes | |
| VI. Human in the loop | Yes | |
| VII. Public / private separation | Yes | |
| VIII. Small, isolated sessions | Yes | |
| IX. Measured change | Yes | |
| X. Observable by default | Yes | |
| XI. Design as diagram and as code | Yes | |
| XII. Tooling and language | Yes | |

## Project Structure

- Core: `src/pkpipe/security/guard.py` (output guard decision), `src/pkpipe/security/quarantine.py` (MaliciousUrlEntry, deny-list edit, inert format, release candidate), `src/pkpipe/security/canary.py` (`run_canary`)
- Steps: `src/pkpipe/steps/output_guard.py` (step type `output_guard`), `src/pkpipe/steps/security_code.py` (`security.record_flag`, `security.quarantined`), `canary` option and guarded-session validation in `src/pkpipe/steps/llm.py`
- Ports/adapters: `SecurityLogPort` + `src/pkpipe/adapters/security_log_files.py` (fake `tests/fakes/security_log.py`); `ClassifierPort` adapters `classifier_qwen3guard.py`, `classifier_deberta.py`, `classifier_combined.py`, factory `classifiers.py`
- Vault: `quarantine_note` tool, protected quarantine folder, release restore (`src/pkpipe/obsidian/vault.py`, `server.py`)
- Owner release: `AppServices.release_quarantine` (UI route and app MCP tool from spec 014)
- Pack: `flows/handle_security_flag.json`; `categorize_item`, `other_resource`, `learning_material` route flags to it; `security.json` (`classifier`, `output_guard`)
- Evals: `evals/datasets/output_guard.jsonl`, flow cases `triage-canary-leak-quarantined`, `other-guard-quarantines`, `learning-url-blocked`; classifier cassette `evals/cassettes/classifier.json`

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
| --- | --- | --- |
| – | – | – |
