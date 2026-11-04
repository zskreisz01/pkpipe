---
title: Process model
---

# Process model

The process model is BPMN 2.0, drawn in draw.io: `docs/design/pkpipe-process-model.drawio`. Every page is exported as an image to `docs/img/bpmn/` and also described as a Mermaid flowchart, so humans read the picture and agents read the diagram as code. The BPMN file is the source of truth; when a page changes, re-export the image and update the Mermaid block in the same commit.

| # | Page |
| --- | --- |
| 1 | [Global Process Overview](process/global-process-overview.md) |
| 2 | [Process Inbox Items](process/process-inbox-items.md) |
| 3 | [Process Learning Material](process/process-learning-material.md) |
| 4 | [Process Other Resource](process/process-other-resource.md) |
| 5 | [Secure Fetch](process/secure-fetch.md) |
| 6 | [Run Category Sessions](process/run-category-sessions.md) |
| 7 | [Handle Security Flag](process/handle-security-flag.md) |
| 8 | [Refresh Project Contexts](process/refresh-project-contexts.md) |
| 9 | [Self-Improvement Cycle](process/self-improvement-cycle.md) |
| 10 | [System Tool Candidates](process/system-tool-candidates.md) |
| 11 | [Wait for Generation Result](process/wait-for-generation-result.md) |
| 12 | [Restore Run](process/restore-run.md) |

## Notation

- **Pools and lanes:** the pkpipe process with an *Agent* and an *Owner* lane; external services are collapsed black-box pools connected by message flows.
- **Call activities** (thick border, `+` marker) are reusable processes with their own page.
- **Colours:** yellow = isolated LLM session with a category instruction; red = security element.
- **Security flag** is an error end event inside a sub-process, caught per item by a boundary error event.

## Re-exporting the images

```bash
D=/Applications/draw.io.app/Contents/MacOS/draw.io   # draw.io desktop CLI
cd docs
i=1
for n in global-process-overview process-inbox-items process-learning-material process-other-resource secure-fetch run-category-sessions handle-security-flag refresh-project-contexts self-improvement-cycle system-tool-candidates wait-for-generation-result restore-run; do
  $D -x -f png -s 2 -b 10 -p $i -o img/bpmn/$n.png design/pkpipe-process-model.drawio
  i=$((i+1))
done
```
