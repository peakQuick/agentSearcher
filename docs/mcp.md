[← Back to README](../README.md)

# MCP

```bash
agentsearcher mcp            # stdio
agentsearcher mcp --http     # streamable HTTP at http://127.0.0.1:8485 (PORT+1)
```

The tools return compact text (title, URL, chunks) rather than JSON, sized for a small context window.

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

### HTTP-only clients

Run `agentsearcher mcp --http` and point the client at `http://127.0.0.1:8485/mcp`.

In stdio mode, logs go to stderr: stdout carries the protocol.
