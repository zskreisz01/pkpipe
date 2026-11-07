---
spec_id: 022-owner-comment-and-new-categories
status: Completed
description: Data model for the owner comment column and new-category proposals
updated: 2026-11-06
---

# Data Model

**Triage table** (columns): `confirm`, `item`, `resource`, `suggested`, `override`, `state`, `kind`, `needs_owner`, `topic`, `comment`. Older tables have fewer trailing columns and are widened on the next write.

**TriageRow** (additions): `comment` (str, owner-owned, never written by the pipeline). `topic`: pipeline-owned for image rows (spec 021), reserved and preserved as found for other rows.

**ItemState** (addition): `awaiting_category`: a new-category proposal was written; left only by the owner (override naming a configured category, or quarantine). Transitions: `suggested | pending | released` to `awaiting_category`; `awaiting_category` to `released | pending | quarantined | awaiting_category`.

**resolve_category(value, categories)**: `("exact" | "normalised", id)` for a configured id or a variant of an id or label (case, spaces, underscores, hyphen runs), `("new", slug)` for a valid slug that names nothing configured (`[a-z0-9]` and single hyphens, 2 to 40 characters), `("invalid", None)` otherwise.

**Proposal note** (below `layout.proposals_dir`, `new-category-<slug>-<item id>.md`): front matter `type: agent_task`, `kind: new_category`, `status: proposed`, `category_id`, `pkpipe_item`; body: the requested name, the title and note of the `misc.evaluate` session (neutralised), a `categories.json` entry draft in a code block, how to publish it.

**proposal_attempts** (app DB table, FR-009): `item_id` (primary key), `requested` (slug), `attempts` (failed evaluations), `updated_at`. `get` is 0 for another requested name; `bump` after a failed evaluation; `clear` after a written note. `MAX_PROPOSAL_ATTEMPTS = 3` (`domain/triage.py`).

**Vault tool `update_triage`**: `rows: []` is the maintenance call (migration only). A row naming `comment` is refused (existing rows: a changed value; new rows: any value).
