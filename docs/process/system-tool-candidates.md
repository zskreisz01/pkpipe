---
title: System Tool Candidates
sidebar_position: 10
---

# System Tool Candidates

A tool evaluation can flag that the tool could improve pkpipe itself. Candidates go to a separate list; adoption only happens after a spike behind the matching adapter beats the current component on the eval suite, recorded in an ADR.

![System Tool Candidates](../img/bpmn/system-tool-candidates.png)

```mermaid
flowchart LR
    s((After tool S5)) --> rel[S6 System relevance check<br/>system context] --> g{Candidate?}
    g -- no --> ne((Not relevant))
    g -- yes --> add[Add to System-Tool-Candidates] --> e((Listed))
    zs((Weekly review)) --> zp[Owner: pick candidates] --> sp[[Spike behind adapter,<br/>A/B on eval suite]] --> sg{Better?}
    sg -- yes --> ad[Write ADR + adopt via eval gate] --> ade((Adopted))
    sg -- no --> rj[Mark rejected with reason] --> rje((Rejected))
```
