---
spec_id: 028-cache-any-version-and-protected-domains
title: Cached image descriptions at release, protected hosts in the deny-list
status: Completed
status_reason: Implemented 2026-11-08 on branch spec/028-cache-any-version (from dev at v0.1.6); tests green; awaiting the owner's approval to publish
description: Two defects found in the first production run of specs 025 and 021/009. (1) A released image item looked up its description with today's prompt digest and missed descriptions the triage made with an earlier digest; the release makes no model call, so it now takes the newest description of the model under any prompt version. (2) One flagged page put its whole host on the deny-list (a single github.com flag blocked every later github.com link); security.url_filter.protected_domains lists hosts that a flag never deny-lists.
relevant_when:
  - image description cache
  - prompt version
  - load_image_description
  - deny-list
  - quarantine
  - protected domains
related: [009-security-quarantine, 021-image-input, 025-find-and-evaluate-urlless-items]
updated: 2026-11-08
---

# Feature Specification: Cached image descriptions at release, protected hosts in the deny-list

**Feature Branch**: `spec/028-cache-any-version` (from `dev` at v0.1.6)
**Status**: Completed (2026-11-08)

## Clarifications

1. **Release takes any prompt version.** `ImageStorePort.cache_get_latest(sha256, model)` returns the newest description of the prepared bytes for the model, whatever `prompt_version` it was stored with. Only `image.prepare` with `any_version: true` uses it, and only the flow `load_image_description` sets that. The triage flows keep the exact key `(sha256, model, prompt_version)`, so a changed prompt still re-describes new images. The description was validated and guarded when it was written, and `image.finish` checks it again at release (URL policy, suspected injection), so nothing unchecked is served. A different model or different bytes is still a miss.
2. **Protected hosts.** `security.url_filter.protected_domains` (list of hosts; a host and its subdomains) names hosts that `security.record_flag` never puts on the deny-list. The flag is still logged to `malicious_urls.jsonl`, the item is still quarantined, and only that item stops; the other pages of the host stay fetchable. The pack ships a conservative list (github.com, gitlab.com, huggingface.co, arxiv.org, pypi.org, npmjs.com, wikipedia.org, stackoverflow.com, youtube.com); the overlay can extend or replace it. Hosts that serve arbitrary user content (raw.githubusercontent.com, github.io) are not in the list.
3. **Cause.** In the first production run a classifier flag on one repository page deny-listed `github.com`; the URL filter then blocked 12 further GitHub links with the reason `deny_list`, so the quarantine held mostly items nobody had judged.

## Functional Requirements

- **FR-001**: `ImageStorePort` has `cache_get_latest`; the SQLite adapter and the fake implement it (newest `created_at`).
- **FR-002**: `image.prepare` has the argument `any_version` (default false); `load_image_description` sets it.
- **FR-003**: `security.url_filter.protected_domains` (default empty in the model, the list above in the pack); `security.record_flag` skips `deny_domain` for a protected host and reports `deny.protected: true`.

## Success Criteria

- **SC-001**: a released image whose description was cached under another prompt digest is described from the cache (test in `test_source_discovery_flows`).
- **SC-002**: a flag on a protected host or a subdomain leaves the deny-list file unchanged and still logs the row (tests in `test_security_code`, `test_quarantine`).
