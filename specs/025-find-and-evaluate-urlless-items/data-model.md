---
spec_id: 025-find-and-evaluate-urlless-items
status: Completed
description: Data model for source discovery of items without a URL
updated: 2026-11-07
---

# Data Model

**Config `source_discovery.json`** (`SourceDiscoveryConfig`)
- `enabled` (bool, default `false`)
- `categories` (list of category ids, default `["tool"]`; only categories with subflow `other_resource` are searched)
- `from_images` (bool, default `true`), `from_notes` (bool, default `true`)
- `max_candidates` (1 to 5, default 2): pages fetched in order until one is clean
- `max_results` (1 to 10, default 5): results asked from the search tool
- `max_query_chars` (20 to 300, default 120): the query is cut after a whole word
- `query_template` (at most 200 characters, default `{topic} {text}`): only the placeholders `{topic}` (image topic, else empty) and `{text}` (image visible text, else the note text), at least one

**Item fields** (run state of one item; never written to the vault): `source_url` (the page being fetched), `source_candidates` (the URLs still to try), `source_discovered` (true when `source_url` came from a search), `description` (spec 021 description, loaded from the cache for a released image).

**`category.plan` status**: `ok`, `no_plan`, `no_url`, `discover`.

**`item.find_sources` result**: `status` (`ok`, `no_description`, `nothing_to_search`, `query_blocked`, `no_results`, `search_failed`), `found`, `results`, `dropped`, and `reason` (a fixed text) on a failure. The query is never part of the result.

**`item.next_source` result**: `status` (`ok`, `exhausted`), `remaining`.

**Processing list reasons** (column `link` of a failed row): "no source found by web search; add a link to the entry and release it again", "no source found: the search query was refused by the web query guard", "no source found: nothing searchable in the item; add a link to its entry", "no source found: the web search failed; the next run tries again", "no source found: the image has no cached description; triage it again", "the page could not be fetched", "no source URL in the note" (discovery off or the category not listed).

**Span `source.discover`**: `discovery.query_chars`, `discovery.results`, `discovery.candidates`, `discovery.refused` (`blocked_term`), `discovery.error` (exception type). No query, description or result text.

**Flow eval case inputs** (new): `config` (a section name to a dict merged over the pinned section) and `search` (the list of results the scripted search returns).
