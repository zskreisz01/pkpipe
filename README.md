# pkpipe – Personal Knowledge Pipeline

[![CI](https://github.com/zskreisz01/pkpipe/actions/workflows/ci.yml/badge.svg)](https://github.com/zskreisz01/pkpipe/actions/workflows/ci.yml)
[![Pages](https://github.com/zskreisz01/pkpipe/actions/workflows/pages.yml/badge.svg)](https://zskreisz01.github.io/pkpipe/)

> Local-first agent pipeline that turns an Obsidian INBOX into learning material, tool evaluations and curated notes.

**Status: pre-alpha.** The design is stable, the code is a skeleton.

## What it does

- **Daily triage (20:00):** an isolated LLM session suggests a category for each new INBOX item and notifies you (Discord, Teams, ...).
- **You decide:** only confirmed or overridden items are processed at 06:00; the rest stays pending.
- **Processing:** learning material becomes a notebook + podcast (Open Notebook with local TTS, or NotebookLM); tools, marketing and misc items get category-specific LLM sessions and land in your PARA folders.
- **Security first:** raw web content only reaches sessions without tools; input and output guards; flagged URLs are quarantined and logged.
- **Configurable without code:** categories, session plans, instructions and schedules live in JSON + Markdown, editable from a small web UI.
- **Measurable:** eval datasets, gates before publishing a config change, OpenTelemetry traces.

## Architecture

LangGraph + FastAPI (Python), any OpenAI-compatible model server (LM Studio, Ollama, vLLM), MCP servers for the vault, web search/fetch and the learning backend. Hexagonal core: the engine only knows ports, adapters are replaceable.

See the [documentation site](https://zskreisz01.github.io/pkpipe/) for C4 diagrams, the BPMN process model, the security model and the eval strategy.

## Design and specs

- [Documentation](docs/index.md): [installation checklist](docs/installation.md), [owner manual](docs/owner-manual.md), [architecture (C4)](docs/architecture.md), [process model](docs/bpmn.md), [security](docs/security.md), [evaluation](docs/evaluation.md)
- [Constitution](.specify/memory/constitution.md): the non-negotiable rules every spec is checked against
- [Specs](specs/README.md): the implementation broken into 16 features, in implementation order

## Quickstart

```bash
uv sync
uv run pytest -n auto -m 'not live'   # fast, parallel; live tests: tests/README.md
uv run pkpipe --help
```

Configuration starts from the public pack in `packs/learning-inbox/`. Your own categories, instructions and contexts go into a private overlay directory (`PKPIPE_OVERLAY_DIR`), never into this repo.

## Project layout

```
src/pkpipe/   engine, ports, adapters, domain, security, api, mcp, telemetry
packs/           public config packs (categories, sessions, instructions, schemas)
mcp_servers/     obsidian-vault, learning-gateway, web-fetch (MCP)
evals/           synthetic datasets and rubrics
docs/            design docs: C4, process model (drawio + PNG + Mermaid), security, evals
specs/           spec-kit feature specs (spec, plan, tasks, research, data-model)
.specify/        constitution and spec templates
website/         Docusaurus site that publishes docs/ via GitHub Pages
deploy/          docker-compose and launchd examples
```

## Need it for your organisation?

The same pattern (triage → isolated sessions → guarded output → human decision) works for SME back-office processes. Contact: <sales-page-url>.

## License

MIT (to be confirmed before the first release).
