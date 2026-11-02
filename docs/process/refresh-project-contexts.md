---
title: Refresh Project Contexts
sidebar_position: 8
---

# Refresh Project Contexts

Weekly (Sunday 19:00) and on demand: flow `context_refresh` with the subflow `context_pack` per scope. Builds compact context packs from the owner's project notes. Personal and enterprise contexts stay separate and are summarized in parallel; the enterprise summary is anonymized by code and runs on the local model only (validation and every run refuse a remote profile). The names of the enterprise notes go to the query guard's terms file. Manual edits are preserved: an edited pack is not overwritten, the new version is proposed as a diff. Details: [Configuration](../configuration.md#weekly-refresh-spec-011).

![Refresh Project Contexts](../img/bpmn/refresh-project-contexts.png)

```mermaid
flowchart LR
    s(( )) --> p{{+}}
    p --> r1[Read personal project notes] --> s1[Summarize personal context<br/>local model] --> b1[Build pack within token budget] --> w1[Keep manual edits:<br/>write pack or propose diff] --> j{{+}}
    p --> r2[Read enterprise project notes] --> s2[Summarize enterprise context<br/>local model only] --> a2[Anonymize: scrub and verify names] --> t2[Add names to query guard terms] --> w2[Keep manual edits:<br/>write pack or propose diff] --> j
    r1 -.->|no notes| e1[Empty pack with a warning] -.-> w1
    r2 -.->|no notes| e2[Empty pack with a warning] -.-> w2
    j --> n[Notify: review context diff] --> e((Refreshed))
```
