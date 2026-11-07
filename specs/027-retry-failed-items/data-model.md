---
spec_id: 027-retry-failed-items
status: Completed
description: Data of the owner's retry of failed items
updated: 2026-11-07
---

# Data Model: The owner retries failed items

## App database table `item_retries`

| Column | Type | Meaning |
| --- | --- | --- |
| `item_id` | TEXT, primary key | the triage item id (hash of the resource) |
| `retries` | INTEGER | how many times the owner sent this item through the release again |
| `updated_at` | TEXT | ISO time of the last retry |

Never holds a resource, a note or a failure text. Created with `CREATE TABLE IF NOT EXISTS` (no migration).

## Files

- Triage row: `state` `processed -> released` by `lists.retry_item` only; `confirm`, `override`, `comment` and every other cell stay.
- Processing list line (`item | category | note | nb_id | link | status`): `status` `failed -> unprocessed`; the other cells stay until the next run rewrites the line.

## Config

`triage.retry_max` (integer 1 to 20, default 3).

## API shapes

- Row of `list_triage` (processed rows with a failed line only): `failed: true`, `retries: int`, `retryable: bool`.
- `failed_summary`: `{failed, retryable, capped, max}`.
- `retry_item`: the row plus `retries`; `retry_failed`: `{retried, skipped, capped, item_ids}`.
- Vault tool `failed_items`: `{count, items: [{item_id, category}]}`.
