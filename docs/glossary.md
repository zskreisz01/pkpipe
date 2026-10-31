---
title: Glossary
---

# Glossary

| Term | Meaning |
| --- | --- |
| INBOX | `0-INBOX` in the Obsidian vault; where captured resources land |
| Triage | The 20:00 categorization; the AI suggests, the owner confirms or overrides |
| Release | At 06:00, confirmed or overridden items move on; the rest stays pending |
| Category | Learning material, Tool, Marketing, Misc (public) plus overlay categories |
| Session | One isolated LLM step: fixed system prompt, category instruction, declared inputs |
| Session plan | Ordered list of sessions per category, in `categories.json` |
| Context pack | Compact Markdown context: system, personal, enterprise |
| Overlay | Private configuration layer merged over the public pack |
| Secure fetch | Reusable process: URL check, fetch, sanitize, classify, Reader |
| Reader | Session without tools that reads untrusted content and returns JSON |
| Output guard | Checks before writing to the vault: harmful content, injection, canary |
| Canary | Per-run random token in system instructions; its appearance signals a leak |
| Quarantine | Inert storage of flagged content plus URL log and deny-list |
| Learning gateway | MCP server hiding the learning backend (Open Notebook, NotebookLM, fake) |
| Eval gate | Required green eval before a change is published |
| Owner | The single user of a pkpipe installation |
