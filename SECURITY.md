# Security model (short)

- All external content is untrusted data. Raw web text only reaches sessions without tools.
- Input guard: URL filter -> sanitising -> heuristics + local classifier (Qwen3Guard + ProtectAI DeBERTa) -> Reader session (canary, injection verdict) -> code decides.
- Output guard before anything is written to the vault: run canary, active markup, heuristics, harmful-content and injection classifiers; code decides.
- Flagged items are quarantined in inert form, the URL is logged and deny-listed (except configured protected hosts such as github.com), the owner is alerted; only the owner releases.

Report vulnerabilities privately via GitHub security advisories.
