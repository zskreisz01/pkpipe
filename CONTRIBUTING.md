# Contributing

- Python tooling: `uv sync`, `uv run pytest -n auto -m 'not live'` (parallel), live tests serially with
  `PKPIPE_REQUIRE_LIVE=1 uv run pytest -m live` after `scripts/lmstudio-test-setup.sh` ([tests/README.md](tests/README.md)),
  `uv run ruff check`.
- The core (`src/pkpipe/engine`, `domain`) may only depend on `ports`. New integrations are adapters.
- Every behaviour change to a step needs a dataset entry in `evals/` and must keep the eval gate green.
- Never commit personal data, contexts or real journals. Examples must be synthetic.
- Docs: `cd website && npm install && npm start` (Docusaurus).
