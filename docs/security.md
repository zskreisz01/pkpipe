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

Process-level isolation: Lightpanda runs in a container without volumes or secrets, with private networks blocked and JavaScript `evaluate` disabled. MCP servers only get their own secrets.

## Canary and authenticated instructions

- A random canary is placed in each system instruction per run, with the instruction never to output it. If it appears in any output, the item is stopped: that is a reliable sign of a prompt leak.
- Only text framed as `<<SYS:token>>` counts as instruction. External content is wrapped in random delimiters (datamarking). If the content tries to override instructions, the model reports `injection_suspected = true` with evidence instead of following it.
- The guard step evaluates the JSON field, the canary and the classifiers; the model never "decides to stop".

## Input guard

| Layer | Tool | Catches |
| --- | --- | --- |
| URL filter | Deny-list, private networks blocked, size limit | Known bad sources, SSRF |
| Sanitizing | Hidden HTML, zero-width and Unicode tag characters removed | Invisible injections |
| Heuristics | Regexes: "ignore previous", role tags, base64 blobs | Naive attacks, cheaply |
| Classifier | Llama Prompt Guard 2 (86M), 512-token chunks, multilingual | Injection and jailbreak |
| Reader verdict | `injection_suspected` + canary check | Context-dependent attempts |

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
