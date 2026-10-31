# Contributing

- Python tooling: `uv sync`, `uv run pytest`, `uv run ruff check`.
- The core (`src/pkpipe/engine`, `domain`) may only depend on `ports`. New integrations are adapters.
- Every behaviour change to a step needs a dataset entry in `evals/` and must keep the eval gate green.
- Never commit personal data, contexts or real journals. Examples must be synthetic.
- Docs: `cd website && npm install && npm start` (Docusaurus).
