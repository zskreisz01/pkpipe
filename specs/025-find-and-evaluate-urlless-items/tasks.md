---
spec_id: 025-find-and-evaluate-urlless-items
status: Completed
description: Tasks for source discovery of items without a URL
updated: 2026-11-07
---

# Tasks: Find the source of items without a URL and evaluate it

Format: `- [x] T001 [USx] Description`

## Phase: Setup

- [x] T001 Spec, plan, data model, tasks

## Phase: User Story 1 and 2 (find, fetch, evaluate)

- [x] T002 [US1] `domain/source_discovery.py` (query, candidates, reasons) with unit tests (cleaning, word cut, host and policy rules, malformed results)
- [x] T003 [US1] `SourceDiscoveryConfig`, core default, pack default `source_discovery.json`; schema export test
- [x] T004 [US1] `steps/discovery_code.py` (`item.find_sources`, `item.next_source`), `category.plan` status `discover`; unit tests (guard, failures, spans, policy)
- [x] T005 [US1] Flow `load_image_description` and the new nodes and edges of `other_resource`
- [x] T006 [US1] Integration tests: image, note, candidates, address wins, max candidates, cache miss

## Phase: User Story 3 (nothing leaks, nothing silent)

- [x] T007 [US3] Integration tests: blocked term, no result, nothing searchable, off by default, category not listed, no query text in spans or notifications; flow eval harness inputs `config` and `search` and the case `tool-without-url-finds-source`

## Phase: Polish

- [x] T008 Docs (flows, configuration, security, user guide, glossary, evaluation, architecture), BPMN drawio + PNG + Mermaid, CHANGELOG (Unreleased), spec index; consistency walk
