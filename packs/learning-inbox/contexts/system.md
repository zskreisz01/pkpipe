# pkpipe system context (public)

pkpipe is a local-first pipeline: Obsidian INBOX, triage, guarded LLM sessions, curated notes. One Python application orchestrates flows; MCP servers run as separate processes on the same machine. The list below names the components a tool could replace or improve.

## Components

- **Model adapter**: OpenAI-compatible client (ModelPort) with JSON-schema structured output; reference model served by LM Studio
- **Flow engine**: compiles flow JSON to LangGraph graphs, runs steps, checkpoints in SQLite
- **Scheduler**: APScheduler with a SQLite job store, runs the daily and weekly flows
- **Config registry**: layered public pack plus private overlay, validation, versioned snapshots
- **Web search**: MCP server that queries a search provider (SearXNG or Brave)
- **Web fetch**: MCP server that fetches a page with Lightpanda behind a filtering proxy, Chromium fallback
- **Injection classifier**: local Qwen3Guard and DeBERTa classifiers behind ClassifierPort, plus canary and heuristics in code
- **Vault server**: MCP server for the Obsidian vault (notes, triage file, lists)
- **Learning gateway**: MCP server that creates notebooks and podcasts, backend Open Notebook with local TTS
- **Notifier**: Discord, Teams and Apprise channels behind NotifyPort
- **Eval framework**: eval suites, LLM judge, cassettes and the publish gate
- **Telemetry**: OpenTelemetry spans exported to Langfuse or Phoenix
- **App DB**: SQLite for runs, items, jobs and checkpoints
- **Web UI**: FastAPI and HTMX interface with a Monaco config editor

## In use

- LangGraph
- LM Studio
- Lightpanda
- Playwright
- SearXNG
- Open Notebook
- Qwen3Guard
- DeBERTa
- FastMCP
- APScheduler
- FastAPI
- HTMX
- Langfuse
- Phoenix
- OpenTelemetry
- SQLite
- Obsidian
- Docusaurus
- Apprise

## Stack

Python 3.12+ with uv, pytest, ruff, pydantic. Everything external sits behind a standard protocol (MCP, OpenAI API, REST, OTLP, webhook) and a port, so a candidate tool is tried behind the matching adapter and compared with the current component on the eval suite.
