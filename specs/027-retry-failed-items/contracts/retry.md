---
spec_id: 027-retry-failed-items
status: Completed
description: HTTP, MCP and CLI contract of the owner's retry
updated: 2026-11-07
---

# Contract: retry of failed items (owner only)

| Channel | Call | Result |
| --- | --- | --- |
| HTTP (owner token) | `GET /api/triage/failed` | `{failed, retryable, capped, max}` |
| HTTP | `POST /api/triage/{item_id}/retry` | the row plus `retries`; 409 with the reason when refused |
| HTTP | `POST /api/triage/retry-failed` body `{"confirm": true, "item_ids": [...]?}` | `{retried, skipped, capped, item_ids}`; 409 without `confirm` |
| UI | row button **Retry**; **Retry all failed (N)** (`hx-confirm` + server side `confirm=yes`) | row / redirect with counts |
| App MCP (owner token) | `retry_items(item_ids?)` | `{retried, skipped, capped, item_ids}`; an agent token is refused (audited `retry_items_refused`) |
| CLI | `pkpipe retry` (list) / `pkpipe retry --all` / `pkpipe retry ITEM ...` | counts; `--all` with ids is a usage error (exit 2) |
| Vault MCP (sessions) | `failed_items()` (read only) | `{count, items: [{item_id, category}]}`; there is no retry tool |

The agent token gets 403 on every HTTP route above, like every other owner endpoint.
