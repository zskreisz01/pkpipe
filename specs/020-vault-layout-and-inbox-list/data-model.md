---
spec_id: 020-vault-layout-and-inbox-list
status: Draft
description: Data model for the vault layout and the inbox list
updated: 2026-11-04
---

# Data Model

**VaultLayout** (policy `layout`): `inbox_dir`, `inbox_list_file` (optional), `triage_file`, `processing_list`, `archive_dir`, `inbox_archive_pattern`, `agent_tasks_dir`, `tool_candidates_file`, `improvement_file`, `quarantine_dir`, `journal_pattern`, `attachments_dir`. All relative; defaults = current constants.

**InboxListConfig**: `bullet_markers` (`["-", "*"]`), `header_end` (`^---\s*$`), `strip_params` (`utm_*`, `fbclid`, `gclid`, `igshid`, `mc_*`), `comments_ignored` (true).

**InboxEntry**: `raw` (exact lines), `kind` (`link` | `image` | `text_link` | `text`), `resource`, `title`, `line`.

| Entry | kind | resource | title |
| --- | --- | --- | --- |
| `- https://a.b/x?utm_source=fb` | link | `https://a.b/x` | (empty) |
| `- ![[FB_IMG_1.jpg]]` | image | `FB_IMG_1.jpg` | file name |
| `- Title` + next line URL | text_link | the URL | `Title` |
| `- some text` | text | normalised text | the text |

Item id = `sha256(resource)[:12]` (unchanged). Row fields added to the triage file: `kind`, `needs_owner`.
