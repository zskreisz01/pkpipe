---
spec_id: 012-learning-gateway
title: learning-gateway MCP and Process Learning Material
status: Completed
description: Contract of the learning-gateway MCP tools, the backend interface, configuration and errors.
updated: 2026-11-01
---

# Contract: learning-gateway MCP server

Endpoint `http://127.0.0.1:8703/mcp` (Streamable HTTP, loopback only; `--transport stdio` for local clients).
Run: `uv run python -m pkpipe.learning.server [--pack DIR] [--overlay DIR]` (entry `mcp_servers/learning_gateway/server.py`).
The server reads the effective config: `mcp_servers.json` entry `learning` and `security.json` `url_filter`.

## Tools (FR-001)

The app's allow list (`mcp_servers.json` `learning.allow`) is `create`, `status`, `link`, `audio`. Every tool returns
a JSON object; an error is an MCP tool error whose message is safe to show and to record as a failure reason.

| Tool | Arguments | Returns | Errors |
| --- | --- | --- | --- |
| `create` | `url: str`, `template: str = ""`, `item_id: str = ""` | `NotebookJob` with `status: generating` (`job_id`, `nb_id`, `backend`, ...) | `url_blocked: <code>` with code `private_network`, `scheme_not_allowed`, `deny_list`, `credentials_in_url`, `invalid_url` (URL filter, before any backend call; the flow keys on the `url_blocked:` prefix); `template too long` (4000 characters); `item_id may only contain ...`; backend unavailable (recorded as a failed job) |
| `status` | `job_id: str` | `NotebookJob`: `status` = `generating`, `ready` or `failed`, `reason` when failed, `link` when ready, `elapsed_s` | `unknown job` |
| `link` | `nb_id: str` | `{nb_id, url}` | `unknown notebook` |
| `audio` | `job_id: str` | `{job_id, audio_path, bytes}`: the podcast downloaded into the gateway's audio folder | `unknown job`; `job is <status>, audio exists only for a ready job` |

Behaviour that does not depend on the backend:

- **Only the URL and the template reach the backend** (FR-002). The note text, the item's path and everything else
  stay in the flow. The URL filter (`pkpipe.domain.urlfilter`: http(s) only, no credentials, no private networks
  with DNS resolution, `security.url_filter.deny_list`) runs first; a blocked URL creates no job and no notebook.
- **Idempotent per item**: `create` with an `item_id` that has a `generating` or `ready` job returns that job (a
  repeated run or a restart makes no second notebook); a `failed` job does not block a new one.
- **Timeout** (FR-003): a job that is still generating `timeout_s` after its start (default 5400 s = 90 minutes,
  `options.timeout_s`) becomes `failed` with reason `timeout after N min`. The flow's own wait deadline
  (`learning_material.wait_generation.deadline_s`, 6000 s) is the backstop and must stay above `timeout_s`.
- **Failure reasons** are one line, at most 300 characters, never contain credentials, and are stored with the job
  (`jobs.db`, table `notebook_jobs`). Five consecutive failed polls (backend unreachable) fail the job.
- **Jobs survive a restart** (SQLite under `options.data_dir`, default `$PKPIPE_DATA_DIR/learning`).

## Backend interface

`pkpipe.learning.backend.LearningBackend` (Protocol):

| Method | Meaning |
| --- | --- |
| `create(url, template, item_id) -> BackendHandle(nb_id, job_id, ref)` | notebook from the URL, start the podcast generation |
| `poll(handle) -> BackendStatus(status, reason, ref)` | `generating`, `ready` or `failed` with a reason; raises `BackendUnavailable` when the service cannot be reached |
| `link(nb_id) -> str` | URL the owner opens (browser or mobile app) |
| `fetch_audio(handle, dest_dir) -> Path` | download the finished podcast |

| Backend (`mcp_servers.json` `learning.backend`) | Implementation | Status |
| --- | --- | --- |
| `open_notebook` (default) | REST: `POST /api/notebooks`, `POST /api/sources/json` (type `link`), `POST /api/podcasts/generate`, `GET /api/podcasts/jobs/{id}`, `GET /api/podcasts/episodes/{id}/audio`; local TTS by Speaches (Kokoro) | implemented, live tested |
| `notebooklm_browser` | `NotebookLMBrowserBackend` over a `NotebookLMDriver` (browser automation client) named in `options.backend_options.driver` as `module:factory`; every driver error, and every driver call that takes longer than `backend_options.call_timeout_s` (default 120 s), becomes a fast `BackendUnavailable` naming the step | interface and contract tested with a scripted driver; **no driver is shipped** and the owner's Google account is never used by tests |
| `fake` | `FakeBackend` (scripted: `ready_after`, `fail`, `never`, `down`) | tests and flow evals |

## Configuration

```json
{
  "learning": {
    "transport": "streamable_http",
    "url": "http://127.0.0.1:8703/mcp",
    "backend": "open_notebook",
    "allow": ["create", "status", "link", "audio"],
    "options": {
      "timeout_s": 5400,
      "data_dir": "~/.local/share/pkpipe/learning",
      "audio_dir": "~/.local/share/pkpipe/learning/audio",
      "backend_options": {
        "api_url": "http://127.0.0.1:5055",
        "ui_url": "http://127.0.0.1:8502",
        "episode_profile": "pkpipe_overview",
        "speaker_profile": "pkpipe_duo",
        "password_env": "OPEN_NOTEBOOK_PASSWORD",
        "request_timeout_s": 110
      }
    }
  }
}
```

Switching to NotebookLM is an overlay: `{"learning": {"backend": "notebooklm_browser", "options": {"backend_options": {"driver": "my_pkg.nlm:make_driver"}}}}`.
Unknown option names are rejected. Secrets (the Open Notebook password) are read from the environment variable named
by `password_env`, never from the config.

## Flow `learning_material` (BPMN: Process Learning Material)

`read` (obsidian `read_note`) -> `prepare` (`learning.prepare`: source URL from the front matter `source` / `url` /
`link` or the first URL of the body, template from `instructions/learning-material/notebook.md`, first URL check)
-> `create` -> `wait_generation` (60 s polls) -> `link` -> `audio` -> `ready`. Branches: `blocked` URL -> security
flag -> `quarantined`; no URL -> `failed`; failed generation or timeout -> `failed` (reason in `item.failure_reason`);
audio download error -> `failed`; `create` error -> `create_failed` (`learning.create_failed`): `url_blocked: <code>` (the
gateway's DNS check or deny list) -> security flag -> `quarantined`; any other error (service down) -> the item fails,
stays `released` and is retried by the next run. A wait error (deadline, status tool error) -> `failed` with the error
message as reason.

## Metrics

The gateway records `pkpipe.learning_timeouts_total` (a job reached `timeout_s`) and
`pkpipe.learning_generation_seconds` (start to ready) with the `OTEL_*` settings of its environment.
