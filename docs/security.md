---
title: Security
---

# Security

All external content is untrusted data and never reaches a session that has tools or write access. The decision to stop, flag or log is always made by deterministic code, never by the model under attack.

## Isolation (dual LLM)

| Session | Sees | Tools | Output |
| --- | --- | --- | --- |
| Reader | The fetched, sanitized page | None | JSON only: extract, facts, `injection_suspected`, `evidence` |
| Research agent | Only the Reader's validated JSON | `websearch:search`, `webfetch:fetch` | Research notes (JSON) |
| Notebook agent | Only the URL and a prompt template | `learning:create`, `status`, `link` | `nb_id`, status, link |
| Synthesizer | The research output | None | Markdown note (JSON) |

"Without tools" refers to the LLM session, not to the step. The inputs of a tool-less session are loaded by deterministic code through a port (vault, fetch, state) and passed in as datamarked text; the model cannot ask for more, read other files or write anywhere. This is how the triage session reads INBOX items and the improvement extraction reads the journal.

Journal data is local only (constitution VII, spec 015): `pkpipe validate` rejects a flow with `data_classes: ["journal"]` that has a tool session, uses a model profile (or fallback) that is not on a loopback address, calls an MCP server marked `egress`, or starts a subflow that is not a journal flow itself. Every run checks the profile again when it compiles the flow: if an environment variable of the `env:` chain (for example `PKPIPE_MODEL_BASE_URL`) points at another host, the journal run stops before any session starts, while other flows may still use that host. The flow's traces never capture prompt content. The model only proposes: an improvement statement is stored only if it is verbatim in the journal, evidence only if its quote is verbatim, and every status change is the owner's (the vault tool refuses it).

Process-level isolation: pages are fetched only by the web-fetch MCP server (`pkpipe.webfetch`), which exposes one tool, `fetch_page` (plus `fetch_stats`), instead of Lightpanda's own MCP with its ~30 tools such as `evaluate`, `click` and `fill`: no model can run JavaScript in a page or drive the browser. Lightpanda runs as one process per page with a minimal environment (no API keys, tokens or webhook URLs) in an empty temporary home directory; Chromium (fallback) runs the same way with downloads, service workers and non-proxied WebRTC off. Both browsers send every request through a filtering proxy of the server that applies the URL filter after DNS resolution and connects to the address it checked, so redirect hops, scripts and XHR to private addresses are refused and DNS rebinding cannot swap in a private address. MCP servers only get their own secrets.

## Canary and authenticated instructions

- A random canary is placed in each system instruction per run, with the instruction never to output it. If it appears in any output, the item is stopped: that is a reliable sign of a prompt leak.
- Only text framed as `<<SYS:token>>` counts as instruction. External content is wrapped in random delimiters (datamarking). If the content tries to override instructions, the model reports `injection_suspected = true` with evidence instead of following it.
- The guard step evaluates the JSON field, the canary and the classifiers; the model never "decides to stop".

## Input guard

| Layer | Tool | Catches |
| --- | --- | --- |
| URL filter | Deny-list (`security/deny_list.txt`, subdomains included), only http(s), no credentials in URLs, private networks blocked after DNS resolution (loopback, RFC 1918, link-local and cloud metadata, CGNAT, IPv6 local, numeric IP forms) on the URL and on every request the browser makes, size limit (`max_bytes`) | Known bad sources, SSRF, redirects into the private network |
| Sanitizing | Hidden HTML (hidden attribute, `aria-hidden`, hiding inline styles and simple `<style>` rules, off-screen text), comments, scripts, zero-width, bidi and Unicode tag characters removed; the removed text is kept aside for the heuristics | Invisible injections |
| Heuristics | Regexes with weights: overrides ("ignore previous instructions"), role tags, text addressed to the model, role changes, prompt-leak and exfiltration requests, output hijacking, base64 blobs; hidden matches count separately (`hidden_<rule>`) | Naive attacks, cheaply |
| Classifier | Llama Prompt Guard 2 (86M), 512-token chunks with overlap, maximum over chunks; missing model fails closed | Injection and jailbreak |
| Reader verdict | `injection_suspected` + canary check | Context-dependent attempts |

