---
title: Glossary
---

# Glossary

| Term | Meaning |
| --- | --- |
| INBOX | `0-INBOX` in the Obsidian vault; where captured resources land |
| Triage | The 20:00 categorization; the AI suggests, the owner confirms or overrides |
| Release | At 06:00, confirmed or overridden items move on; the rest stays pending |
| Category | A kind of INBOX item the owner wants handled in a specific way. Categories are configuration, not code: each entry in `categories.json` couples the category id with its triage hint, target PARA path, subflow, session plan, session instructions and optional context pack. The public pack ships Learning material, Tool, Marketing and Misc as examples; an owner adds, changes or disables categories in the private overlay. |
| Session | One isolated LLM step: fixed system prompt, category instruction, declared inputs |
| Session plan | Ordered list of sessions per category, in `categories.json` |
| Context pack | Compact Markdown context: system, personal, enterprise |
| Overlay | Private configuration layer merged over the public pack |
| Secure fetch | Reusable process: URL check, fetch, sanitize, classify, Reader |
| Reader | Session without tools that reads untrusted content and returns JSON |
| Output guard | Checks before writing to the vault: harmful content, injection, canary |
| Canary | Per-run random token in system instructions; its appearance signals a leak |
| Held for review (classifier warning) | Spec 029: only the injection detector fired, the safety model said Safe; the item waits for the owner's decision instead of going to quarantine |
| Quarantine | Inert storage of flagged content plus URL log and deny-list (protected domains excepted, spec 028) |
| Learning gateway | MCP server hiding the learning backend (Open Notebook, NotebookLM, fake) |
| Eval gate | Required green eval before a change is published |
| Run report | The summary the pipeline sends after every triage and release run (counts, created evaluations / notebooks / podcasts, todos); spec 024 |
| Todo block | The generated lines between the `pkpipe-todo` markers in the daily journal: `date - todo - item count - link` |
| Retry (failed item) | The owner's action that sends a processed item with a failed result through the next release run again; bounded by `triage.retry_max`; spec 027 |
| Source discovery | Spec 025: for an item that names no URL, one guarded web search from the validated image description or the owner's text finds the page the category's sessions then evaluate; off by default (`source_discovery.json`) |
| Owner | The single user of a pkpipe installation |
| Config form | The generated edit form of a config section in the UI (spec 026); it edits the same draft as the JSON view |
