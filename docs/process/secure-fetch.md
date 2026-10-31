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
    g1 -- yes --> fe[Fetch as Markdown<br/>Lightpanda sandbox]
    fe -. fetch failed .-> fb[Fallback fetch<br/>Playwright + Chromium]
    fe --> gm{ }
    fb --> gm
    gm --> cl[Sanitize: hidden HTML,<br/>zero-width, regex] --> pg[Prompt Guard 2 scan] --> rd[Reader session<br/>no tools, canary, JSON] --> vd[Evaluate verdict - code] --> g2{Any flag?}
    g2 -- no --> ok((Clean extract))
    g2 -- yes --> f2((Security flag))
```
