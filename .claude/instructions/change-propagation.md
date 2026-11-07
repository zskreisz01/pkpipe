# Change propagation – where a change must be carried through

A change that is "done" in the code but not in the places below is not done (constitution XI, spec conventions).
Use this table when you plan a change, and run the `consistency-checker` agent before you ask for a PR.

| If the change touches… | …then update, in the same PR |
|---|---|
| Behaviour of a flow (nodes, edges, a step's contract, a state, a column) | `packs/*/flows/*.json` (incl. `bpmn` / `bpmn_element` names) · BPMN page in `docs/design/pkpipe-process-model.drawio` · its PNG in `docs/img/bpmn/<page>.png` · the Mermaid block in `docs/process/<page>.md` (see `docs/bpmn.md` for the export command) · `docs/flows.md` |
| Component structure, a port, an adapter, a new process or service | C4 views `docs/design/pkpipe-c4.drawio` · PNGs in `docs/img/c4/` · Mermaid in `docs/architecture.md` · `tests/unit/repo/test_architecture.py` rules |
| Config keys, defaults, schemas, new overlay files | JSON schema / validator in code · `packs/*` defaults · `docs/configuration.md` · `docs/glossary.md` when a term is new |
| Security behaviour (guards, canary, quarantine, untrusted data, UI auth) | `docs/security.md` · constitution principle if binding · security eval datasets and `tests/` for the leak/flag cases |
| Operator-facing behaviour (UI, notifications, schedules, launchd) | `docs/user-guide.md` / `docs/owner-manual.md` · `docs/operations.md` · `deploy/launchd/*.example` |
| Evaluation (suites, thresholds, datasets) | `docs/evaluation.md` · eval suite files · cassettes re-recorded when prompts change |
| Any spec-governed change | `specs/###-*/spec.md` (FR/SC, Clarifications), `plan.md`, `tasks.md`, `data-model.md` when a persisted structure changes · front matter `status`/`updated` · `specs/README.md` index · an ADR in `docs/decisions/` for an architectural choice |
| A release | version in `pyproject.toml` **before** tagging (the tag and `pkpipe --version` must agree) · CHANGELOG/release notes · `pkpipe-priv` pin (`pyproject.toml`, `uv.lock`) |
| Process-model change that also exists privately | `pkpipe-priv/docs/design/pkpipe-process-model-private.drawio` and `pkpipe-priv/docs/design-rules.md` (private repo; never copy personal data into this repo) |

## Rules of thumb
- The drawio is the source; PNG and Mermaid are derived and must not be older than it.
- Names must match across flow JSON `bpmn_element`, the drawio labels and the Mermaid nodes.
- A new state, column or error path appears in code, tests, docs, the BPMN page and the user guide, or in none.
- Public docs stay free of personal data and client names.
