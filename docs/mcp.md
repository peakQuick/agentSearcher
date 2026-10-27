[← Back to README](../README.md)

# MCP

```bash
agentsearcher serve          # HTTP API + MCP at http://127.0.0.1:8484/mcp (also what Docker runs)
agentsearcher mcp            # stdio, for clients that start the server themselves
agentsearcher mcp --http     # MCP alone, streamable HTTP at http://127.0.0.1:8485/mcp (PORT+1)
```

`serve` is the recommended way for HTTP clients: the API and MCP share one process, so they share the page cache, the browser and the model. If `AGENTSEARCHER_API_KEYS` is set, `/mcp` requires `Authorization: Bearer <key>` like the API. CORS is open, so browser-based clients (llama.cpp webui and others) can connect directly.

The tools return compact text (title, URL, chunks) rather than JSON, sized for a small context window. Every parameter has a one-line description in the tool schema, so the agent knows what it does without the docs.

## Tools

Default names are `web_*`. With `AGENTSEARCHER_MCP_TAVILY_NAMES=1`, the same tools take the names from the official tavily-mcp: `tavily_search`, `tavily_extract`, `tavily_crawl`, `tavily_map`. This is for prompts and configs that hardcode those names. Only one set is exposed at a time.

| tool | parameters (defaults) |
|---|---|
| `web_search` | `query`, `max_results` (5), `search_depth` (`basic`), `topic` (`general`/`news`), `time_range`, `include_domains`, `exclude_domains`, `include_raw_content` (false), `country`, `max_per_domain`, `dedup` (true) |
| `web_extract` | `urls`, `query`, `extract_depth` (`basic`), `format` (`markdown`) |
| `web_crawl` | `url`, `instructions`, `max_depth` (1), `max_breadth` (20), `limit` (20), `select_paths`, `exclude_paths`, `allow_external` (**false**), `extract_depth` (`basic`), `dedup` (true) |
| `web_map` | `url`, `instructions`, `max_depth` (1), `max_breadth` (20), `limit` (50), `select_paths`, `exclude_paths`, `allow_external` (**false**) |

Differences from the HTTP API: `web_crawl` and `web_map` default to `allow_external=false` (the agent stays on the site), and crawl has a lower `limit`. Parameters mean the same as in [api.md](api.md).

## Setup

### Claude Code

```bash
claude mcp add agentsearcher -- /path/to/agentSearcher/.venv/bin/agentsearcher mcp
# with environment variables:
claude mcp add agentsearcher -e AGENTSEARCHER_MCP_TAVILY_NAMES=1 -- /path/to/agentSearcher/.venv/bin/agentsearcher mcp
```

### Clients with a JSON config (LM Studio, Claude Desktop, etc.)

```json
{"mcpServers": {"agentsearcher": {
  "command": "/path/to/agentSearcher/.venv/bin/agentsearcher",
  "args": ["mcp"],
  "env": {"AGENTSEARCHER_LOG_LEVEL": "INFO"}
}}}
```

### HTTP clients and web UIs

Run `agentsearcher serve` (or `docker compose up -d`) and point the client at `http://127.0.0.1:8484/mcp`. With `API_KEYS`, add the header `Authorization: Bearer <key>` in the client's MCP settings.

```bash
claude mcp add --transport http agentsearcher http://127.0.0.1:8484/mcp
```

In stdio mode, logs go to stderr: stdout carries the protocol.
