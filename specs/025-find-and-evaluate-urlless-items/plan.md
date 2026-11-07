---
spec_id: 025-find-and-evaluate-urlless-items
status: Completed
description: Plan for source discovery of items without a URL
updated: 2026-11-07
---

# Implementation Plan: Find the source of items without a URL and evaluate it

**Branch**: `spec/025-find-and-evaluate-urlless-items` | **Spec**: [spec.md](spec.md) | **Depends on**: 008, 009, 010, 017, 020, 021, 024

## Summary

A pure module `domain/source_discovery.py` (query construction, candidate choice, fixed reasons); two code steps in `steps/discovery_code.py` (`item.find_sources`, `item.next_source`); `category.plan` returns `discover` for an item without a URL in a listed category; the flow `other_resource` gets four nodes (`load_description`, `discover`, `no_source`, `next_source`) and a new flow `load_image_description` (image data class) returns a released image's description from the cache; the `source_discovery` config; the flow eval harness learns `config` and `search` case inputs.

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| II. Hexagonal core | Yes | The steps use the tool port (search) and the existing secure-fetch subflow; the query rules are pure domain functions |
| III. Configuration over code | Yes | `source_discovery.json` switches and tunes it; the evaluation is the category's configured sessions |
| IV. Untrusted content | Yes | The query is built by a template from validated fields, neutralised and guarded; result texts are dropped, only URLs are used after the URL policy; raw page text only reaches the tool-less Reader |
| V. Code decides | Yes | Query, guard, candidate order, failure reasons are code; no model chooses what to search |
| VI. Human in the loop | Yes | A wrong match is visible in the note's source; the owner's fix for a failure is a link |
| VII. Public / private separation | Yes | Off by default; the blocked terms stay in the overlay's `security.query_guard`; no personal data in the repo |
| VIII. Local first / data classes | Yes | The image data class keeps its rule (no egress in an image flow): the description is loaded in an image flow, the only egress is the guarded query from the non-private `other_resource` flow, by explicit opt-in (`from_images`) |
| IX. Measured change | Yes | A flow eval case; no LLM step added |
| X. Observable by default | Yes | The span `source.discover` carries counts only |
| XI. Design as diagram and code | Yes | Process Other Resource: Load image description, Find source by web search, Try the next found page (drawio, PNG, Mermaid) |
| Others | n/a | |

## Structure

- `pkpipe/domain/source_discovery.py`
- `pkpipe/steps/discovery_code.py`, `steps/session_plan.py` (`discover` status), `steps/__init__.py`
- `pkpipe/engine/config_models.py`, `engine/registry.py` (`source_discovery`)
- `pkpipe/evals/flowkit.py`, `evals/suites.py` (case inputs `config`, `search`)
- `packs/learning-inbox/flows/other_resource.json`, `flows/load_image_description.json`, `source_discovery.json`
- `evals/datasets/flows/tool-without-url-finds-source.json`
- docs (flows, configuration, security, user guide, glossary, evaluation, architecture), BPMN Process Other Resource

## Risks

- A search query that names a client or project: the query guard sees the final query; the owner's blocked terms apply (tested)
- A wrong page found for a vague screenshot: the note shows its source; `max_candidates` and `categories` limit the exposure; the owner can discard the note
- The description cache misses after a vision model change: a fixed, actionable reason (triage again); no silent model call
- A larger egress surface (queries): off by default, documented in the security page
