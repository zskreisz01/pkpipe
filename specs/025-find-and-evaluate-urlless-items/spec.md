---
spec_id: 025-find-and-evaluate-urlless-items
title: Find the source of items without a URL and evaluate it
status: Completed
status_reason: Implemented 2026-11-07 on branch spec/025-find-and-evaluate-urlless-items (from dev at v0.1.5, unreleased; T001-T008); suite, ruff and mypy green; opt-in, off by default
description: An item that a session plan needs a page for but that names no URL (an image whose description shows no address, a note or text entry without a link) no longer fails with "no source URL in the note" when source_discovery is on for its category. A guarded web search finds candidate pages from the validated image description or the owner's text, secure fetch fetches the first clean one, and the category's own configured sessions evaluate it.
relevant_when:
  - source discovery
  - web search
  - image
  - no source URL
  - tool category
  - evaluate
related: [008-secure-fetch, 009-output-guard-and-quarantine, 010-category-sessions, 017-web-search, 020-vault-layout-and-inbox-list, 021-image-input, 024-run-report-and-journal-todos]
updated: 2026-11-07
---

# Feature Specification: Find the source of items without a URL and evaluate it

**Feature Branch**: `spec/025-find-and-evaluate-urlless-items` (from `dev` at v0.1.5)
**Created**: 2026-11-07
**Status**: Completed
**Input**: Owner request (translated): items categorized as `tool` that have no source URL (images such as screenshots and shared pictures, or a text without a link) fail with "no source URL in the note". Based on the text or the image description a web search and a web fetch should be started, followed by evaluate steps. The evaluate steps belong in the config (the category's sessions), not in code.
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **Opt-in, per category.** `source_discovery.json` (`enabled`, default **false**; `categories`, default `["tool"]`) switches the feature on. A search query leaves the machine, so the owner turns it on knowingly. Only categories whose subflow is `other_resource` can use it (tool, business, job, marketing, misc, overlay categories). `learning-material` is not supported: a found URL would go to the notebook service as a source, which is a bigger step than reading a page; such an item keeps failing with "no source URL in the note".
2. **No model builds the query.** `item.find_sources` fills a template (`query_template`, default `{topic} {text}`; only those two placeholders) from fields the pipeline already validated: for an image the description's `topic` and `visible_text` (spec 021: a schema-checked answer that passed the output guard), for a note or text item the owner's text. Text derived from untrusted pixels or pages must not decide what leaves the machine, and a model rewriting a query from it would be an injection channel. The query is neutralised (`text_safety`: no URL or domain, no markdown or HTML opener; control and zero-width characters removed), cut after a whole word at `max_query_chars` (default 120) and dropped when nothing searchable is left.
3. **The web-query guard decides before the call.** The query goes through `security.query_guard` (blocked terms and the terms file: client and project names), the same check every session's search call gets. A hit refuses the search; the item fails with a fixed reason and nothing leaves. The guard sees the final query, so a blocked name inside a description or note never reaches the search server.
4. **An address in the image wins.** Spec 021 already keeps the URLs of a description that pass the URL policy, and the description note names them; `plan` finds the first one as the item's `source_url`. Discovery runs only when the item has no URL.
5. **Released images get their description back.** A released image item carries no description (it was made in the triage cycle). A new subflow `load_image_description` (data class `image`, so a loopback-only flow without egress tools) reads and prepares the image again, looks the prepared bytes up in the description cache and runs the same `image.finish` checks as the triage (URL policy, suspected injection: a flag quarantines only this item). The model is never called here. Spec 028: the lookup takes the newest description of the model under any prompt version. A cache miss (the vision model changed, or the image was never described) leaves the item without a description; discovery then fails with "the image has no cached description; triage it again". This also lets images that show an address work without discovery. The search itself happens afterwards in `other_resource`, a flow of no private class; the only thing that crosses is the guarded query, by the owner's opt-in (`from_images`, default true).
6. **Candidates and fetching.** The search tool is asked for `max_results` (5) results. Only the URLs are used (titles and snippets are untrusted and dropped): http(s), no credentials, each page once, at most one page per host, and each passes the URL policy (deny-list, private networks after DNS). The first `max_candidates` (default 2) are tried in order through the ordinary `secure_fetch` subflow (URL filter, sanitizer, heuristics, classifier, Reader, verdict); the first clean page wins and the next is tried only when a fetch failed (not on a security flag, which quarantines the item). All the safety rules of secure fetch stay: raw page text only reaches the tool-less Reader.
7. **Evaluation is configuration.** After the fetch the item is an ordinary item with `item.extract`: the category's `sessions` in `categories.json` (for Tool: research, compare, fit, synthesize, system relevance) evaluate it, with their own instructions, contexts and output schemas, all versioned config. Nothing about evaluation is hard-coded; an overlay changes the evaluation by changing the category.
8. **Failure is explicit and content-free.** No result, a blocked or empty query, a failed search, a missing description, or pages that could not be fetched leave the item `failed` with a fixed reason in the processing list ("no source found by web search; add a link to the entry and release it again", "no source found: the search query was refused by the web query guard", "...: nothing searchable in the item; add a link to its entry", "...: the web search failed; the next run tries again", "...: the image has no cached description; triage it again", or the existing "the page could not be fetched"). Neither the query nor a result text is written to a span, a message, a log or a reason; the span `source.discover` carries only counts (`discovery.query_chars`, `discovery.results`, `discovery.candidates`). The owner's fix is a link in the entry.
9. **Where a found page shows.** The note stores the found page as its source like any fetched page (`Source: <url>`); `source_discovered` is an item field only. The owner sees where an evaluation came from and can discard a wrong match.
10. **Status: Completed (2026-11-07).** Gate 2: tasks done, suite green, docs and BPMN (drawio, PNG, Mermaid) updated, consistency checklist walked. Decisions taken while implementing: a queue of candidates instead of merging several pages into one extract (the sessions evaluate one source; an owner who wants two sources lists two links); a template with two placeholders instead of a query session (see 2).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An image without an address is searched, fetched and evaluated (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a screenshot in the INBOX list that the owner categorizes as `tool` and whose description names no address, **When** the release runs with discovery on, **Then** one guarded search is made from the description, the first clean found page is fetched and the Tool plan writes its evaluation note into the PARA folder; the processing list says `done`
2. **Given** the first found page cannot be fetched, **Then** the next candidate (up to `max_candidates`) is fetched; when all fail the item is `failed` with "the page could not be fetched"
3. **Given** the image shows an address, **Then** that address is fetched and no search is made
4. **Given** the description cache has no entry, **Then** the item fails with the cache reason and no search is made

### User Story 2 - A note without a link is searched with its text (Priority: P1)

1. **Given** a note or text entry without a link in a listed category, **Then** the query is built from its text and the same steps follow
2. **Given** `from_notes: false` or `from_images: false`, **Then** that kind is not searched and fails as before

### User Story 3 - Nothing leaks, nothing is silent (Priority: P1)

1. **Given** a blocked term in the query, **Then** no search is made and the item fails with the guard reason
2. **Given** no result, an empty query or a failing search tool, **Then** the item fails with the fixed reason from clarification 8
3. **Given** a run with discovery on, **Then** no span, notification or log holds the query or a result text
4. **Given** discovery off (the default) or a category that is not listed, **Then** nothing changes: "no source URL in the note", no search, no fetch

### Edge Cases

- A search result that is a private address, a deny-listed domain, a non-http URL or a URL with credentials is dropped; fewer than `max_candidates` candidates is normal.
- Two results of one host: only the first is a candidate.
- The vision model changed between triage and release: images fail with the cache reason until triaged again (images are described once per model; since spec 028 a changed prompt alone no longer hides a description at release).
- A suspected injection or a deny-listed URL in the description: the item is quarantined, as in the triage.

## Requirements *(mandatory)*

- **FR-001**: `source_discovery.json` (`SourceDiscoveryConfig`: `enabled`, `categories`, `from_images`, `from_notes`, `max_candidates`, `max_results`, `max_query_chars`, `query_template`), validated, core default off, pack default off
- **FR-002**: `domain/source_discovery.py`: query construction (template with `{topic}` and `{text}` only, neutralised, whole words, empty when nothing searchable), candidate choice, fixed failure reasons
- **FR-003**: code steps `item.find_sources` and `item.next_source` (`steps/discovery_code.py`); `category.plan` returns `discover` for an item without a URL in a listed `other_resource` category
- **FR-004**: the query is checked by `security.query_guard` before the search tool is called; the search tool is the configured `websearch:search`
- **FR-005**: flow `other_resource`: nodes `load_description`, `discover`, `no_source`, `next_source` and their edges; flow `load_image_description` (data class `image`, no model call)
- **FR-006**: candidates pass the URL policy and are fetched by `secure_fetch` in order until one is clean; a security flag ends the item
- **FR-007**: the evaluation is the category's configured session plan; no evaluation text or step in code
- **FR-008**: failures are fixed texts in the processing list; the span `source.discover` carries counts only; no query, description or result text in any span, message, log or reason
- **FR-009**: the flow eval harness can switch a config section on per case (`config`) and script the search results (`search`); a flow case covers a note without a URL that finds its source
- **FR-010**: docs (flows, configuration, security, user guide, glossary, evaluation, architecture), BPMN page Process Other Resource (drawio, PNG, Mermaid)

## Success Criteria

- **SC-001**: on a synthetic vault an image without an address and a note without a link are each evaluated from a found page when discovery is on (integration tests), and fail exactly as before when it is off
- **SC-002**: no query text, description text or result text appears in any span, notification or reason of an end-to-end run (tests)
- **SC-003**: a blocked term never reaches the search tool (tests)
- **SC-004**: the 24 flow eval cases pass, including the new case, with the default config
