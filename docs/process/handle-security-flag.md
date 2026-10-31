---
title: Handle Security Flag
sidebar_position: 7
---

# Handle Security Flag

Called per flagged item. Stops the item, logs the malicious URL, deny-lists the domain, moves the content to quarantine in inert form and alerts. Releasing a false positive is the owner's task on the daily processing page; every release becomes an entry in the security eval dataset.

![Handle Security Flag](../img/bpmn/handle-security-flag.png)

```mermaid
flowchart LR
    s(( )) --> t1[Log malicious URL] --> t2[Add domain to deny-list] --> t3[Move content to quarantine<br/>inert form] --> t4[Set item state quarantined] --> t5[Alert + OTel security.flag] --> e((Quarantined))
    t1 -.-> log[(malicious_urls.jsonl)]
    t3 -.-> q[(0-INBOX/_quarantine)]
```
