---
spec_id: 026-config-editor-forms
status: Completed
description: Data model of the config forms (field tree, field names, actions)
updated: 2026-11-07
---

# Data Model

No persisted structure changes: a form writes the same draft file content (2-space JSON, UTF-8, trailing newline) the JSON editor writes.

**Field tree** (`forms.Node`): `kind` (`object`, `map`, `array`, `lines`, `string`, `text`, `integer`, `number`, `boolean`, `enum`, `json`), `name` (form field), `label`, `help`, `value`, `required`, `nullable`, `default`, `enum`, `children` (object properties, or the rows of an array or map), `error`, `hint` (picker), `secret`, `widget` (`cron`, `asset`).

**Field names**: `f` is the section root; `f.<property>` below an object; `f.<n>` the n-th row of an array or map; a map row's key is `f.<...>.<n>.__k`. Booleans and enums post `""` for "default / unset".

**Parsing rules**: an empty optional value is left out; an empty optional object, list or map is left out; numbers must parse; booleans are `true` / `false` / unset; a free JSON field must be valid JSON; map keys must be present and unique; `channels.json` secrets must match `^env:[A-Za-z_][A-Za-z0-9_]*$`.

**Actions** (posted as `act`): `add:<path>`, `del:<path>:<row>`, `up:<path>:<row>`, `down:<path>:<row>`; `<path>` matches `f(.name|.number)*`.

**Asset links**: `instruction` -> `instructions/<value>`; `system` -> `prompts/system/<value>.md`; `context` -> `contexts/<value>.md`.

**Endpoints** (owner session, CSRF header): `GET /config/form?path=<section>.json`, `POST /ui/config/form/save`, `POST /ui/config/form/action`, `GET /ui/config/cron-preview?f.…=<expr>`.
