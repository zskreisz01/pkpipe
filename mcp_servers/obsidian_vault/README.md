# obsidian_vault

FastMCP server over a vault folder (spec 004). Code: `src/pkpipe/obsidian/`, contract:
`specs/004-obsidian-vault-mcp/contracts/obsidian-vault.md`.

```
uv run python mcp_servers/obsidian_vault/server.py --config vault-policy.json   # 127.0.0.1:8701
```

A launchd example is in `deploy/launchd/io.pkpipe.obsidian-vault.plist.example`.
