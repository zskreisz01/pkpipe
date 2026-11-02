# Tests

```
tests/
  conftest.py        live-marker handling, shared fixtures (pack_dir, overlay_dir, write_json, synthetic_vault, lmstudio)
  support/live.py    probes for live services (LM Studio, Docker, Lightpanda)
  support/preflight.py  system preflight + background sampler of a live session (how live tests adapt)
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
scripts/lmstudio-test-setup.sh                         # once before live tests: both models loaded, no TTL, parallel 4, context 32768
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
  idle TTL, 4 parallel predictions and a 32768-token context (`--check` only reports; other loaded models are
  left alone). Without it, a TTL unload or a JIT load of one model while the other is serving shows up as
  "Model unloaded" (HTTP 400); the pack's model profiles retry that and other transient errors twice
  (`retries`). Keep other clients off LM Studio during a live run: a stalled server times out requests and
  leaves eval cases unjudged (`docs/evaluation.md`, Concurrency); rerun that test alone before calling it a
  failure.
- Plain `uv run pytest` still runs everything serially.

## How live tests adapt

The Mac mini that runs the live tests also runs other programs and agents, and it may overheat or swap. Before
the first live test that drives a model (`lmstudio`, `judge`, `open_notebook`, `guard`) and whose services are
reachable, `tests/support/preflight.py` runs the system preflight (`pkpipe doctor`, docs/operations.md
"System preflight") with the pack's `evals.preflight` thresholds and a ceiling of `LIVE_EVAL_CONCURRENCY` (4):

- **Concurrency.** Eval tests pass `live.eval_concurrency()` to `EvalRunner` / `PublishGate`: 4 when the machine
  is ok, 2 under memory pressure, high swap, a `fair` thermal state, high load or another client queued on the
  model, 1 when throttled, `serious`, or swap nearly full under pressure. Results do not depend on it.
- **Timeouts.** `PKPIPE_MODEL_TIMEOUT_MULTIPLIER` (x1, x1.5, x2) scales every model profile's `timeout_s`, also in
  CLI subprocesses of e2e tests; it is restored when the session ends.
- **Blocked.** A *system* block (memory pressure `critical`, under 3% memory available, thermal state `critical`)
  skips the remaining live tests with the reason, or fails them with `PKPIPE_REQUIRE_LIVE=1`. An unreachable
  server or a missing model is left to the service probes above.
- **During the run.** A background sampler re-samples every `evals.preflight.sample_interval_s` (30 s;
  `PKPIPE_LIVE_SAMPLE_S` overrides it). A mid-run degradation lowers the concurrency and lengthens the timeouts of
  later tests; recovery raises them again. Requests of the test's own concurrency are not counted as another
  client's.
- **Record.** The first snapshot and decision are written to `$PKPIPE_TEST_DATA_DIR/system-preflight.json`, every
  decision change is a `system.preflight` span and every sample sets the `pkpipe.system.*` gauges (exported only
  when `OTEL_EXPORTER_OTLP_ENDPOINT` is set). The terminal summary ends with a `system preflight` section: the
  first and last decision, min/max swap, worst memory pressure, max load, thermal events and concurrency changes.
- `PKPIPE_LIVE_PREFLIGHT=0` turns all of this off (fixed concurrency 4, timeouts x1). Live tests never load or
  unload models; `scripts/lmstudio-test-setup.sh` does that.

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

## Category sessions (spec 010)

`FakeWeb` (`webfetch:fetch_page` over HTML pages by URL, plus the `resolver` port so no DNS is needed) and
`FakeSearch` (`websearch:search`, records the queries) are in `tests/fakes/web.py`; `tests/support/pack_runtime.py`
puts them behind `vault_tools(vault, web=..., search=...)` next to the real `Vault`. `fake_model_reply` answers every
session of the public pack by its output schema (it receives the schema: a callable with `wants_schema = True`).
`tests/e2e/test_category_sessions_e2e.py` runs the whole process through the CLI with the real vault server, the
real web-fetch server (Lightpanda, the fixture site) and `tests/support/websearch_mcp.py`, a synthetic web search
MCP server that logs every query (the query guard is asserted on that log). The fit sessions need context packs: the
tests write synthetic ones into a temp overlay, never real ones. Fixture pages of one host share a deny-list: a page
quarantined by secure fetch deny-lists the host of the fixture site, so such a page is processed in a later run than
the pages that must succeed.
