---
spec_id: 032-results-in-vault
status: Completed
description: Data model of the learning note, the image note, the index, the quota state and the auto-pass fields
updated: 2026-11-10
---

# Data Model: Results in the vault

**Learning note** (`<para path>/<slug of "Learning: <host>/<path>">-<item id>.md`): front matter `type: learning_note`, `source`, `category`, `pkpipe_item`, `notebook_url`, `created`, optional `audio_file`; body: title, `Source:`, `Notebook:`, `Podcast:` (embed of the copied file, the local path, or "not available"), `History:` (`<ui_public_url>/items/<id>`).

**Image note** (`<para path>/<slug of topic>-<item id>.md`): front matter `type: resource_note`, `source: ""`, `origin: image_description`, `category`, `pkpipe_item`, `inbox_note`, `created`; body: title (the topic), `Image:`, `What the image shows`, `Text in the image` (quote, at most 400 characters), `Addresses in the image`, kind and language, `Next step`. Written only when `usable_description` (3 letters or digits) and after the output guard.

**Result index** (`<folder>/_Index.md`, `type: pkpipe_index`): header, then `| date | note | category | source | result | notebook | item |`; `result` is the first line of `## Recommendation`, `podcast + notebook` or `from the image only`; at most 500 rows, newest first; cells escaped like the InProgress table.

**Job** (`NotebookJob`): `status` adds `waiting_quota`; `retry_after` (epoch seconds, may be empty); `nb_id` set when the notebook exists. Failure reasons start with the class: `quota:`, `auth:` or the backend text.

**Verdict**: `auto_passed: bool`; `scores.classifier_auto_passed`; item field `auto_passed: {reason, evidence}`.

**Config**: `learning.json {copy_audio_to_vault}`; `source_discovery.from_images` default false; `security.classifier.review.mode` `hold | auto_pass`; vault policy `audio_import_dirs` (absolute folders), `max_audio_bytes`; NotebookLM `backend_options`: `driver`, `call_timeout_s`, `retries`, `backoff_s`, `min_remaining_percent`, `reason_detail`.

**Events**: `result {what, path}`, `learning_wait {reason}` (outcome `quota` or `auth`), `auto_passed {classifier, url}`.
