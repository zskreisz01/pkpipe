---
title: Self-Improvement Cycle
sidebar_position: 9
---

# Self-Improvement Cycle

Improvement items come from the daily journal. A local session without tools extracts them every evening; every Sunday the agent collects evidence and proposes a verdict (keep, adjust, drop) and the next experiment, and the owner decides. System improvements go through the eval gate like any other configuration change.

![Self-Improvement Cycle](../img/bpmn/self-improvement-cycle.png)

```mermaid
flowchart LR
    subgraph Owner
        zs((Day ends)) --> zj[Write journal] --> ze((Written))
        zr[Weekly review:<br/>confirm verdicts]
    end
    subgraph Agent
        as((21:30)) --> ax[Extract improvement items<br/>local, no tools] --> ag{New?}
        ag -- yes --> aa[Add to backlog: proposed] --> ae(( ))
        ag -- no --> ae2((Nothing new))
        ws((Sun 19:30)) --> we[Collect evidence] --> wp[Propose verdict + next experiment] --> wn[Notify: review ready]
        wg{Area?}
        wg -- system --> wy[[System change via eval gate]] --> wye(( ))
        wg -- personal / learning / work --> wz[Update backlog; kept items to personal context] --> wze(( ))
    end
    wn --> zr --> wg
```
