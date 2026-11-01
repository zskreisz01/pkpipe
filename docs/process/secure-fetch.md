---
title: Secure Fetch
sidebar_position: 5
---

# Secure Fetch

Reusable call activity. Raw web content only enters sessions without tools; every check can end in a security flag, and the decision is made by code, not by the model. The "safe word" idea is implemented as a per-run canary plus an authenticated instruction channel.

![Secure Fetch](../img/bpmn/secure-fetch.png)

```mermaid
flowchart LR
    s(( )) --> url[URL check<br/>deny-list, private networks, size] --> g1{Allowed?}
    g1 -- no --> f1((Security flag))
    g1 -- yes --> fe[Fetch page<br/>Lightpanda behind filtering proxy]
    fe -. fetch failed .-> fb[Fallback fetch<br/>Playwright + Chromium]
    fb -. fallback failed .-> ff((Fetch failed<br/>no flag))
    fe --> gm{ }
    fb --> gm
    gm --> cl[Sanitize: hidden HTML,<br/>zero-width, regex] --> pg[Classifier scan<br/>Qwen3Guard + DeBERTa] --> rd[Reader session<br/>no tools, canary, JSON] --> vd[Evaluate verdict - code] --> g2{Any flag?}
    pg -- PDF, video, image: metadata only --> vd
    g2 -- no --> ok((Clean extract))
    g2 -- yes --> f2((Security flag))
```

## Implementation (spec 008)

The pack flow `flows/secure_fetch.json` implements this page node by node:

| BPMN element | Node | Step type | Notes |
| --- | --- | --- | --- |
| URL check | `url_check` | `code` `secure_fetch.url_check` | Scheme http(s), no credentials, deny-list (`security/deny_list.txt`, subdomains included), private networks after DNS resolution (loopback, RFC 1918, link-local / cloud metadata, CGNAT, IPv6 local, numeric IP forms). Unresolvable host: failed, not a flag |
| Fetch page / Fallback fetch | `fetch` | `fetch` | One call of `webfetch:fetch_page` (web-fetch MCP server). The server runs Lightpanda, and once Playwright + Chromium on error or timeout, both behind a filtering proxy that checks every request (redirect hops, scripts, XHR) and a byte budget. A blocked hop or an oversized page is `blocked` (flag); both browsers failing is `failed` (no flag) |
| Sanitize | `sanitize` | `sanitize` | Hidden elements, comments, script/style, zero-width and Unicode tag characters removed; hidden text kept aside for the heuristics. PDF, video, image: metadata only |
| Classifier scan | `scan` | `code` `secure_fetch.scan` | Regex heuristics (visible and hidden text) and the classifier of `security.classifier` (reference: Qwen3Guard-Gen-0.6B + ProtectAI DeBERTa injection classifier, maximum score, token chunks with overlap; [decision 0001](../decisions/0001-injection-classifier.md)). Scores only |
| Reader session | `read` | `reader` | No tools, no context pack, per-run canary, datamarked page, `reader_extract` schema |
| Evaluate verdict | `verdict` | `code` `secure_fetch.verdict` | Flags: `url_filter`, `heuristic`, `classifier` (also when the classifier is unavailable and `security.classifier.required`), `canary`, `reader`. Sets `item.fetch_status`, `item.extract` (clean only) and `item.flagged` / `item.security_flags` |

A flagged verdict sets `item.flagged`; the calling flow hands the item to [Handle Security Flag](handle-security-flag.md). Only `verdict.extract` / `item.extract` may reach a session with tools: the compiler refuses `fetch`, `sanitize`, `reader` and `subflow` outputs as `llm_tools` inputs.

