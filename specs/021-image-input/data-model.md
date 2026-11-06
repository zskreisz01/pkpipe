---
spec_id: 021-image-input
status: Completed
description: Data model for image input
updated: 2026-11-05
---

# Data Model

**ImageInput**: `data` (bytes), `media_type` (`image/jpeg` after preparation).

**ModelProfile** (additions): `vision` (bool, default false), `max_tokens` (int, optional).

**ImageDescription** (schema `image_description`): `kind` (`screenshot | photo | meme | diagram | document | other`), `topic` (string, one sentence), `visible_text` (string, max 400), `urls` (list of strings), `language` (string), `category_hint` (a category id or `unknown`, advisory only), `injection_suspected` (bool), optional `evidence` (shortest suspicious passage).

**ImageCacheRow** (app DB): `sha256` (prepared bytes), `model`, `prompt_version`, `description` (JSON), `created_at`.

**images.json**: `time_budget_s` (7200), `estimate_s` (60), `per_image_timeout_s` (300), `max_side` (1024), `max_tokens` (1200), `max_bytes` (10485760), `retries` (1).

**ImageTiming** (app DB): `run_id`, `sha256`, `seconds`, `model`; the seed of the next run's estimate is the median of the last 50 rows of the model.

**ImageBatchReport** (run output and notification): `images_total`, `cached`, `described`, `failed`, `deferred`, `estimate_start_s`, `estimate_end_s`, `elapsed_s`, `stopped_by_budget` (bool).

**Triage row** (additions, spec 020 columns stay): `kind=image`; `needs_owner` is `yes` only for `failed` and `flagged` (a deferred image has no row until it is processed); the suggestion comes from the category session; the column `topic` holds the one-line topic.

**Vault tool** `read_image`: input `name`; output `{name, media_type, size, data_base64}`; refusal codes `not_found`, `bad_name`, `bad_type`, `too_large`.
