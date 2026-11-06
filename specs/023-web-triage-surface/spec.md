---
spec_id: 023-web-triage-surface
title: Web triage surface, owner note to the sessions, one place for the triage data
status: In Progress
status_reason: Implementation started 2026-11-06 on branch spec/023-web-triage-surface (from spec/022, unreleased)
description: The web UI becomes the primary decision surface for triage (images, all fields, filters, bulk actions, new-category proposals, comment); the owner's `comment` is owner input and reaches the sessions as the declared input `owner_note`; `triage.surface` (`vault` default, `web`) decides where the machine-owned table lives, so the data is edited in one place only; the UI is reachable over Tailscale (public_url, allowed_hosts); an English user guide.
relevant_when:
  - triage
  - web ui
  - tailscale
  - owner note
  - comment
  - surface
  - user guide
related: [007-triage-and-release, 010-category-sessions, 014-config-ui-and-app-mcp, 019-vault-snapshots, 020-vault-layout-and-inbox-list, 021-image-input, 022-owner-comment-and-new-categories]
updated: 2026-11-06
---

# Feature Specification: Web triage surface

**Feature Branch**: `spec/023-web-triage-surface` (from `spec/022-owner-comment-and-new-categories`, which is unreleased: this spec amends it where it contradicts)
**Created**: 2026-11-06
**Status**: In Progress
**Input**: Owner decisions (translated): the web UI is the primary place to decide; the Obsidian file stays a readable fallback; the comment is the owner's input and the sessions may use it; the data lives in one place; the UI is reached over Tailscale; an end-user guide.
**Companions**: [data-model.md](data-model.md)

## Clarifications

1. **The web UI is the primary decision surface (spec 014 `/triage`).** It shows every field of a row (image thumbnail, topic, kind, state, `needs_owner`, suggested category, confirm, override, comment), filters by state, category and `needs_owner`, offers bulk actions on the filtered set, and keeps the quarantine release. Every write goes through `AppServices` and the vault library with the actor `ui`: the rules of spec 007, 014 and 022 hold server side (override is a configured id or a valid new-category slug; confirm only in an allowed state).
2. **`comment` is owner input, not a secret from the models (reverses 022 clarification 2).** The owner writes it to direct the work, so it is *trusted as intent but handled as data*: it reaches a model only as the declared input `owner_note` (capped at 1000 characters, neutralised of control characters, datamarked like every input, never in a system prompt) of (a) the `misc` `evaluate` session of a new-category proposal together with the requested name, and (b) the sessions of the category plan at release time that declare `item.owner_note`. It is never an input of the vision, journal or context flows (private data classes), never in trace content capture (the capture view of a prompt replaces the block by a marker), never in a log, a notification or a span attribute. A session that has a web search tool receives it, but every query it proposes still passes the query guard (`security.query_guard`) before it leaves the machine: the guard checks all string arguments, so a blocked term the owner typed into a comment and the model copied into a query is refused (tested with a canary term). The vault MCP tool `read_triage`, the app JSON API and the UI expose `comment` (the code steps that route the note need it); the app MCP `list_triage` returns it for the owner token only, never for the agent token.
3. **Override reasons feed the eval datasets.** When an owner override differs from the suggestion, a candidate row with the comment as `reason` is kept in `<overlay>/evals/candidates/triage.jsonl` (the private overlay, else the data directory; never the public repo). The owner reviews candidates weekly before they join a dataset (design rule 6.4).
4. **One place for the data: `triage.surface`.** `vault` (default, nothing changes): the table is in `layout.triage_file`. `web`: the machine-owned table lives in `layout.triage_data_file` (default `0-INBOX/_pkpipe/triage-data.md`, validated like the other layout paths, inside the vault policy allowlists of `read_triage` / `update_triage`, in the snapshot scope) and `layout.triage_file` is a generated page: front matter, title, the link to the web UI (`ui.public_url`) and a short summary (counts per state, how many await the owner, how many new-category proposals, last triage run), rewritten on each run and each UI decision, **with no table**, so there is nothing to edit in two places. The owner switches by changing config in the overlay (`triage.json`, mirrored in the vault policy file as `triage_surface`); `pkpipe validate` checks that both agree and that `web` has a `ui.public_url`.
5. **Migration in both directions, lossless, on the next triage write.** The table moves as text: the table block (header, separator, every row including rows that do not parse, stray `[ x]`, image embeds `![[x.jpg\|200]]`, any column count) is moved verbatim, so every cell survives. The target is written first, then the source is replaced (page or tombstone note), under the file locks, with a re-plan when the source changed in between. Idempotent: afterwards the source holds no table. If both files hold a table (an interrupted migration), the surface file wins and the other file contributes only the rows it has that the surface lacks. Until the move, reads and owner decisions use the file that holds the table, so a switch never shows an empty table. The move runs inside the writing triage flow (so the vault snapshot of spec 019 is taken first) as the maintenance call; its plan (direction, row count, files) is logged before it is applied. Prose the owner added to the retired page is kept below the table in the data file; going back to `vault` leaves a tombstone note in the data file (no deletion).
6. **Tailscale.** `check_bind` already allows loopback or a Tailscale address and the token is mandatory. New: `ui.json` has `public_url` and `allowed_hosts` (MagicDNS name, IP) that extend the Host allowlist (DNS rebinding); token, CSRF and Origin checks are unchanged. Two supported setups are documented: bind to the Tailscale IP, or bind to loopback and `tailscale serve`; **never Funnel**. A launchd template for `pkpipe serve` is in `deploy/`.
7. **Images in the UI.** A UI endpoint serves a thumbnail of an image row from the read-only attachments folder through the same `read_image` path (name only, allowlisted folder, size limit, signature check) and, where Pillow is available, the prepared bytes (scaled, metadata stripped); it needs the owner login, never fetches a URL, and sends `nosniff`, `no-store` and a restrictive CSP.
8. **User guide.** `docs/user-guide.md` (English, in the website sidebar): the daily flow, every triage field and who writes it, `needs_owner`, the states, images and the 2 h budget, new categories, the comment, restore and snapshots, the two surfaces, Tailscale access. No private data.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Decide in the browser (Priority: P1)

