---
title: Process Learning Material
sidebar_position: 3
---

# Process Learning Material

The notebook step only receives the URL and a prompt template from configuration; it never sees the page content (the flow is deterministic, no model reads the note). The gateway filters the URL once more before the backend fetches it (with DNS resolution and the deny list; a URL it refuses takes the same security flag path as the first check), and starts the podcast generation together with the notebook (one `create` call). NotebookLM is the learning backend (spec 032; the `nlm` CLI driver, the owner's own Google session). Generation is slow, so the gateway has a configurable timeout (default 90 minutes) and records the failure reason; the flow's wait deadline backs it up. Before it creates anything the backend checks the quota (`nlm usage`): an exhausted window parks the notebook as `waiting_quota` (the item stays released, the next run continues after the reset), a transient error is retried a bounded number of times, a login or page problem fails fast with its class. When the notebook and the audio are ready, a **learning note** (source, notebook link, podcast, history link; no page text) is written into the category folder and the list row points at it; the podcast file is copied into the vault only with `learning.copy_audio_to_vault`. An image item without an address becomes its own note from the validated description, after the output guard.

![Process Learning Material](../img/bpmn/process-learning-material.png)

```mermaid
flowchart LR
    s(( )) --> url[URL check] --> g1{Allowed?}
    url -- image, no address --> img[Write image note from the description<br/>output guard, list row done, entry archived] --> idone((Done))
    g1 -- no --> f1((Security flag))
    g1 -- yes --> cr[Create notebook + source<br/>URL + template only] --> gen[Start podcast generation<br/>NotebookLM audio overview<br/>waits for the quota] --> w[[Wait for generation result]]
    w --> get[Get audio + link] --> og[Output guard] --> g2{Flag?}
    g2 -- yes --> f2((Security flag))
    g2 -- no --> note[Write learning note<br/>source, notebook, podcast, history link] --> upd[Update list: link, audio, note, status ready] --> ok((Ready))
    w -. timeout / error .-> fl[Update list: status failed] --> fe((Failed))
    cr -. nlm CLI .-> B[/NotebookLM/]
    gen -. nlm CLI .-> B
```
