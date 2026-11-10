---
spec_id: 032-results-in-vault
title: Results land in the vault, NotebookLM is the learning backend, images keep their own note, the injection detector alone can auto-pass
status: Completed
status_reason: Implemented 2026-11-10 on branch spec/032-results-in-vault (from dev at v0.1.11); suite, ruff green; awaiting the owner's approval to publish
description: After days of production about 40 percent of the items were closed. The results were hard to find (tool notes without an index, learning results only a row in the processing list with a local link and a podcast outside the vault), the biggest failure groups were screenshots (no address, no source found), injection-detector-only holds (17) and NotebookLM failures whose real cause was hidden behind a generic message. A ready notebook now gets a learning note, every result folder an _Index.md, an image without an address its own guarded note from the description, NotebookLM is the default backend with a classified failure reason, a quota pre-check and a bounded retry, and security.classifier.review.mode auto_pass lets the exact spec 029 case through, logged.
relevant_when:
  - learning note
  - results index
  - _Index.md
  - NotebookLM
  - quota
  - nlm usage
  - image note
  - source_discovery.from_images
  - auto_pass
  - injection detector only
  - copy_audio_to_vault
related: [012-learning-gateway, 020-vault-layout-and-inbox-list, 025-find-and-evaluate-urlless-items, 029-classifier-evidence-and-review, 030-item-event-log, 031-inbox-in-progress]
updated: 2026-11-10
---

# Feature Specification: Results in the vault

