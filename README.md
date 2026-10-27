<div align="center">

# agentsearcher

**Lightweight local web retrieval for AI agents: search, extract, crawl and map in one CPU-only process. No cloud keys.**

[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)
[![Tavily-compatible](https://img.shields.io/badge/API-Tavily--compatible-6E56CF)](docs/api.md)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20HTTP-black)](docs/mcp.md)

</div>

An agent that works with the web needs to find pages, read them (including JavaScript pages and PDFs) and sometimes walk a whole site. agentsearcher does all of it in one Python process and returns only the passages that matter, sized for a small context window. It is available as an MCP server, an HTTP API (Tavily-compatible) and a CLI, with a dashboard in the browser.

## Contents

- [Why](#why)
- [Features](#features)
- [How it works](#how-it-works)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Search depth](#search-depth)
- [Documentation](#documentation)
- [Development](#development)
- [License](#license)

## Why

Self-hosted web retrieval usually means assembling several services: a metasearch engine such as SearXNG for search, a crawler such as Crawl4AI for page content, or a self-hosted Firecrawl, which runs as an API plus a browser service, Redis, RabbitMQ and Postgres. agentsearcher is one process instead:

- **Light.** ~75 MB idle; ~380 MB while ranking with the model; Chromium only starts for pages that need it. The model and the browser are unloaded after 2 minutes idle. Measured on a laptop i7-7500U with 7.5 GB RAM, no GPU ([details](docs/how-it-works.md#memory)).
- **Search and reading together.** One query fetches, renders, cleans and ranks the result pages, so the agent gets relevant passages instead of snippets or whole pages.
- **Made for agents.** Responses fit a small context window; reprints and near-duplicates are dropped; results the search engines agree on rank higher than SEO pages one engine pushes up.
- **Easy to run.** `pipx install` or `docker compose up`. Nothing to configure to get started.

## Features

- MCP server (stdio, or streamable HTTP at `/mcp` on the API port) with the tools `web_search`, `web_extract`, `web_crawl` and `web_map`. An env flag renames them to `tavily_*`.
- JavaScript pages: when a plain fetch isn't enough, the page is rendered in headless Chromium.
- PDF: the text layer is read. No OCR.
- `sitemap.xml`, `robots.txt` and `llms.txt` are used by map/crawl, and `.md` versions of pages by extract/crawl. Sites without these files work too.
- Reprints and near-duplicate chunks are dropped (`dedup`), and results can be capped per domain (`max_per_domain`).
- SSRF protection: localhost and private networks are blocked by default.
- Response size is capped to fit a small context window. The model and the browser are unloaded after a period of inactivity.
- Pages and search results are cached for 15 minutes; optional block/lower/raise rules per domain.
- Dashboard at `/`: request and page counters, CPU and memory (with the browser), recent requests, and a playground for search, extract, crawl and map.
- Drop-in for Tavily clients: `/search`, `/extract`, `/crawl`, `/map`, `/usage` respond in the Tavily API format, so existing clients only change the base URL. Covered by tests with the official `tavily-python` client.

## How it works

```
query ─► search engines (ddgs: duckduckgo, yahoo, yandex, brave, mojeek, bing; optional SearXNG)
      ─► fetch pages with curl_cffi (Chrome TLS fingerprint)
           └─ empty page / SPA / anti-bot / text doesn't match snippet ─► patchright Chromium
      ─► trafilatura → markdown (PDF → text layer) ─► ~600-char chunks
      ─► BM25 ─► multilingual cross-encoder (ONNX int8, CPU)
      ─► dedup ─► only the best chunks of each page go into the response
```

Details: [docs/how-it-works.md](docs/how-it-works.md).

## Installation

**Docker** (Chromium and the model are inside the image):

```bash
git clone https://github.com/peakQuick/agentSearcher.git && cd agentSearcher
docker compose up -d --build        # API and MCP at http://127.0.0.1:8484, MCP at /mcp
```

Settings go under `environment:` in [compose.yaml](compose.yaml). On macOS and Windows, Docker Desktop runs a VM, which costs extra RAM; there, the pipx install below is lighter.

**pipx** (no venv to manage; pipx is in most package managers: `apt install pipx`, `brew install pipx`, `pacman -S python-pipx`):

```bash
pipx install git+https://github.com/peakQuick/agentSearcher
agentsearcher setup                 # one time: Chromium (~/.cache/ms-playwright) and the ~120 MB reranker (~/.cache/huggingface)
agentsearcher serve
```

Update with `pipx upgrade agentsearcher`. On a bare Linux server, `agentsearcher setup --with-deps` also installs Chromium's system libraries (needs root).

**From source** (development):

```bash
git clone https://github.com/peakQuick/agentSearcher.git && cd agentSearcher
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/agentsearcher setup
```

Below, `agentsearcher` means the installed command (`.venv/bin/agentsearcher` for a source install).

## Quick start

### CLI

```bash
agentsearcher search "what's new in python 3.14"
agentsearcher extract https://react.dev/learn -q "useState"
agentsearcher map https://docs.python.org/3/
```

### HTTP API

```bash
agentsearcher serve                                    # dashboard: open http://127.0.0.1:8484/
curl -s localhost:8484/search -H 'Content-Type: application/json' \
  -d '{"query": "python asyncio tutorial", "max_results": 3}'
```

With `tavily-python`:

```python
from tavily import TavilyClient

client = TavilyClient(api_key="local", api_base_url="http://127.0.0.1:8484")
print(client.search("python asyncio tutorial", max_results=3)["results"][0]["url"])
```

### MCP

With `serve` or Docker running, HTTP clients and web UIs connect to `http://127.0.0.1:8484/mcp`:

```bash
claude mcp add --transport http agentsearcher http://127.0.0.1:8484/mcp
```

Or let the client start its own server over stdio:

```bash
claude mcp add agentsearcher -- agentsearcher mcp
```

Other clients: [docs/mcp.md](docs/mcp.md).

## Configuration

The defaults work as is. Settings are environment variables with the `AGENTSEARCHER_` prefix:

```bash
AGENTSEARCHER_API_KEYS=change-me agentsearcher serve     # pipx / source
```

With Docker, put them under `environment:` in [compose.yaml](compose.yaml); for MCP over stdio, in the client's `env` block. `GET /stats` shows the values in effect (secrets hidden).

The ones people usually change: `API_KEYS` (before opening the server to a network), `PROXY_URL` (search engines block your IP), `LLM_BASE_URL` (answers with `include_answer`), `MAX_REQUESTS` / `MAX_RENDERS` (a machine with little RAM). All variables and ready-made recipes: [docs/configuration.md](docs/configuration.md).

## Search depth

| mode | what it does | warm latency\* |
|---|---|---|
| `ultra-fast` | search engine snippets only, no pages fetched | ~1 s |
| `fast` | pages without the browser, BM25 ranking | 3–4 s |
| `basic` | + browser for JS and anti-bot pages, + cross-encoder | 6–9 s |
| `advanced` | + more candidates, `favor_recall`, stricter about "empty" pages | 7–10 s |

\* i7-7500U, 5 results. The first request takes longer: ~1.5 s to load the model and ~1 s to start the browser.

## Documentation

| doc | what's inside |
|---|---|
| [CLI](docs/cli.md) | commands, flags and how to read the log |
| [HTTP API](docs/api.md) | endpoints, parameters, responses and errors |
| [MCP](docs/mcp.md) | tools and client setup |
| [Configuration](docs/configuration.md) | environment variables |
| [How it works](docs/how-it-works.md) | pipeline, browser, ranking, crawling, PDF, dedup, memory |
| [Security](docs/security.md) | SSRF protection |
| [Limitations](docs/limitations.md) | known limitations and possible problems |

## Development

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -m "not live"   # offline: local fixture sites (SPA, challenge, PDF, sitemap...), tavily-python contract
.venv/bin/python -m pytest -m live         # needs internet: tavily-python search at every search_depth
```

## License

Copyright (C) 2026 peakQuick

[GNU Affero General Public License v3.0](LICENSE). If you modify this project and let others use it over a network, you must make the source code of your modified version available to those users.
