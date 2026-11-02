---
title: Run Category Sessions
sidebar_position: 6
---

# Run Category Sessions

Each task is one isolated LLM session: a short fixed system prompt, the category instruction from configuration in the user prompt, at most one context pack, and only the declared inputs. Overlay categories (for example job search or business opportunities) live in a private configuration overlay and follow the same pattern: a new category is `categories.json` plus instruction files, no code. The `session_plan` step runs the sessions in plan order; a session whose context pack is missing is skipped. S6 (system relevance) is added by [System Tool Candidates](system-tool-candidates.md) (spec 016) after the note session, which is why the plan names its `note_session`.

![Run Category Sessions](../img/bpmn/run-category-sessions.png)

```mermaid
flowchart LR
    s(( )) --> g{Category?}
    g -- tool --> t1[S1 Research tool and ecosystem] --> t2[S2 Compare alternatives] --> t3[S3 Fit: personal projects] --> t4[S4 Fit: enterprise projects] --> t5[S5 Synthesize evaluation] --> t6[S6 System relevance check] --> j{ }
    g -- marketing --> k1[S1 Extract idea and hook] --> k2[S2 Synthesize note] --> j
    g -- overlay --> o1[S1 Research] --> o2[S2 Extract / evaluate] --> o3[S3 Synthesize] --> j
    j --> e((Session results))
    cp[(Personal context)] -.-> t3
    ce[(Enterprise context<br/>local only)] -.-> t4
```
