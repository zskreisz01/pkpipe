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

## Running

The suite has two parts that run differently:

```bash
uv run pytest -q -n auto -m 'not live'                 # everything that needs no live service, in parallel (~30 s)
PKPIPE_REQUIRE_LIVE=1 uv run pytest -q -m live         # live tests, serially (LM Studio, judge, Lightpanda, Docker, ...)
scripts/lmstudio-test-setup.sh                         # once before live tests: both models loaded, no TTL, parallel 4
```

- **Non-live, parallel.** `-n auto` (pytest-xdist, a dev dependency; `--dist worksteal` is set in `pyproject.toml`)
  starts one worker per CPU. Tests stay parallel-safe: temporary files only under `tmp_path`, each xdist worker
  gets its own `PKPIPE_DATA_DIR` (`tests/conftest.py`), and test servers take ports from
  `tests.support.ports.free_port`, a separate port block per worker, never `bind(0)`-then-close. Do not mutate
  `os.environ` or module globals without `monkeypatch`. One level only: `uv run pytest -n auto tests/unit`.
- **Live, serial.** Live tests share one LM Studio (and Lightpanda, Docker, Open Notebook); parallel workers
  would only queue behind each other and trigger "Model unloaded" errors while LM Studio swaps models. A live
  test in an xdist worker is therefore skipped (a failure with `PKPIPE_REQUIRE_LIVE=1`). The speed of the live
  part comes from inside the tests instead: the eval tests send `LIVE_EVAL_CONCURRENCY` (4) requests at once
  (`EvalRunner(..., concurrency=4)`, LM Studio parallel predictions), and identical requests of several live
  eval tests are sent once per session: they run in cassette `auto` mode on the session fixture
  `live_eval_cassettes` (`tests/conftest.py`), so the first test records the live answer and a later test
  with the same prompt replays it, while every changed request still reaches the model.
- **LM Studio.** `scripts/lmstudio-test-setup.sh` loads the model under test and the judge together with no
  idle TTL and 4 parallel predictions (`--check` only reports; other loaded models are left alone). Without
  it, a TTL unload or a JIT load of one model while the other is serving shows up as "Model unloaded" (HTTP
  400); rerun that test alone before calling it a failure.
- Plain `uv run pytest` still runs everything serially.

## Rules

- Synthetic data only. Never read or write a real Obsidian vault; use `tmp_path` / `synthetic_vault`.
- Name the spec requirement (FR-xxx, SC-xxx, US-x) in the test docstring.
- Core code is tested with fakes from `tests/fakes/`; add a fake (and an entry in
  `tests/unit/ports/test_fakes.py`) whenever a port is added.
- Do not mock the unit under test. Cover failure paths and the security rules.

## Markers

| Marker | Meaning |
| --- | --- |
| `live` | Needs a real local service. `@pytest.mark.live` = LM Studio at `PKPIPE_MODEL_BASE_URL` (default `http://127.0.0.1:1234/v1`) with model `PKPIPE_MODEL_NAME` (default `qwen3.5-9b-mlx`). `@pytest.mark.live("judge")` = the eval judge model `PKPIPE_JUDGE_MODEL` (default `google/gemma-4-12b`) on the same server, different from the model under test (spec 013). `@pytest.mark.live("open_notebook")` = Open Notebook + Speaches (spec 012; `PKPIPE_LIVE_AUDIO=1` also generates a full podcast, 10 to 40 minutes; the tests start `deploy/open-notebook` or `~/tools/open-notebook` when it is down and stop it again). Other services: `@pytest.mark.live("docker")`, `@pytest.mark.live("lightpanda")`, `@pytest.mark.live("browser")` (Playwright Chromium for the UI e2e test, spec 014, and the secure-fetch fallback, spec 008: `uv run playwright install chromium`), combinations allowed. |
| `docs` | Builds the Docusaurus site (`npm run build`); skipped when `website/node_modules` is missing. |

Live tests are **skipped** when the service is unreachable, so GitHub CI stays green. On the machine that owns the
services run `PKPIPE_REQUIRE_LIVE=1 uv run pytest` to turn every skip of a live test into a failure.
Qwen3.5 may emit `<think>...</think>`; adapters and tests must tolerate and strip it.

Add a probe for a new service in `tests/support/live.py` (`PROBES`).

## Secure fetch fixtures (spec 008)

`tests/support/fixture_site.py` serves synthetic pages on 127.0.0.1 (clean, hidden and visible injections, redirects
and page requests to private addresses, oversized, PDF, video). Loopback is blocked by the URL filter, so tests use
the test-only allowance for exactly that port: `fetch_test_loopback` in the run's ports, or
`PKPIPE_WEBFETCH_TEST_LOOPBACK=127.0.0.1:<port>` for the web-fetch server process (`tests/support/webfetch_mcp.py`).
It is not a config key and must never be set in a deployment. ## Classifiers (specs 008, 009)

Unit tests never load a model: `tests/unit/conftest.py` replaces `pkpipe.adapters.classifiers.build_classifier` with
the substring-rule fake (`tests.support.evals.fake_classifier`), and `tests/e2e/conftest.py` replaces
`app.make_classifier` unless the test is marked `real_classifier`. `tests.support.flows.make_ports` adds the fake
classifier and a `FakeSecurityLog` unless a test passes its own (`classifier=None` tests the fail-closed path).
The real Qwen3Guard-Gen-0.6B and ProtectAI DeBERTa models run in `tests/integration/security/test_classifiers_live.py`
under `@pytest.mark.live("guard")` (model directories `~/tools/qwen3guard/Qwen3Guard-Gen-0.6B` and
`~/tools/protectai-deberta-pi-v2` plus `uv sync --extra guard`; see docs/installation.md). The security eval replays
recorded classifier results from `evals/cassettes/classifier.json`.
