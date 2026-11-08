---
spec_id: 008-secure-fetch
title: Secure fetch
status: Draft
description: Entities and storage for secure fetch.
updated: 2026-10-31
---

# Data Model: 008-secure-fetch

**Spec:** [spec.md](spec.md)

Notation: **NEW** = introduced by this spec · **EXISTING** = already in the codebase.

## Entities

### NEW `ReaderExtract`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `title` | str | yes | ≤ 300 chars |
| `summary` | str | yes | ≤ 200 words (≤ 2000 chars enforced) |
| `facts` | list[str] | yes | Short factual bullets (≤ 12, each ≤ 400 chars) |
| `links` | list[str] | no | Relevant outbound links (≤ 10, each ≤ 500 chars; only http(s) leave secure fetch) |
| `injection_suspected` | bool | yes |  |
| `evidence` | str|null | no | Quoted suspicious text (≤ 1000 chars; never leaves secure fetch) |

### NEW `Verdict`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `clean` | bool | yes |  |
| `status` | enum | yes | clean · flagged · review (spec 029: held for the owner) · failed (timeout, unreachable: not a flag) |
| `flags` | list[enum] | yes | url_filter · heuristic · classifier · canary · reader |
| `reasons` | list[str] | yes | Reason codes (never page text) |
| `flag_reasons` | object | yes | First reason code per flag |
| `scores` | object | no | Per-detector scores, fetcher, fallback |

### NEW `FetchResult`

Returned by the web-fetch tool `fetch_page` (raw, untrusted).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `status` | enum | yes | ok · blocked (flag) · failed |
| `url`, `final_url` | str | yes / no | Requested URL, URL after redirects |
| `http_status`, `content_type` | int, str | no | Main response |
| `kind` | enum | yes | html · text · pdf · video · image · audio · other |
| `html` | str\|null | no | Rendered DOM (html/text only) |
| `fetcher` | enum\|null | no | lightpanda · chromium |
| `fallback_used`, `fallback_reason` | bool, str | no | Chromium fallback and why |
| `reason` | str | no | Reason code of a block or failure |

## Diagram

```mermaid
erDiagram
    ReaderExtract {
        str title
        str summary
        list_str facts
        list_str links
        bool injection_suspected
        str evidence
    }
    Verdict {
        bool clean
        list_enum flags
        object scores
    }
```

## Storage

- Pydantic models in `pkpipe.security.verdict`; JSON Schemas exported by `pkpipe schema` (`fetch-result`, `reader-extract`, `verdict`). The Reader's structured-output schema is `packs/learning-inbox/schemas/reader_extract.json` (a test keeps fields and bounds in line). The Reader step validates every reply strictly against the model; a reply that does not validate is flagged (`reader_invalid_output`).
- Nothing new is persisted beyond the run checkpoints; the verdict sets `item.fetch_status`, `item.extract` (clean only), `item.flagged`, `item.security_flags` (each classifier flag with `score` and `detail`, spec 029) and, when held, `item.classifier_review`; the verdict also has `evidence` and `review`. The web-fetch server can append one JSON line per fetch (`--stats-file`, no URLs) for the fallback rate.
