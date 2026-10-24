<div align="center">

# agentsearcher

**Local, Tavily-compatible web search and extraction for AI agents. CPU-only, no cloud keys.**

[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)
[![Tavily-compatible](https://img.shields.io/badge/API-Tavily--compatible-6E56CF)](docs/api.md)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20HTTP-black)](docs/mcp.md)

</div>

One process serves `POST /search`, `/extract`, `/crawl` and `/map` in the Tavily API format, so Tavily clients connect by changing the base URL. The same engine is available as an MCP server and as a CLI. Everything runs on the CPU, with no cloud API keys.

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Search depth](#search-depth)
- [Documentation](#documentation)
- [Development](#development)
- [License](#license)

## Features

- `/search`, `/extract`, `/crawl`, `/map`, `/usage` and `/health` with Tavily-shaped responses. Compatibility is covered by tests using the official `tavily-python` client.
- MCP server (stdio and streamable HTTP) with the tools `web_search`, `web_extract`, `web_crawl` and `web_map`. An env flag renames them to `tavily_*`.
- JavaScript pages: when a plain fetch isn't enough, the page is rendered in headless Chromium.
- PDF: the text layer is read. No OCR.
- `sitemap.xml`, `robots.txt` and `llms.txt` are used by map/crawl, and `.md` versions of pages by extract/crawl. Sites without these files work too.
- Reprints and near-duplicate chunks are dropped (`dedup`), and results can be capped per domain (`max_per_domain`).
- SSRF protection: localhost and private networks are blocked by default.
- Response size is capped to fit a small context window. The model and the browser are unloaded after a period of inactivity.

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

```bash
git clone https://github.com/peakQuick/agentSearcher.git
cd agentSearcher
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/patchright install chromium     # Chromium for JS pages, installed to ~/.cache/ms-playwright
.venv/bin/agentsearcher fetch-model       # ~120 MB reranker to ~/.cache/huggingface, one time
```

Below, `agentsearcher` means `.venv/bin/agentsearcher` (or run `source .venv/bin/activate`).

## Quick start

### CLI

```bash
agentsearcher search "what's new in python 3.14"
agentsearcher extract https://react.dev/learn -q "useState"
agentsearcher map https://docs.python.org/3/
```

### HTTP API

```bash
agentsearcher serve                                    # http://127.0.0.1:8484
curl -s localhost:8484/search -H 'Content-Type: application/json' \
  -d '{"query": "python asyncio tutorial", "max_results": 3}'
```

With `tavily-python`:

```python
from tavily import TavilyClient

client = TavilyClient(api_key="local", api_base_url="http://127.0.0.1:8484")
print(client.search("python asyncio tutorial", max_results=3)["results"][0]["url"])
```

### MCP (Claude Code)

```bash
claude mcp add agentsearcher -- /path/to/agentSearcher/.venv/bin/agentsearcher mcp
```

Other clients: [docs/mcp.md](docs/mcp.md).

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
