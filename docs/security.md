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
| Notebook agent (the `learning_material` flow, no model involved) | Only the URL and a prompt template; the gateway filters the URL (http(s), no private networks, deny list) before the backend fetches it | `learning:create`, `status`, `link`, `audio` | `nb_id`, status, link, audio path |
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
| Classifier | `security.classifier`: Qwen3Guard-Gen-0.6B (harm categories, jailbreak) and ProtectAI deberta-v3-base-prompt-injection-v2 (injection), token chunks with overlap, maximum over chunks and models; local files only, no remote code; missing model fails closed ([decision 0001](decisions/0001-injection-classifier.md)) | Injection, jailbreak, harmful content |
| Reader verdict | `injection_suspected` + canary check | Context-dependent attempts |

The verdict (`secure_fetch.verdict`) is code: any of `url_filter`, `heuristic`, `classifier`, `canary` or `reader` flags the item; a Reader reply that is not a valid `ReaderExtract` (checked strictly by code) is flagged as `reader` (`reader_invalid_output`), never passed on. A redirect hop that the proxy refuses (deny-list, private network) is a flag even though the browser shows the proxy's 403 as the page. A timeout or an unreachable page after the Chromium fallback is a failed fetch, not a flag. Measured on the synthetic `secure_fetch.jsonl` set (40 attack pages of 11 types, 41 clean pages, with the classifier): detection 1.0, false positives 0.024, canary leaks 0; the output guard on 59 session outputs: detection 1.0, false positives 0.033 ([Evaluation](evaluation.md)).

