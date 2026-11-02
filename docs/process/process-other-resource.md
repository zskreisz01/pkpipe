---
title: Process Other Resource
sidebar_position: 4
---

# Process Other Resource

The category's session plan is loaded from configuration (`categories.json`, flow `other_resource`, step type `session_plan`; [Flows](../flows.md#category-session-plans-spec-010)). Secure fetch runs before any session (only when a session reads the page extract), sessions only see the clean JSON extract, and the output guard (step type `output_guard`, spec 009) checks the synthesized note before anything is written to the vault: the run's canary, active markup, heuristics and the classifier. A flag hands the item to [Handle Security Flag](handle-security-flag.md); nothing of the flagged output is written. Misc items have no web content: their plan is the single new-category evaluation over the INBOX note text (a session without tools), so they skip secure fetch but still pass the output guard. After the guard, code turns a new-category suggestion into an agent task note, the note is stored in the category's PARA folder, the processing list row is set, and the INBOX note is archived. A failed session, a missing URL or an unreachable page lists the item as failed with the reason (no security flag).

![Process Other Resource](../img/bpmn/process-other-resource.png)

```mermaid
flowchart LR
    s(( )) --> plan[Load category session plan] --> gp{Plan?}
    gp -- page --> sf[[Secure fetch]] -- clean --> rs[[Run category sessions]]
    gp -- note text only --> rs
    gp -- no plan / no URL --> fl[List item as failed<br/>reason in the processing list] --> f((Failed))
    sf -. fetch failed .-> fl
    sf -. security flag .-> x1((Security flag))
    rs -. error .-> fl
    rs --> og[Output guard<br/>canary, heuristics,<br/>Qwen3Guard + DeBERTa] --> g2{Flag?}
    g2 -- yes --> x2((Security flag))
    g2 -- no --> so[Store in Obsidian PARA] --> gn{New category<br/>suggested?}
    gn -- yes --> ac[Add suggestion to agent tasks] --> m{ }
    gn -- no --> m
    m --> al[Add to processing list] --> ar[Archive INBOX item] --> e((Done))
```
