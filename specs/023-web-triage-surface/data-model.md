---
spec_id: 023-web-triage-surface
status: In Progress
description: Data model for the web triage surface
updated: 2026-11-06
---

# Data Model

**Config**
- `layout.triage_data_file` (str, `.md`, relative, default `0-INBOX/_pkpipe/triage-data.md`): where the table lives in `web` mode; distinct from the other managed files, not in the quarantine folder, allowed for `read_triage` and `update_triage`, part of the snapshot scope.
- `triage.json`: `{"surface": "vault" | "web"}` (default `vault`). The vault policy file mirrors it as `triage_surface`; `pkpipe validate` fails when they differ.
- `ui.json`: `{"public_url": str | null, "allowed_hosts": [str]}`. `public_url` is an `http(s)` URL without credentials; `allowed_hosts` are host names or IPs (no scheme, port or path). Both extend the Host allowlist of the UI; `web` mode needs a `public_url`.

**Triage page (web mode)**: preserved front matter (or `pkpipe: triage-summary`), `# Inbox triage`, a link line to `<public_url>/triage`, a summary block (counts per state, awaiting the owner, new-category proposals, `Last triage run`). No table.

**Data file (web mode)**: a note with a short header and the triage table (the columns of spec 022).

**Tombstone** (`vault` mode, data file after the move back): a note that says where the table is. No table.

**Migration plan** `{direction: "to_web" | "to_vault", rows: int, invalid: int, source: path, target: path}`; logged, returned by the maintenance call as `migration`.

**TriageRow**: unchanged. **Item** (flow): `owner_note` (str, at most 1000 characters) on released items and proposals when the row has a comment.

**Session input** `item.owner_note`: declared by a session in `categories.json`; datamarked; absent when the item has no note; never offered to a flow of a private data class.

**Override candidate** (`<overlay>/evals/candidates/triage.jsonl`): `{id: "override-<item id>", source: "owner_override", input: {resource}, expected: {category}, ai_suggested, reason, day}`; upserted by id.

**UI routes** (owner login or owner token): `GET /triage` (filters `state`, `category`, `needs_owner`), `POST /ui/triage/{id}/confirm|override|comment|release`, `POST /ui/triage/bulk-confirm`, `GET /ui/triage/{id}/image`; JSON: `GET /api/triage`, `POST /api/triage/{id}/comment`.
