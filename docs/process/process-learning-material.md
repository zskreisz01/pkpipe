---
title: Process Learning Material
sidebar_position: 3
---

# Process Learning Material

The notebook step only receives the URL and a prompt template from configuration; it never sees the page content (the flow is deterministic, no model reads the note). The gateway filters the URL once more before the backend fetches it (with DNS resolution and the deny list; a URL it refuses takes the same security flag path as the first check), and starts the podcast generation together with the notebook (one `create` call). Generation with local TTS is slow, so the gateway has a configurable timeout (default 90 minutes) and records the failure reason; the flow's wait deadline backs it up. Switching to NotebookLM is a `learning-gateway` setting. The output guard step is added with spec 009.

![Process Learning Material](../img/bpmn/process-learning-material.png)

```mermaid
flowchart LR
    s(( )) --> url[URL check] --> g1{Allowed?}
    g1 -- no --> f1((Security flag))
    g1 -- yes --> cr[Create notebook + source<br/>URL + template only] --> gen[Start podcast generation<br/>local TTS] --> w[[Wait for generation result]]
    w --> get[Get audio + link] --> og[Output guard] --> g2{Flag?}
    g2 -- yes --> f2((Security flag))
    g2 -- no --> upd[Update list: status ready] --> ok((Ready))
    w -. timeout / error .-> fl[Update list: status failed] --> fe((Failed))
    cr -. REST .-> B[/Open Notebook + TTS/]
    gen -. REST .-> B
```
