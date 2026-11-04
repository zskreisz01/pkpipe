# obsidian_vault

FastMCP server over a vault folder (spec 004). Code: `src/pkpipe/obsidian/`, contract:
`specs/004-obsidian-vault-mcp/contracts/obsidian-vault.md`.

```
uv run python mcp_servers/obsidian_vault/server.py --config vault-policy.json   # 127.0.0.1:8701
```

A launchd example is in `deploy/launchd/io.pkpipe.obsidian-vault.plist.example`.

Spec 020: the policy file has a `layout` block (folders and files, see `docs/configuration.md`, "Vault layout") and the
tool `inbox_remove_entries(entries)`, which removes processed entries (exact raw text) from the configured
`inbox_list_file` and appends them to the dated archive file. It writes nothing else.
