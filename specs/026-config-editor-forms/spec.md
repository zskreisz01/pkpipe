---
spec_id: 026-config-editor-forms
title: Config editor forms in the UI
status: Completed
status_reason: Implemented 2026-11-07 on branch spec/026-config-editor-forms (from dev at v0.1.5, unreleased; T001-T008); suite, ruff and mypy green; awaiting the owner's approval to publish
description: The config UI edits every config section through generated forms (fields, row actions, per-field errors, secrets only as env references, cron preview, token hint for instruction texts) next to the raw JSON view; both edit the same draft and nothing is written outside draft, validate, eval, publish.
relevant_when:
  - config ui
  - config editor
  - forms
  - categories
  - sessions
  - schedules
  - channels
  - evaluate
related: [001-config-core, 014-config-ui-and-app-mcp, 022-owner-comment-and-new-categories, 023-web-triage-surface]
updated: 2026-11-07
---

# Feature Specification: Config editor forms in the UI

**Feature Branch**: `spec/026-config-editor-forms` (from `dev` at v0.1.5)
**Created**: 2026-11-07
**Status**: Completed
**Input**: Owner request (translated): the config files are raw JSON in the FastAPI UI; build proper UI elements where they can be edited: categories with their sessions, session definitions and instruction files, schedules, channels and routes (secrets only as env references), the other sections, and the new `evaluate` / source discovery configuration the owner will add.
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **Forms edit the draft, nothing else.** A form is another view of a draft file. Saving writes the file into the open draft (`AppServices.edit_file`, the same call as the JSON editor); validate, smoke eval and publish stay the only way into the versioned config (constitution III and IX). The JSON view stays as the escape hatch; both read the draft content when it has one, so an edit made in one view is the starting point of the other.
2. **Schema-driven, not hand-built.** The Pydantic config models already export JSON Schema (`pkpipe schema`). `pkpipe.api.forms` builds the field tree from that schema and parses the posted form back; a new model in `CONFIG_MODELS`, or a new nested field, gets its form without code. Supported shapes: objects, maps (a name per row), arrays of objects (rows), arrays of scalars (one per line), enums, booleans, numbers, strings and the nullable variants. A shape the engine cannot render (a union, an array of arrays) falls back to a JSON text field inside the same form, never to a missing field. Flows (`flows/<id>.json`) keep the JSON view only: a node graph is not a form.
3. **No JavaScript framework, no new host.** Server-rendered Jinja templates and htmx, as the rest of the UI. Row actions (add, remove, move up, move down) are server round trips that re-render the form from the posted values, so half-typed values survive; they work without any script. The only script is a self-hosted `config-forms.js` (instruction token counter, an enhancement). The CSP stays `script-src 'self'`.
4. **Names carry no owner text.** Field names are built from schema property names and row numbers (`f.categories.0.sessions.1.id`); a map row's key is a value in `<row>.__k`. An action path is checked against `f(.name|.number)*` before it is used. Every value, label and message is escaped by Jinja.
5. **The overlay is a layer.** The form shows what the editor shows: the overlay file when it exists, else the pack file. Validation runs on the file **merged onto the pack file** with the registry's merge (`merge_layers`), so a partial overlay is judged as the engine will see it; an error of a merged entry is attached to its row by the merge key (`id`, `flow`). When a section has no overlay file yet, saving writes only what differs from the pack (`forms.minimize`: lists merged by key keep the changed or new entries, dicts keep the differing keys) instead of copying the whole pack file into the overlay, which would shadow later pack updates. A pack entry is removed with `enabled: false`; saving a form that changes nothing writes nothing.
6. **Secrets are never values.** In `channels.json` the fields `url`, `urls`, `token` (and `password`, `secret`, `api_key`) accept `env:NAME` only. The form refuses a literal, the JSON editor refuses it too (`ConfigWorkspace.check_content`), and the engine's own validator already did. A literal secret that exists in a file is never rendered back (the field is empty with a note); the field shows whether the named variable is set, never its value. `channel_id` is not a secret.
7. **Pickers instead of memory.** Flow ids (`subflow`, `flow`), category ids, channel names, notification events and the `env:` names already referenced in the config are offered as lists. A session's `instruction`, `system` and `context` link to the text file they name (`instructions/<value>`, `prompts/system/<value>.md`, `contexts/<value>.md`) when it exists, so a session and its instruction are two clicks apart.
8. **Small widgets where they pay.** `schedules.cron` shows the next three fire times (or the reason the expression is invalid) as the owner types, using the scheduler's own `CronTrigger.from_crontab`. An instruction text (`instructions/**.md`) shows its length as about four characters per token against the 800 token budget of design rule 4.2: a hint, not a gate. Booleans are three-valued (default, true, false) so that leaving a key unset stays possible.
9. **Spec 025 and later sections.** The `evaluate` sessions and the `source_discovery` configuration that spec 025 introduces are ordinary config (a section in `CONFIG_MODELS` or fields of a category); they get their form from the schema. A test registers a demo model and checks that its form appears and round-trips.
10. **Status: Completed (2026-11-07).** Gate 2: tasks done, suite green, docs updated. No BPMN page and no C4 view change: the UI gains views of an existing function, no process step and no component.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Edit a section as a form (Priority: P1)
**Independent Test**: open `/config`, follow *form* of `schedules.json`, change a cron expression, save; the draft holds the change and the JSON view shows it.
**Acceptance**:
1. **Given** a pack section without an overlay file, **When** the owner changes one field, **Then** the draft file contains only the changed entry; **and when** nothing changed, **Then** nothing is written.
2. **Given** a wrong value (a word in a number field, an enum outside its options), **Then** the error is shown at that field and the draft is unchanged.
3. **Given** the whole draft has a problem the single file does not show (an unknown route target), **Then** the form says the draft is not valid yet and names it.

