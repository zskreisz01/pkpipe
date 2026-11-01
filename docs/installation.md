---
title: Installation checklist
---

# Installation checklist

Everything you need on your machine to run pkpipe in its reference setup: local model, local data. Tick each box and run its check before going to the next section. Optional components can be added later; pkpipe starts without them and the related features stay off.

> **Status:** pkpipe is pre-alpha. The prerequisites are stable; the `pkpipe` subcommands are stubs until the engine specs land (see `specs/README.md`).

## 1. Machine

- [ ] **macOS on Apple Silicon** (reference, uses `launchd`) or **Linux** (use `systemd` instead of `launchd`).
- [ ] **RAM: 16 GB minimum, 32 GB recommended.** A ~9B instruct model (4-bit) needs about 6–8 GB; the guard models, Docker services and the learning backend's TTS need the rest.
- [ ] **Disk: about 30 GB free** for models, Docker images and trace data.
- [ ] **Keep the machine awake at schedule times** (20:00, 06:00, Sunday evening), or accept that missed runs execute once on wake.

## 2. Required software

| ✓ | Component | Why | Install | Check |
| --- | --- | --- | --- | --- |
| [ ] | git | Clone the engine; the config overlay is a git repo | `xcode-select --install` | `git --version` |
| [ ] | uv | Every Python command (never pip or venv) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `uv --version` |
| [ ] | Python 3.12+ | Runtime, installed and managed by uv | `uv python install 3.12` | `uv run python --version` |
| [ ] | Obsidian + a vault | INBOX, triage file, processing list, PARA folders | [obsidian.md](https://obsidian.md); create a `0-INBOX` folder | Folder exists at `PKPIPE_VAULT_DIR` |
| [ ] | Model server | All LLM sessions | LM Studio or Ollama (or any OpenAI-compatible server) | `curl http://127.0.0.1:1234/v1/models` |
| [ ] | An instruct model with JSON-schema output | Triage and category sessions | In LM Studio / Ollama, download a ~9B instruct model (default in `.env.example`) | Model listed by the check above |
| [ ] | Docker runtime | Lightpanda, SearXNG, Phoenix, Open Notebook | Docker Desktop, OrbStack or Colima | `docker compose version` |

## 3. Security components (required for processing web content)

| ✓ | Component | Why | Install | Check |
| --- | --- | --- | --- | --- |
| [ ] | Hugging Face account + access token | Llama Prompt Guard 2 is a gated model | Accept the model licence on Hugging Face, then `hf auth login` | `hf auth whoami` |
| [ ] | Llama Prompt Guard 2 (86M) | Injection and jailbreak classifier (secure fetch, spec 008) | `uv sync --extra promptguard`, then `hf download meta-llama/Llama-Prompt-Guard-2-86M --local-dir ~/tools/prompt-guard-2/Llama-Prompt-Guard-2-86M` (or set `PKPIPE_PROMPT_GUARD_DIR`). pkpipe never downloads it at run time | `uv run pytest tests/integration/security` runs instead of skipping |
| [ ] | Qwen3Guard | Harmful-content classifier for the output guard | Load it in the model server next to the main model | Listed by `/v1/models` |
| [ ] | Lightpanda | Headless fetch, one process per page with a clean environment, behind the filtering proxy of the web-fetch server | Binary on `PATH` (or `LIGHTPANDA_BIN`); set `LIGHTPANDA_DISABLE_TELEMETRY=true` | `lightpanda version` |
| [ ] | Chromium for Playwright | Fallback fetch for pages Lightpanda cannot render | `uv sync --extra webfetch && uv run playwright install chromium` | Command succeeds |
| [ ] | web-fetch MCP server | The only way flows fetch pages (spec 008) | `uv run python -m pkpipe.webfetch.server` (127.0.0.1:8704), or `deploy/launchd/io.pkpipe.web-fetch.plist.example` | `pkpipe run secure_fetch --input '{"url": "https://example.org"}'` |

Without these, triage still works (it only sees URL, title and note), but no web content is fetched or processed. Without Prompt Guard 2 every secure fetch is flagged (fail closed) unless the overlay sets `security.prompt_guard.required: false`.

## 4. Optional components

| ✓ | Component | Enables | Install | Configure |
| --- | --- | --- | --- | --- |
| [ ] | SearXNG (Docker) or a Brave Search API key | Web search in research sessions | `docker compose -f deploy/docker-compose.yml up -d searxng` | `mcp_servers.json` |
| [ ] | Open Notebook (Docker, includes SurrealDB) | Notebooks for learning material | See the Open Notebook docs | `learning-gateway` backend `open_notebook` |
| [ ] | Local TTS (Speaches / Kokoro) | Podcasts generated locally | Docker, alongside Open Notebook | Open Notebook TTS settings |
| [ ] | Google account for NotebookLM | Alternative learning backend (browser automation) | – | `learning-gateway` backend `notebooklm_browser` |
| [ ] | Discord webhook or Teams workflow webhook | Notifications | Create the webhook in the channel | `DISCORD_WEBHOOK_URL` (or Teams equivalent), referenced from `channels.json` |
| [ ] | Phoenix (Docker) | Traces and metrics, stored in a persistent volume | `docker compose -f deploy/docker-compose.yml up -d phoenix` | `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:6006` |
| [ ] | Langfuse | Alternative trace backend | Self-hosted or cloud | OTLP endpoint + auth header |
| [ ] | Tailscale | Reach the UI from your phone | [tailscale.com](https://tailscale.com) | UI stays token-protected |
| [ ] | MCP chat client (e.g. Hermes, Claude Desktop) | Confirm triage and run flows from chat | Register the pkpipe app MCP server | App MCP token |

## 5. Set up pkpipe

- [ ] Clone the engine at a released tag: `git clone https://github.com/zskreisz01/pkpipe && git checkout <tag>`
- [ ] Install dependencies: `uv sync`
- [ ] Create a **private** overlay repository outside this repo (your categories, instructions, contexts, datasets). Never commit it to the public repo.
- [ ] Copy `.env.example` to `.env` and set `PKPIPE_MODEL_BASE_URL`, `PKPIPE_MODEL_NAME`, `PKPIPE_VAULT_DIR`, `PKPIPE_OVERLAY_DIR`, `OTEL_EXPORTER_OTLP_ENDPOINT`.
- [ ] Put secrets (webhooks, API keys, `HF_TOKEN`) in the macOS keychain or the environment; config files only reference them as `env:NAME`.
- [ ] Start the helper services you chose: `docker compose -f deploy/docker-compose.yml up -d`
- [ ] Validate the layered config: `uv run pkpipe validate`
- [ ] Run the tests: `uv run pytest`
- [ ] Start the app: `uv run pkpipe serve` and open the UI.
- [ ] Do a first manual run: `uv run pkpipe run triage` with one or two test items in `0-INBOX`, and confirm the notification arrives.
- [ ] Install a `launchd` agent that runs `uv run pkpipe serve`, so the app restarts on login and after crashes (an example for the vault server is in `deploy/launchd/`).
- [ ] Calibrate the LLM judge once (see the [owner manual](owner-manual.md#calibrate-the-llm-judge)).

## 6. Cloud variant (blueprint)

The same engine runs without a local model or local data, for example on Microsoft Foundry. Change these items and skip the local ones:

| Local item | Cloud replacement |
| --- | --- |
| LM Studio / Ollama | A Foundry (or other cloud) model deployment with an OpenAI-compatible endpoint; key in a secret store |
| Obsidian vault on disk | A vault adapter over the chosen storage (synced folder, blob storage) |
| Phoenix | Azure Monitor / Application Insights via an OpenTelemetry Collector |
| `launchd` | A container host with a scheduler (e.g. Azure Container Apps) |
| Keychain | Azure Key Vault (still referenced as `env:NAME`) |

The rules do not change: raw external content only reaches sessions without tools, private data stays out of this repository, and every change passes the eval gate.

## For contributors only

- [ ] Node.js LTS for the Docusaurus site in `website/`
- [ ] draw.io desktop for the BPMN and C4 diagrams (`docs/design/*.drawio`); re-export images as described in [Process model](bpmn.md)
- [ ] `uv run ruff check` and `uv run ruff format` before every commit
