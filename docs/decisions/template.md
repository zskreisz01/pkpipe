---
title: "ADR template and spike checklist"
---

# ADR template and spike checklist

An architecture decision record (ADR) is one short page in `docs/decisions/`, numbered `NNNN-<slug>.md`. A **system tool candidate** (spec 016) is adopted only with an ADR; `pkpipe candidates adr <tool> --ab-report <report>` writes the first draft from the candidate list and the A/B report, the owner edits the prose and sets the status to *Accepted*.

## Template

```markdown
---
title: "NNNN <Decision in a few words>"
---

# NNNN: <Decision>

- **Status:** Proposed | Accepted | Superseded by NNNN, <date>
- **Context:** the specs or issues this decides
- **Candidate:** <candidate id> (evaluation note: <vault path>)
- **A/B verdict:** better (gate green: true)

## Context

What is wrong or missing, and which component is affected.

## Decision

What is adopted, behind which adapter, and how it is switched on (configuration, not code, where possible).

## Evidence (A/B on the eval suite)

The metrics and the pairwise judge score from the A/B report (`pkpipe candidates ab`): current component against the candidate on the same datasets.

## Consequences

Effort, licence, what must be maintained, how to roll back.
```

## Spike checklist

A spike tries the candidate behind the matching adapter; nothing in the main configuration changes until the A/B is won. Mark each step in the candidate's row (status `investigating`, then `trial`).

1. **Read the evaluation note** of the candidate: component, benefit, effort, licence (no Meta/Llama models, no tool that needs an account with a third party for local data).
2. **Find the port.** The tool must fit behind an existing port or MCP server; if it needs a new port, say so in the ADR (the core depends only on ports).
3. **Build the spike as a configuration** (a second pack or an overlay directory) and, only when needed, an adapter with a fake in `tests/fakes/`. Do not touch the main configuration.
4. **Run the A/B:** `pkpipe candidates ab <tool> --candidate-overlay <spike overlay>` (or `--candidate-pack`). It runs the same eval suites on the current and the candidate configuration and compares gated metrics and the judge's pairwise preference.
5. **Read the verdict.** `better` = gate green, no gated metric worse, and an improvement or a judge preference. `equal` and `inconclusive` do not adopt; `worse` is a reason to reject.
6. **Write the ADR:** `pkpipe candidates adr <tool> --ab-report ab-<tool>.json`, edit it, accept it.
7. **Decide:** `pkpipe candidates set <tool> --status adopted --adr <file> --ab-report <report>` (refused without both), or `--status rejected --reason "..."`. A rejected tool is not proposed again without new evidence (`--status candidate --evidence "..."`).
8. **Publish the configuration change** through the config UI so the eval gate runs on the real change.
