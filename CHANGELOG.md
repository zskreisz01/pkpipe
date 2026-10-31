# Changelog

## 0.0.1 – unreleased
- Spec 001: core-defaults config layer, layer-confined session references, Pydantic config models + JSON Schema export, `pkpipe validate`, effective-config sha256 hash, test layout (unit/integration/e2e, fakes, `live` marker), CI + Pages workflows, Docusaurus site.
- Spec 004: obsidian-vault FastMCP server (`pkpipe.obsidian`, `mcp_servers/obsidian_vault`): per-tool path allowlists, atomic front-matter-safe note tools, lossless triage file and processing list, sync-conflict detection, loopback-only Streamable HTTP, launchd example.
- Spec 004 review: `update_triage` refuses pipeline changes to the owner's confirm/override and enforces the domain state transitions (`pkpipe.domain.triage.TRANSITIONS`); managed-file protection is case-insensitive (APFS); conflict files are never appended to or moved; `move_note` cannot overwrite a racing destination; the HTTP server validates Host/Origin against DNS rebinding.
- Repository skeleton, ports, triage domain rules, canary helper, layered config loader.
