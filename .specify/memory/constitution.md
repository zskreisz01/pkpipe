# pkpipe – Personal Knowledge Pipeline – Constitution

> Spec-kit constitution for the `pkpipe` repository. Every spec's `plan.md` "Constitution Check" refers to these
> principles. Source: the design documentation in `docs/` and the process model `docs/design/pkpipe-process-model.drawio`.

**Version**: 1.1.0 | **Ratified**: 2026-10-31 | **Last Amended**: 2026-10-31

## Core Principles

### I. Local-first, model-agnostic, deployable anywhere
pkpipe is a blueprint for personal knowledge and novelty pipelines, not a single installation. The reference deployment
is local-first: data stays on the owner's machine and the agents use a local model. The same engine must also run in a
cloud deployment (for example Microsoft Foundry with Azure-hosted models, storage and monitoring) by changing
configuration and adapters only. The model is reached only through an OpenAI-compatible API (LM Studio, Ollama, vLLM,
Microsoft Foundry or another cloud endpoint); no core code depends on a specific vendor SDK, model family or hosting
environment. External services (search, learning backend, notification channels, trace backend, storage) are optional
and replaceable by configuration. A choice that only works locally, or only in one cloud, belongs in an adapter.

### II. Hexagonal core (NON-NEGOTIABLE)
The core (`pkpipe.engine`, `pkpipe.domain`, `pkpipe.steps`) imports only `pkpipe.ports` and the standard library,
LangGraph and Pydantic. Every integration is an adapter that implements a port; every port has an in-memory fake in
`tests/fakes/`. A dependency arrow that points outwards from the core is a defect.

### III. Configuration over code
Flows, categories, session plans, instructions, schedules, channels and guard thresholds live in a layered, versioned
config (core defaults → public pack → private overlay). Every JSON file has a schema generated from Pydantic models. A
new category or session must not require a code change. Secrets are referenced (`env:NAME`), never stored in config.

### IV. Untrusted content isolation (NON-NEGOTIABLE)
All external content (web pages, search results, INBOX items, learning-service output) is untrusted data. Raw external
text only reaches sessions without tools and without write access (Reader). Sessions with tools see only validated
JSON. Untrusted text is always wrapped in random delimiters (datamarking). Every output derived from external content
passes the output guard before it is written to the vault.

### V. Code decides, models propose
Gates, flags, state transitions, file writes and paths are deterministic code. A model may suggest a category, a
verdict or a note; it never decides to stop a process, release an item or skip a guard. A security flag is raised by
code from classifier scores, schema fields and canary checks.

### VI. Human in the loop
The owner confirms or overrides triage, releases quarantined items, decides weekly improvement verdicts and adopts
system tool candidates. The agent may only create items in a `proposed` / `suggested` state. Unreviewed items stay
pending; nothing is processed by default.

### VII. Public / private separation (NON-NEGOTIABLE)
This repository is public. Personal data (journals, context packs, real INBOX items, private datasets, client names)
never enters it, its docs or its CI logs. Examples and datasets are synthetic. Private categories, instructions,
contexts and datasets live in a private overlay outside this repository.

### VIII. Small, isolated sessions
Every LLM step is one session with a fresh message list: a short fixed system prompt, the category instruction in the
user prompt (about 800 tokens at most), at most one context pack, only the declared inputs, and a JSON-schema output.
Long, bundled skill documents are not used inside flows.

### IX. Measured change
Every behaviour change has tests; every LLM step has an eval dataset. A change to a prompt, instruction, model or
guard can only be published when the affected eval passes its gate. Thresholds only move upwards (regression gate).

### X. Observable by default
Every flow, step, LLM call, tool call and HTTP call produces OpenTelemetry spans with the attributes `flow.id`,
`step.id`, `bpmn.element`, `item.id`, `item.category`, `config.version`. Only OTLP is exported. Content capture is
configurable and off for private data.

### XI. Design as diagram and as code
The process model (BPMN, draw.io) and the C4 views are the design. Every diagram is kept in two forms: an exported image
for humans and a Mermaid block for agents. A behaviour change that alters a process updates the BPMN page, the image and
the Mermaid block in the same pull request.

### XII. Tooling and language
Python 3.12+, `uv` for every Python command, `ruff` for lint and format, `pytest` for tests, Docusaurus for docs.
Code, docs, specs, commits (Conventional Commits) and issues are in English.

## Spec-driven development

- Non-trivial work starts with a spec in `specs/###-short-name/` (see `.claude/instructions/spec-conventions.md`).
- `spec.md`, `plan.md` and `tasks.md` are required; `research.md` and `data-model.md` are added when the spec involves a
  technology choice or a persistent / exchanged data structure.
- Branch: `spec/###-short-name`; PR title: `spec/###: <summary>`.

## Governance

- The constitution is above the specs. A deviation is allowed only in `plan.md` "Complexity Tracking", with reason.
- Amendment: PR + version bump (MAJOR: principle removed or redefined, MINOR: new principle, PATCH: clarification).
- Gate 1 (Draft → Active): every `[NEEDS CLARIFICATION]` resolved or explicitly deferred; every user story has an
  independent test; Constitution Check filled in.
- Gate 2 (Active → Completed): all tasks done, CI green, eval gates passed, docs and diagrams updated (XI).
