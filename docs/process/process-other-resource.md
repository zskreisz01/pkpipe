---
title: Process Other Resource
sidebar_position: 4
---

# Process Other Resource

The category's session plan is loaded from configuration. Secure fetch runs before any session, sessions only see the clean JSON extract, and the output guard (step type `output_guard`, spec 009) checks the synthesized note before anything is written to the vault: the run's canary, active markup, heuristics and the classifier. A flag hands the item to [Handle Security Flag](handle-security-flag.md); nothing of the flagged output is written. Misc items have no web content and only get the new-category evaluation.

![Process Other Resource](../img/bpmn/process-other-resource.png)

```mermaid
flowchart LR
    s(( )) --> gc{Category?}
    gc -- misc --> ev[Evaluate: new category needed?] --> gn{New?}
    gn -- yes --> ac[Add suggestion to agent tasks] --> m1{ }
    gn -- no --> m1
    gc -- other --> plan[Load session plan] --> sf[[Secure fetch]] --> rs[[Run category sessions]] --> og[Output guard<br/>canary, heuristics,<br/>Qwen3Guard + DeBERTa] --> g2{Flag?}
    sf -. security flag .-> x1((Security flag))
    g2 -- yes --> x2((Security flag))
    g2 -- no --> m2{ }
    m1 --> m2 --> so[Store in Obsidian PARA] --> al[Add to processing list] --> ar[Archive INBOX item] --> e((Done))
```
