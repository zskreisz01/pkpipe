# Spec-Driven Development Conventions – pkpipe

pkpipe uses spec-kit (github/spec-kit) conventions, extended with an optional `research.md` and `data-model.md` per spec.
The constitution (`.specify/memory/constitution.md`) is binding; templates are in `.specify/templates/`.

## Spec directory

```
specs/###-short-name/
├── spec.md          required – user stories, FR, SC, clarifications
├── plan.md          required – technical context, constitution check, structure
├── tasks.md         required – phased task checklist
├── research.md      optional – technology choices, alternatives, spikes
├── data-model.md    optional – entities, schemas, files, ER diagram
└── contracts/       optional – MCP tool or HTTP contracts
```

Add `research.md` when the spec chooses between technologies or approaches; add `data-model.md` when it introduces
or changes a persisted or exchanged structure (SQLite table, JSON schema, vault file format, MCP payload).

## Front matter (agent-readable)

Every spec file starts with YAML front matter (`spec_id`, `title`, `status`, `status_reason`, `description`,
`relevant_when`, `blocked_by`, `superseded_by`, `related`, `updated`).

**Agent reading protocol**

1. Read only the front matter of every spec first (`head -15 specs/*/spec.md`).
2. Load a body only if `description` / `relevant_when` match the task and the status is not `Obsolete`.
3. For `On-hold`, also read the `blocked_by` spec's header and tell the user the spec is paused.
4. Load companion files (`research.md`, `data-model.md`, `contracts/`, `tasks.md`) only when the task needs them.

## Status lifecycle

`Draft → Active → Completed`, plus `On-hold` (paused, `blocked_by` required) and `Obsolete` (`superseded_by` or
dropped). Specs are never deleted. Status changes update the front matter, the spec's Clarifications and `specs/README.md`.

## Branches and PRs

| Convention | Format |
| --- | --- |
| Branch | `spec/###-short-name` |
| PR title | `spec/###: <summary>` |
| Commits | Conventional Commits, referencing the task id (e.g. `feat(triage): release rule (T012)`) |

## Gates

| Gate | Transition | Check |
| --- | --- | --- |
| Gate 1 | Draft → Active | Clarifications closed or deferred; every user story has an independent test; Constitution Check filled |
| Gate 2 | Active → Completed | Tasks done; CI green; eval gates passed; docs, BPMN image and Mermaid updated |
| Gate 3 | Any → On-hold / Obsolete | Reason and `blocked_by` / `superseded_by` recorded; index updated |
