---
title: Wait for Generation Result
sidebar_position: 11
---

# Wait for Generation Result

The learning service does not call back, so the process polls every 60 seconds. There is no timeout inside the loop: the interrupting boundary timer on Process Learning Material cancels it.

![Wait for Generation Result](../img/bpmn/wait-for-generation-result.png)

```mermaid
flowchart LR
    s(( )) --> t((Wait 60 s)) --> st[Get generation status] --> g{Status?}
    g -- running --> t
    g -- ready --> ok((Result ready))
    g -- failed --> err((Generation failed))
```
