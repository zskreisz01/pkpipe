# pkpipe – Claude Code guide

pkpipe (Personal Knowledge Pipeline) is a public, local-first agent pipeline: Obsidian INBOX → triage → guarded LLM
sessions → curated notes. Repository: github.com/zskreisz01/pkpipe.

## Read first
- `.specify/memory/constitution.md` – binding principles (I–XII).
- `.claude/instructions/spec-conventions.md` – how specs work; read spec front matter before bodies.
- `specs/README.md` – spec index and implementation order.
- `docs/` – architecture (C4), process model (BPMN images + Mermaid), configuration, security, evaluation, operations.

## Hard rules
- Public repo: no personal data, no real contexts, no client names. Everything private lives in a separate overlay repo.
- Core depends only on ports; integrations are adapters with fakes in `tests/fakes/`.
- Raw external content only reaches sessions without tools.
- Python tooling: always `uv` (`uv sync`, `uv run pytest`, `uv run ruff check`), never pip/venv.
- A process change updates the BPMN page (`docs/design/pkpipe-process-model.drawio`), its PNG export and its Mermaid block together.
- Ask before publishing actions (push, release, GitHub settings).
- English for code, docs, specs and commits (Conventional Commits).
