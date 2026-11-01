---
spec_id: 009-output-guard-and-quarantine
title: Output guard and quarantine
status: Completed
description: Technology options and decision for output guard and quarantine.
updated: 2026-11-01
---

# Research: 009-output-guard-and-quarantine

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| Qwen3Guard-Gen-0.6B | Multilingual, severity levels and categories, Apache-2.0, not gated | A harm moderator: misses most plain instruction overrides (0.35 direct, 0.15 in pages) | Harmful content + Jailbreak category |
| ProtectAI deberta-v3-base-prompt-injection-v2 | Injection classifier, Apache-2.0, not gated, safetensors | Binary label, 512-token window, weaker on injections buried in long pages | Injection check |
| Meta's gated injection classifier | Small injection classifier | Gated licence that shares account data; owner refuses Meta models | Rejected (owner decision) |
| LLM self-check with the main model | No extra model | Attackable, slow | Not used |
| Deterministic checks | Certain, cheap | Only known patterns | Canary (string match), active markup, heuristics: always on |

## R-2 – Decision and rationale

`combined` backend (maximum score) of Qwen3Guard and DeBERTa, plus deterministic checks; code decides every flag. Measurements and vetting in `docs/decisions/0001-injection-classifier.md`; output-guard eval in `docs/evaluation.md`.

## R-3 – Risks and open points

- The classifiers alone detect only 0.45 of the injections hidden in long pages: the output guard and secure fetch never rely on them alone (heuristics, Reader, canary).
- Heuristics flag articles that quote attack phrases (one false positive in the output-guard set): the owner releases them, and every release becomes an eval candidate.
