---
title: Refresh Project Contexts
sidebar_position: 8
---

# Refresh Project Contexts

Weekly (Sunday 19:00). Builds compact context packs from the owner's project notes. Personal and enterprise contexts stay separate; the enterprise summary anonymizes clients and runs on the local model only. Manual edits are preserved: the next run shows a diff.

![Refresh Project Contexts](../img/bpmn/refresh-project-contexts.png)

```mermaid
flowchart LR
    s(( )) --> p{{+}}
    p --> r1[Read personal project notes] --> s1[Summarize personal context] --> j{{+}}
    p --> r2[Read enterprise project notes] --> s2[Summarize enterprise context<br/>anonymized, local only] --> j
    j --> w[Write context packs] --> n[Notify: review diff] --> e((Refreshed))
```