**Feature Branch**: `spec/032-results-in-vault` (from `dev` at v0.1.11)
**Status**: Completed (2026-11-10)
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **Cause.** The owner's production numbers (about 80 items): 14 vault notes and 18 notebooks with a podcast, 48 unfinished: 17 held (injection detector only, mostly image items), 13 "no source found" plus 6 "no source URL" (screenshots), 10 page not fetched, 2 model errors; 28 notebook jobs failed (14 NotebookLM "start audio overview", 10 Open Notebook transcript limits). The tool notes existed (`4-Resources/Tools`) but nothing listed them; a ready notebook was only a processing-list row with a `127.0.0.1` link, the podcast was outside the vault.
2. **Decision (owner, 2026-11-10): all four items, and NotebookLM only.** NotebookLM gave the better results; Open Notebook stays in the code, marked deprecated, not the default.
3. **Learning note.** When the notebook and the audio are ready the flow writes `<para path>/<title>-<item id>.md` (`type: learning_note`; front matter `source`, `category`, `pkpipe_item`, `notebook_url` = `https://notebook.google.com/notebook/<id>`, `created`, `audio_file`). The body has only facts of our own: source address, notebook link, podcast file or a missing-podcast note, history link (`/items/<id>` below `ui.public_url`). **No text derived from the page** (the learning flow's rule), so no output guard is needed. The processing-list row points at the note (`ready`), which also closes the item for the INBOX-InProgress table (spec 031).
4. **Podcast in the vault: off by default.** `learning.copy_audio_to_vault` (new `learning.json`, default false) copies the audio into `<attachments>/Podcasts/<item id>-<name>.<ext>` with the new vault tool `import_audio(src, name)` and embeds it. Reason for the default: 18 episodes are 575 MB (about 30 MB each) and the vault is committed to git every night. The tool reads only below `audio_import_dirs` of the vault policy (empty = refuses everything), only audio types, at most `max_audio_bytes` (200 MB); it does not enter a snapshot scope (it only copies a generated file into the attachments folder).
5. **Result index.** After every release run the owner finds one `_Index.md` per category folder: a plain Markdown table of the pkpipe notes there (date, `[[note]]`, category, source, result, notebook link, item id), newest first, at most 500 rows, no Dataview. **Decision: one file per folder, not one global file.** The private vault policy lists `write_note` folders per category already, while a root file in `4-Resources` is not allowed; a per-folder index also sits where the owner browses. The vault tool `write_results_index(folder)` builds it from the notes' front matter and first heading inside the vault server (no note text goes through the flow) and writes only when the text changed (no git noise); a folder without pkpipe notes gets no file. The flow `write_results_index` (a subflow of `process_released`, after the unfinished-items table) calls it per enabled category folder and never stops the run.
6. **NotebookLM only and robustness.** Root cause of the opaque failures: the backend swallowed the driver's message ("page may have changed or the browser session expired"), so a daily/weekly usage limit looked like a UI problem. Now (a) the failure is classified from the driver's short message: `quota`, `auth`, `page_changed`, `transient`, `unknown`; the reason names the class and, with `backend_options.reason_detail`, the cleaned message; (b) **quota pre-check**: the optional driver method `usage()` (the JSON of `nlm usage --json`) is read before anything is created; a window with less than `min_remaining_percent` (5) left refuses the work with the reset time, creating nothing; (c) **bounded retry** of a transient error (`retries` 2, `backoff_s` 5 and 20 s), never of quota, login, a changed page, or a hung call (a hung browser is not retried: threads pile up) and never of the audio start; (d) **waiting is not failing**: a quota refusal after the notebook exists saves the job as `waiting_quota` (`retry_after`), `learning.create_failed` records a `learning_wait` event and leaves the item released, and the next run after the reset starts only the audio overview on the notebook that exists (`backend.resume`); (e) the `nlm` driver now ships in the engine (`pkpipe.learning.drivers.nlm_cli`, no credentials, the owner's `nlm login` session) and is the default driver, so the pack needs no private driver; the private `drivers/nlm_driver.py` can be dropped.
7. **Image path.** An image item with a validated, usable description (at least three letters or digits in topic and visible text) and a category folder becomes **its own note** `<para path>/<topic>-<item id>.md` (`origin: image_description`: what the image shows, the text in the image as a quote, addresses in the image, a next-step line) when no address is found: (i) an address in the image is still fetched first as before; (ii) web search for the source runs only when `source_discovery.from_images` is on (**default now false** in the model, the core defaults and the pack: it was true; this is the requested `search_images` switch, kept under its spec 025 name so there are not two keys for one thing); (iii) when the search is on and finds nothing, is refused by the query guard or fails, the image still gets its note instead of "no source found". The description derives from untrusted image content, so the note passes the **output guard** before `write_note`; a flag quarantines the item and nothing is written. An image without a usable description fails as before with the specific reason (no cached description, nothing searchable). The learning flow handles image items the same way (it now loads the cached description first).
8. **Auto-pass.** `security.classifier.review.mode`: `hold` (spec 029, engine default) or `auto_pass`. In `auto_pass` exactly the holdable case passes: the injection detector alone is over the threshold, every safety model says Safe, the heuristics are clean, the Reader ran and returned `injection_suspected: false`, the canary is intact. The verdict is `clean` with `auto_passed`, the evidence is kept, an `auto_passed` item event is written (spec 030), the triage row shows *auto-passed*, the item history has the sentence and the scores, and the run report counts `auto-passed N`. Every other finding still quarantines (safety model Controversial or Unsafe, both models, heuristics, suspicious or invalid Reader, canary, no Reader opinion, unavailable classifier). The output guard still applies. `mode: auto_pass` works without `enabled`; `enabled` without a mode keeps the hold. Pack default stays `hold`; the owner sets `auto_pass` in the overlay.
9. **Where in the process.** `learning_material`: after the audio, `learning.audio_plan` -> optional `obsidian:import_audio` -> `learning.note` -> `obsidian:write_note` -> `ready`. `other_resource` and `learning_material`: `image_note` -> `image_guard` -> `write_image` -> `record_image` -> archive. `process_released`: `results_index` (subflow `write_results_index`) before the run report. `secure_fetch.verdict`: the `auto_passed` branch. BPMN pages updated: Process Learning Material, Process Other Resource, Secure Fetch, Process Inbox Items.
10. **Out of scope (spec 033):** one overview and control page for the whole pipeline (UI and vault).

## Functional Requirements

- **FR-001**: pure `pkpipe.domain.results` (`learning_note`, `image_note`, `usable_description`, `index_row`, `render_index`, `audio_vault_name`) and `pkpipe.learning.failures` (`classify_failure`, `quota_wait`, `quota_reset_guess`, `until_text`).
- **FR-002**: `QuotaExceeded` / `AuthExpired`, `JobStatus.WAITING_QUOTA`, `NotebookJob.retry_after`; the gateway parks and resumes; `NotebookLMBrowserBackend` classifies, retries, checks the quota, `resume`s; the shipped driver `pkpipe.learning.drivers.nlm_cli`; backend default `notebooklm_browser`; Open Notebook marked deprecated.
- **FR-003**: config `learning.json` (`copy_audio_to_vault`), `source_discovery.from_images` default false, `security.classifier.review.mode`.
- **FR-004**: vault tools `write_results_index`, `import_audio` (MCP, policy names and default allow-lists, snapshot classification, pack `mcp_servers.json`), policy `audio_import_dirs`, `max_audio_bytes`.
- **FR-005**: code functions `learning.audio_plan`, `learning.note`, `image.result_note`, `results.write_indexes`; routing in `category.plan`, `learning.prepare`, `item.find_sources` (`describable`); flows `learning_material`, `other_resource`, `write_results_index`, `process_released`.
- **FR-006**: `Verdict.auto_passed`, `evaluate(auto_pass=)`, `decide` maps the mode; `item.auto_passed`, the `auto_passed` event, the triage flag and badge, the history sentence, the run-report count.
- **FR-007**: events `result` (a note was written), `learning_wait` (quota or login), `auto_passed`.

## Success Criteria

- **SC-001**: a ready notebook has a learning note the list row points at, with no page text (`test_results_flows.py`).
- **SC-002**: every result folder has an `_Index.md` that is not rewritten when nothing changed (`test_results_flows.py`, `test_vault_results.py`).
- **SC-003**: a quota refusal creates nothing and fails no item; the next run after the reset only starts the audio (`test_learning_quota.py`).
- **SC-004**: an image without an address gets its own note, and an injection in it is quarantined before any write (`test_source_discovery_flows.py`).
- **SC-005**: auto-pass passes only the exact case; every other finding still flags (`test_classifier_evidence.py`, `test_source_discovery_flows.py`).
- **SC-006**: the podcast is copied only below `audio_import_dirs` (`test_vault_results.py`).
