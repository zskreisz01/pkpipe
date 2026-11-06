MCP servers run as separate processes on 127.0.0.1 (Streamable HTTP), shared by the app, Hermes and Claude Desktop.

| Server | Port | Status |
| --- | --- | --- |
| obsidian_vault | 8701 | implemented (spec 004, `pkpipe.obsidian`) |
| web_search | 8702 | implemented (spec 017, `pkpipe.websearch`): one tool `search`; SearXNG (default) or Brave; results filtered by the URL policy |
| learning_gateway | 8703 | implemented (spec 012, `pkpipe.learning`; backends: open_notebook, notebooklm_browser, fake) |
| web_fetch | 8704 | implemented (spec 008, `pkpipe.webfetch`): one tool `fetch_page` (+ `fetch_stats`); Lightpanda, Chromium fallback, filtering proxy |
