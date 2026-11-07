---
spec_id: 024-run-report-and-journal-todos
status: Completed
description: Data model for the run report and the journal todo block
updated: 2026-11-07
---

# Data Model

**Config `report.json`** (`ReportConfig`)
- `enabled` (bool, default `true`): send the run report as `daily_summary` after every triage and release run; `false`: the earlier messages
- `tool_category` (category id, default `tool`): the category whose pipeline notes the todo `tool_reads` counts
- `journal_todos.enabled` (bool, default `false`), `journal_todos.heading` (one Markdown heading line, at most 60 characters, no `<` `>`; default `## pkpipe todo`)

**Vault policy** (`allow`): `write_journal_todos: [folder, ...]` (no default); `unreviewed_notes` defaults to the project, area and resource folders.

**Report item** (input of the subflow, built by `report.settings`): `kind` (`triage` | `process_released`), `summary` (counts: `new_by_category`, `images_described`, `images_deferred`, `quarantined`, or `by_category`, `processed`, `quarantined`, `failed`, `evaluations`, `notebooks`, `podcasts`, `notebooks_failed`, `proposals`), `notes` (the attention sentences of the run: counts, slugs, paths), `tool_folder`, `tool_category`, `journal`, `heading`, `surface`, `public_url`, `triage_file`, `processing_list`.

**Todo** (`key`, `count`, `link`): `approval` "Review triage decisions", `notebooks` "Process notebook materials and podcasts", `tool_reads` "Read tool evaluations". Line: `- <date> - <label> - <n> item(s) - <link>`; links: `[triage UI](<public_url>/triage)` on the web surface, else `[[<triage file>]]`; `[[<processing list>]]`; `[[<newest unread evaluation>]]`.

**Notification** `daily_summary`: `message` (Markdown, at most 1800 characters, first line `<date> <HH:MM> · run <id8>`), payload `title`, `awaiting`, `pending`, `todos` (key -> count), `new`, `processed`, `quarantined`, `failed`, `evaluations`, `notebooks`, `podcasts`.

**Journal block**: after the heading, `<!-- pkpipe-todo:begin -->`, the lines, `<!-- pkpipe-todo:end -->`. A new note: `---\ntype: journal\n---\n\n<heading>\n<block>\n`.

**Tool results**: `write_journal_todos` -> `{path, lines, created}`; `unreviewed_notes` -> `{count, newest}` (a vault path or null).