### User Story 2 - Categories, sessions and their instructions (Priority: P1)
**Independent Test**: add a category with rows through the form, reorder the sessions, follow the link of an instruction to its text editor.
**Acceptance**:
1. **Given** the categories form, **Then** every category shows its fields and its session rows; rows can be added, removed and moved.
2. **Given** a session whose instruction file exists, **Then** the field links to it; **and** the text editor of an instruction shows the token estimate against 800.
3. **Given** the owner adds an `evaluate` session (spec 025) in the draft, **Then** it appears as a row with the same fields.

### User Story 3 - Channels and routes without secrets (Priority: P1)
**Acceptance**:
1. **Given** a channel, **When** a literal webhook URL or token is typed, **Then** the form and the JSON view refuse it and the value is not echoed into any other page.
2. **Given** `env:NAME`, **Then** it is accepted and shows *set* or *not set*.
3. **Given** the routes map, **Then** events and channel names are offered and unknown channels are reported by the validator.

### User Story 4 - Schedules with a preview (Priority: P2)
**Acceptance**: a cron expression shows the next runs while typing; an invalid one shows the parser's reason; an unparsable value cannot be saved.

## Requirements *(mandatory)*

- **FR-001**: A form for every config section in `CONFIG_MODELS` (and none for flows); the `/config` file list links each section to *form* and *JSON*.
- **FR-002**: The form is generated from the section's JSON Schema; unknown shapes fall back to a JSON field in the same form.
- **FR-003**: Add, remove, move up and move down on arrays of objects and maps, working without script and keeping typed values.
- **FR-004**: Per-field errors from the section's model validated on the merged layers; errors of merged entries land on their row; errors that fit no field show at the top of the form.
- **FR-005**: Saving goes through `edit_file` into the open draft; no other write path to the config exists; both views share the draft.
- **FR-006**: `channels.json` secrets accept `env:NAME` only (form and JSON editor); literals are never rendered; set or not set is shown, the value never.
- **FR-007**: A section without an overlay file saves the minimal overlay; unchanged forms write nothing.
- **FR-008**: Pickers for flows, categories, events, channels and env names; links from a session's `instruction`, `system`, `context` to their text files.
- **FR-009**: Cron preview with the scheduler's parser; instruction token hint against 800.
- **FR-010**: Owner token, CSRF header and same-origin check on every state-changing form request; Jinja autoescape; no inline script, no external host; labels, `aria-label` on row buttons, errors linked with `aria-describedby`, state never by colour alone.

## Success Criteria

- **SC-001**: Every pack config file round-trips through its form to an equal model (test per section).
- **SC-002**: A new config model gets a form with no template or route change (test).
- **SC-003**: No literal secret is accepted in `channels.json` by either view; no stored literal appears in any rendered page (tests).
- **SC-004**: Script, markup and attribute injection through a label, hint or key stays inert (test); the pages still load only local assets.