**Acceptance Scenarios**:

1. **Given** triage rows, **When** the owner opens `/triage`, **Then** every field shows, images render as thumbnails, `needs_owner` rows are highlighted and the filters and bulk "confirm all suggested" act on the filtered set only
2. **Given** a row, **When** the owner picks a configured category or types a new-category slug, **Then** the override is stored (new slug: the row proposes a category on the next 06:00 run); an invalid value is refused server side
3. **Given** an `awaiting_category` row, **Then** the page shows it with the link to its proposal note path
4. **Given** a comment edited in the UI, **Then** it is stored byte for byte and shown again

### User Story 2 - The note directs the work (Priority: P1)

1. **Given** a released item with a comment, **When** its category plan runs, **Then** a session that declares `item.owner_note` receives it as a datamarked, capped input, and a session that does not declare it does not
2. **Given** a proposal, **Then** the `evaluate` session receives the requested name and the note
3. **Given** a web search session, **When** the model copies a blocked term from the note into a query, **Then** the call is blocked
4. **Given** trace content capture on, **Then** the captured prompt has no note text; the vision, journal and context flows never receive one

### User Story 3 - One place for the table (Priority: P1)

1. **Given** `surface: vault`, **Then** behaviour is as before spec 023
2. **Given** `surface: web` and a table in the page, **When** the triage flow runs, **Then** every row moves to the data file unchanged and the page has the summary, no table; the second run changes nothing but the run time
3. **Given** the reverse switch, **Then** the table is back in the page, the data file is a tombstone
4. **Given** the real header variants (stray `[ x]`, embeds, 6, 9, 10 columns, an unknown 11th), **Then** no cell is lost

### User Story 4 - Reach the UI over Tailscale (Priority: P2)

1. **Given** `ui.allowed_hosts` with a MagicDNS name, **Then** a request with that Host passes the guard, an unlisted Host is refused (421); token, CSRF and Origin checks are unchanged

### User Story 5 - A guide for the owner (Priority: P2)

## Requirements *(mandatory)*

- **FR-001**: `layout.triage_data_file`, `triage.json` (`surface`), vault policy `triage_surface`, `ui.json` (`public_url`, `allowed_hosts`); consistency checks in `pkpipe validate`
- **FR-002**: the vault library resolves the file that holds the table, renders the summary page in `web` mode, and migrates in both directions verbatim and idempotently
- **FR-003**: `owner_note` flows from `comment` to the sessions only as specified in clarification 2; `read_triage` exposes `comment`
- **FR-004**: UI and API: all fields, thumbnail endpoint, filters, bulk confirm, override dropdown plus new-category slug input, comment edit; every write through the services, audited
- **FR-005**: override candidates with the comment as reason
- **FR-006**: `ui.allowed_hosts` and `ui.public_url`; launchd template; Tailscale docs
- **FR-007**: `docs/user-guide.md`

## Success Criteria

- **SC-001**: Migration of a live-shaped table (about 54 confirmed rows, image embeds, stray blanks) in both directions loses no cell and is idempotent
- **SC-002**: A canary string in a comment appears in no trace capture, log, notification or span, and in no session that does not declare `item.owner_note`
- **SC-003**: A query copied from a comment with a blocked term never reaches a tool