**Test-only loopback allowance.** Tests fetch synthetic pages from a local fixture server, which the URL filter would block as loopback. They use an allowance for exactly that `127.0.0.1:<port>`: in-process it is a port of the run (`fetch_test_loopback`, set by the test's composition root), for the web-fetch server process the environment variable `PKPIPE_WEBFETCH_TEST_LOOPBACK`, which accepts only loopback IP literals with a port and is logged at start-up. Neither is a config key: `security.url_filter` rejects unknown keys, so no pack or overlay can enable it. Never set the variable in a deployment.

## Output guard

Runs on every session output that originates from external content, before anything is written to the vault (step type `output_guard`, spec 009). A note with an injected instruction would otherwise attack every agent that reads the vault later (stored injection). A session over external content is marked `canary: true`: the run's canary goes into its system prompt and `pkpipe validate` refuses a flow in which any path after it does not pass an `output_guard` that checks it, and an `output_guard` must route on its decision: its first conditional edge is `<guard>.flagged` (truthy) to the security branch, with no plain edge (taken even after a flag) and no error edge (a failed check stops the item). In the public pack these are the triage session (`categorize_item.suggest`, before the triage file is written) and the summary of other resources (`other_resource.summarize`, before the PARA note is written). The learning branch writes no derived text (notebook id, link and audio path only), so it has no output to guard.

| Flag | Detector (decided in code) |
| --- | --- |
| `canary_leak` | The run's canary or its hex part anywhere in the output: deterministic string match, independent of every model |
| `injection_or_jailbreak` | Active markup (`<script>`, `<iframe>`, `javascript:`, event handlers), heuristics, the DeBERTa injection score ≥ `classifier.threshold`, a Qwen3Guard `Jailbreak` category, or no usable classifier while `classifier.required` (fail closed) |
| `malicious_content` | A Qwen3Guard label in `output_guard.block_on` (default `Unsafe`) with a harm category (Violent, PII, Non-violent Illegal Acts, ...), or with no category at all (fail closed) |

The step's output holds flag names, detectors, reason codes and scores, never the checked text. Regression: the spec 008 review once saw the triage session leak its canary on the injection set; the guard now stops such a reply before the triage file is written (`canary_leaks_unguarded` = 0 is a gate, and a flow eval case covers it).

## Category sessions

The sessions of a category plan (spec 010, [Run Category Sessions](process/run-category-sessions.md)) follow the same rules as every session, with checks made by code at validation and at run time:

- **Raw content only reaches tool-less sessions.** The Reader reads the page; the plan's sessions read its validated extract. Raw note text (`item.text`) goes only to a session without tools (the Misc evaluation); a session with tools reads only `item.extract` and the output of a session with an output schema (`pkpipe validate`).
- **Every session carries the run's canary** and the output guard checks everything the plan produced before the note, the agent task or the list row is written; a flag quarantines the item.
- **The enterprise context never goes to the web.** A session with the enterprise pack has no web search tool, no session that reads enterprise-derived output has one either, and every query of a session with tools passes the query guard (`security.query_guard`: client and project names, refused before the call leaves the machine).
- **A missing context pack skips its session** instead of letting the model answer without the context it was written for.
- **The system relevance session reads no raw content** (spec 016): its inputs are the validated research and comparison outputs and the public system context, it has no tools and no private context, and it passes the output guard like every session. What it says never writes a list row by itself: code accepts a row only for a component named in the system context, strips pipes, line breaks and control characters from every value, and the list tool only adds rows with status `candidate`, so a model, an injected page or a rerun cannot change a rejected or adopted tool or start a candidate in another status. Status changes are owner commands run in-process, not a tool of the vault server.
- **Failures never carry content.** A failed session lists `session '<id>' failed: <ErrorType>`, nothing the model or the page said.

## When something is flagged

The flow calls [Handle Security Flag](process/handle-security-flag.md) for the item:

1. The item stops and becomes `quarantined`; the other items of the run continue (error end event per item). At triage the row is written as `quarantined` with the suggested category `none`.
2. One row per flag is appended to `security/malicious_urls.jsonl` (url, domain, flag, detector, score, evidence = reason code, item id, run id, timestamp) and the domain goes on the deny-list once (`# pkpipe: flagged N, last <day>` on repeats), in the overlay so every process's URL filter applies it: the web-fetch server and the learning gateway read the deny-list file again on every request, and the in-process checks (secure fetch, `learning.prepare`) merge the handler's entries.
3. The note moves to `0-INBOX/_quarantine/` in inert form: verbatim inside a fenced code block, `security_flag` front matter with flag codes only. Only the vault tool `quarantine_note` writes there (the folder is `layout.quarantine_dir`); `write_note`, `append_note` and `move_note` refuse the folder.
4. An alert goes to the configured channel and a `security.flag` event to the trace.
5. A false positive is released by the owner (UI triage page or app MCP `release_quarantine`, owner token, audited): the note is restored exactly (byte for byte: the quarantine note records the original line ends and final newline), the row is `pending` again and the text becomes a candidate case of the security eval dataset (`<overlay>/evals/candidates/security.jsonl`). Domains are never unblocked automatically.

## Private data

Journals and context packs are processed only by local sessions without tools, are never sent to web search or the learning service, are excluded from trace content capture, and never appear in this repository. The flows that handle them declare the data classes `journal` and `context`; validation and every run refuse a model profile, fallback or environment value that is not on a loopback address, any `egress` tool, any session with tools and any code function that handles the data in a flow without its class, so no remote model can be selected for the enterprise notes.

### Enterprise context pack (spec 011)

The enterprise pack is the only text derived from client work that later sessions see, so code decides what it may contain, not the model: the names the notes declare (front matter, labelled lines, titles), the names the local session reports and the owner's blocked terms are replaced in the generated text and checked with the query guard's matching; a line that still names one is dropped, and a summary that trips the injection heuristics is not written. The same names, in every form, go to the query guard's terms file, so a web query that names a client or project is refused before it leaves the machine. The leak eval (`context_leak.jsonl`, gate 0 leaks) measures this on synthetic notes with the same code. A name that is only in the prose and that the session neither hides nor reports cannot be known to code; the eval measures how often that happens (`names_known_rate`), and the owner reviews the diff of every refresh (the review note and the notification never contain a name).

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
