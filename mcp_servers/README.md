MCP servers run as separate processes on 127.0.0.1 (Streamable HTTP), shared by the app, Hermes and Claude Desktop.

| Server | Port | Status |
| --- | --- | --- |
| obsidian_vault | 8701 | planned (FastMCP over the vault folder) |
| web-search | 8702 | use an existing SearXNG/Brave MCP |
| learning_gateway | 8703 | planned (backends: open_notebook, notebooklm_browser, fake) |
| web-fetch | 8704 | Lightpanda MCP, Chromium fallback |
