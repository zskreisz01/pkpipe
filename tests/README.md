# Tests

```
tests/
  conftest.py        live-marker handling, shared fixtures (pack_dir, overlay_dir, write_json, synthetic_vault, lmstudio)
  support/live.py    probes for live services (LM Studio, Docker, Lightpanda)
  fakes/             in-memory fake for every port in src/pkpipe/ports (ModelPort -> FakeModel, ...)
  unit/<area>/       pure logic, fakes only, fast
  integration/<area>/ real adapters against real local processes the test starts (subprocess MCP servers,
                     SQLite files, local HTTP servers, temp vault dirs)
  e2e/               full flows through the CLI / API / MCP entry points on a synthetic temp vault
```

Run everything: `uv run pytest`. One level: `uv run pytest tests/unit`.

## Rules

- Synthetic data only. Never read or write a real Obsidian vault; use `tmp_path` / `synthetic_vault`.
- Name the spec requirement (FR-xxx, SC-xxx, US-x) in the test docstring.
- Core code is tested with fakes from `tests/fakes/`; add a fake (and an entry in
  `tests/unit/ports/test_fakes.py`) whenever a port is added.
- Do not mock the unit under test. Cover failure paths and the security rules.

## Markers

| Marker | Meaning |
| --- | --- |
| `live` | Needs a real local service. `@pytest.mark.live` = LM Studio at `PKPIPE_MODEL_BASE_URL` (default `http://127.0.0.1:1234/v1`) with model `PKPIPE_MODEL_NAME` (default `qwen3.5-9b-mlx`). `@pytest.mark.live("judge")` = the eval judge model `PKPIPE_JUDGE_MODEL` (default `google/gemma-4-12b`) on the same server, different from the model under test (spec 013). Other services: `@pytest.mark.live("docker")`, `@pytest.mark.live("lightpanda")`, `@pytest.mark.live("browser")` (Playwright Chromium for the UI e2e test, spec 014: `uv run playwright install chromium`), combinations allowed. |
| `docs` | Builds the Docusaurus site (`npm run build`); skipped when `website/node_modules` is missing. |

Live tests are **skipped** when the service is unreachable, so GitHub CI stays green. On the machine that owns the
services run `PKPIPE_REQUIRE_LIVE=1 uv run pytest` to turn every skip of a live test into a failure.
Qwen3.5 may emit `<think>...</think>`; adapters and tests must tolerate and strip it.

Add a probe for a new service in `tests/support/live.py` (`PROBES`).