The verdict (`secure_fetch.verdict`) is code: any of `url_filter`, `heuristic`, `classifier`, `canary` or `reader` flags the item. A timeout or an unreachable page after the Chromium fallback is a failed fetch, not a flag. Measured on the synthetic `secure_fetch.jsonl` set (40 attack pages of 11 types, 41 clean pages, Prompt Guard 2 not installed): detection 1.0, false positives 0.024, canary leaks 0 ([Evaluation](evaluation.md)).

**Test-only loopback allowance.** Tests fetch synthetic pages from a local fixture server, which the URL filter would block as loopback. They use an allowance for exactly that `127.0.0.1:<port>`: in-process it is a port of the run (`fetch_test_loopback`, set by the test's composition root), for the web-fetch server process the environment variable `PKPIPE_WEBFETCH_TEST_LOOPBACK`, which accepts only loopback IP literals with a port and is logged at start-up. Neither is a config key: `security.url_filter` rejects unknown keys, so no pack or overlay can enable it. Never set the variable in a deployment.

## Output guard

Runs on every session output that originates from external content, before anything is written to the vault. A note with an injected instruction would otherwise attack every agent that reads the vault later (stored injection).

| Flag | Detector |
| --- | --- |
| `malicious_content` | Qwen3Guard (Safe / Controversial / Unsafe + category) |
| `injection_or_jailbreak` | Prompt Guard 2 on the output + heuristics |
| `canary_leak` | Deterministic string match |

## When something is flagged

1. The item stops immediately and becomes `quarantined`.
2. The URL is appended to `security/malicious_urls.jsonl` (url, domain, flag, detector, score, evidence, run id, timestamp) and the domain to the deny-list.
3. The content moves to `0-INBOX/_quarantine/` in inert form (inside a code block, with `security_flag` front matter).
4. An alert goes to the configured channel and a `security.flag` event to the trace.
5. A false positive is released by the owner; the release is added to the security eval dataset.

## Private data

Journals and context packs are processed only by local sessions without tools, are never sent to web search or the learning service, are excluded from trace content capture, and never appear in this repository.

## UI and app MCP access

The config UI and the app MCP server (spec 014) can publish config and release triage items, so they are guarded like a local admin console:

- **Network**: loopback by default; only loopback and Tailscale addresses are accepted as listen address. A token is always required, also on loopback.
- **Tokens**: an owner token (UI and every MCP tool) and an agent token (MCP `run_flow`, `get_run`, `list_triage`). Compared in constant time; stored in a 0600 file or the environment, never in config. Failed logins are throttled.
- **Browser**: login sets an HttpOnly, SameSite=Strict cookie. State-changing cookie requests need an `X-CSRF-Token` header (an HMAC of the session) and a same-origin `Origin`/`Sec-Fetch-Site`. A cookie never authorizes `/mcp`.
- **DNS rebinding**: the `Host` header must be on the allowlist, otherwise HTTP 421.
- **Headers**: CSP `default-src 'self'` with no inline script or style, `frame-ancestors 'none'`, `X-Content-Type-Options`, `Referrer-Policy: same-origin`, `Cache-Control: no-store`. HTMX is vendored (no CDN) with eval disabled.
- **Owner decisions** (confirm, override, quarantine release) are separate from the pipeline's own triage writer: the vault MCP server that sessions can reach still refuses to change `confirm` / `override`. Decisions follow the triage domain rules (quarantined and processed items cannot be confirmed; overrides must name a configured category) and are audited.
- **Raw external content** never reaches the chat agent through MCP: `get_run` returns step names only and triage resources are length-capped and datamarked (random `<<DATA:nonce>>` delimiters per call). An owner action refused for the agent token is audited.
- **One active run per flow** across every channel: UI, MCP and the cron scheduler of `pkpipe serve` share the same check.
- **Config editing** is confined to known config paths below the overlay (no `..`, absolute paths or hidden folders), size-capped, and publish commits only the draft's files. Neither validation nor publish writes through a symlink that leaves the overlay, the token file is refused when other users can read it, and a draft edited while its eval gate ran is not published.
