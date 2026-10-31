# Security model (short)

- All external content is untrusted data. Raw web text only reaches sessions without tools.
- Input guard: URL filter -> sanitising -> Prompt Guard 2 -> Reader session (canary, injection verdict) -> code decides.
- Output guard before anything is written to the vault: harmful-content classifier + injection classifier + canary check.
- Flagged items are quarantined, the URL is logged and deny-listed.

Report vulnerabilities privately via GitHub security advisories.
