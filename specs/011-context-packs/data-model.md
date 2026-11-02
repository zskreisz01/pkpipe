---
spec_id: 011-context-packs
title: Context packs and weekly refresh
status: Completed
description: Files and structures of the context packs, their refresh scopes, the proposal and review files and the query guard terms file.
updated: 2026-11-02
---

# Data Model: 011-context-packs

**Spec:** [spec.md](spec.md)

Notation: **NEW** = introduced by this spec · **EXISTING** = already in the codebase.

## Entities

### NEW `ContextPack` (`<overlay>/contexts/<pack>.md`)

```
<!-- pkpipe:context-pack sha256=<first 16 hex of sha256(generated part)> -->
# Enterprise context (anonymized)
...generated part...
<!-- pkpipe:manual -->
...text of the owner, never rewritten...
```

| Part | Meaning |
| --- | --- |
| first line | Hash of the generated part at the time it was written. A different hash (or no line: a hand-written file) means the owner edited the generated part |
| generated part | Title, overview, `## Projects` (personal) or `## Programmes` (enterprise): one line per project with name, status, summary, stack (and constraints for enterprise), within the scope's token budget |
| `<!-- pkpipe:manual -->` | Optional. Everything below it is carried over on every refresh |

Sessions receive the pack without the `<!-- pkpipe: ... -->` lines. `system.md` is a plain Markdown file of the public pack and is never generated.

### NEW `ContextPack` proposal (`<overlay>/contexts/<pack>.md.proposed`)

The new version of a pack whose generated part the owner edited (same format, the owner's manual part included). Not an asset (suffix `.proposed`), removed by the next refresh that can write the pack. Accept it with `mv`.

### NEW `Scope` (`args.scopes` of node `scopes`, flow `context_refresh`)

| Field | Type | Description |
| --- | --- | --- |
| `id` | str | `[a-z][a-z0-9_]*`, unique |
| `kind` | `personal` or `enterprise` | Picks the session and the anonymization |
| `folder` | str | Relative vault folder with the project notes |
| `pack` | str | `[a-z][a-z0-9_-]*`, unique, not `system`; default `id` |
| `max_tokens`, `max_notes`, `max_chars` | int | Budget of the pack, notes read (at most 60), characters sent to the session |

### NEW session outputs (`schemas/context_personal.json`, `context_enterprise.json`)

`overview: str`, `projects: [{name, status?, summary, stack: [str], constraints?: [str] (enterprise)}]`, and for enterprise `names: [str]`: every private name as the notes write it.

### NEW `BuiltPack` (item key `built`, checkpointed)

`{name, kind, body, tokens, projects, scrubbed, trimmed, dropped, terms: [{term, kind}], warnings, empty}`; after `context.write`: `status` (`created`, `updated`, `unchanged`, `proposed`), `terms_added`, `review_path`, `review_body`, without `body` and `terms`.

### NEW review note (`<vault>/2-Areas/Context-Review/<pack>.md`)

Front matter `type: context_review`, `pack`, `status`; body: what happened and a `diff` block (current to new). Overwritten by the next refresh that changes the pack; for the enterprise pack the diff holds no private name.

### EXISTING `QueryGuard` terms file (`security.query_guard.terms_file`, default `security/query_guard_terms.txt`)

One term per line, `#` comments. The refresh appends `# added by context_refresh on <day>` and the new names in every form (full name, short form, last name); lines are never removed or reordered. The overlay's copy starts from the pack's file.

### NEW port `ContextStorePort`

`read(relative)`, `write(relative, text)`, `remove(relative)` on `contexts/` and `security/` of the overlay. Adapter `FileContextStore` (atomic writes), fake `FakeContextStore`.

### NEW eval case (`evals/datasets/context_leak.jsonl`)

`{id, input: {notes: [{path, front_matter, body}]}, expected: {forbidden: [str], must_include: [str]}, source: synthetic}`.
