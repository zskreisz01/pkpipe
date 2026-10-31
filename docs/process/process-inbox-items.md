---
title: Process Inbox Items
sidebar_position: 2
---

# Process Inbox Items

At 20:00 an isolated session without tools suggests a category per new INBOX item and the owner is notified. At 06:00 only confirmed or overridden items are released; the rest stays pending and is listed again in the next notification. Each released item runs in its own instance; a security flag in one item quarantines only that item.

![Process Inbox Items](../img/bpmn/process-inbox-items.png)

```mermaid
flowchart LR
    s((20:00)) --> cat[Categorize new items<br/>isolated session] --> ntf[Notify: triage ready] --> par{{+}}
    par --> tm((06:00)) --> sel[Select confirmed /<br/>overridden items]
    par --> rev[Owner: confirm or override] --> rd((Reviewed))
    rev -. "06:00 boundary" .-> rc((Window closed,<br/>items stay pending))
    sel --> mi
    subgraph mi [Process each released item - parallel]
        g{Category?} -- learning material --> cl[[Process learning material]]
        g -- other --> co[[Process other resource]]
        cl -. security flag .-> hq[[Handle security flag]]
        co -. security flag .-> hq
    end
    mi --> sum[Notify: daily summary] --> e((Done))
    sum --> wk[Owner: work through processing list] --> qr[Owner: review quarantine]
```
