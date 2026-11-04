---
title: Global Process Overview
sidebar_position: 1
---

# Global Process Overview

Independent processes that share only data stores: capture and journaling by the owner, daily processing (20:00 / 06:00), the weekly context refresh and the self-improvement cycle. No sequence flow connects them. Every agent process that can write to the vault starts with the task Take vault snapshot; the owner can undo a run ([Restore Run](restore-run.md)).

![Global Process Overview](../img/bpmn/global-process-overview.png)

```mermaid
flowchart LR
    subgraph Owner
        s1((Resource found)) --> t1[Capture into INBOX] --> e1((Captured))
        s5((Day ends)) --> t5[Write daily journal] --> e5((Written))
    end
    subgraph Agent
        s3((Sun 19:00)) --> n3[Take vault snapshot] --> c3[[Refresh project contexts]] --> e3((Done))
        s4((Daily 21:30 / Sun 19:30)) --> n4[Take vault snapshot] --> c4[[Self-improvement cycle]] --> e4((Done))
        s2((Daily 20:00)) --> n2[Take vault snapshot] --> c2[[Process Inbox Items]] --> e2((Done))
    end
    t1 -.-> V[(Obsidian vault)]
    t5 -.-> V
    c2 -. notebook, podcast .-> L[/Learning service/]
    c2 -. search, fetch .-> W[/Web/]
    c2 -. notify .-> N[/Discord or Teams/]
```
