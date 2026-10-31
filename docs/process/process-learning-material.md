---
title: Process Learning Material
sidebar_position: 3
---

# Process Learning Material

The notebook agent only receives the URL and a prompt template from configuration; it never sees the page content. Podcast generation with local TTS is a separate, slower step with a configurable timeout. Switching to NotebookLM is a `learning-gateway` setting.

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
