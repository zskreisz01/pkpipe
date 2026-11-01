# learning_gateway

FastMCP server that hides the learning backend (spec 012). Code: `src/pkpipe/learning/`, contract:
`specs/012-learning-gateway/contracts/learning-gateway.md`.

```
uv run python mcp_servers/learning_gateway/server.py [--overlay ~/pkpipe-priv/overlay]   # 127.0.0.1:8703
```

Tools: `create(url, template, item_id)`, `status(job_id)`, `link(nb_id)`, `audio(job_id)`. The backend
(`open_notebook` by default, `notebooklm_browser`, `fake`) and the timeout come from the `learning` entry of
`mcp_servers.json`; the Open Notebook + Speaches stack is in `deploy/open-notebook/`. A launchd example is in
`deploy/launchd/io.pkpipe.learning-gateway.plist.example`.
