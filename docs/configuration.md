[← Back to README](../README.md)

# Configuration

Nothing needs to be configured to get started. Everything is set through environment variables with the `AGENTSEARCHER_` prefix (`MAX_REQUESTS` below means `AGENTSEARCHER_MAX_REQUESTS`). Values are read once, at process start: restart the server after a change.

## How to set them

**pipx or source install**, for one run:

```bash
AGENTSEARCHER_API_KEYS=change-me AGENTSEARCHER_MAX_REQUESTS=2 agentsearcher serve
```

or for the whole shell session, `export AGENTSEARCHER_API_KEYS=change-me` (put it in `~/.bashrc` to keep it). For a systemd service, use `Environment=AGENTSEARCHER_...` lines in the unit.

**Docker**: under `environment:` in [compose.yaml](../compose.yaml), then `docker compose up -d`:

```yaml
    environment:
      AGENTSEARCHER_API_KEYS: change-me
      AGENTSEARCHER_MAX_REQUESTS: 2
```

**MCP over stdio** (the client starts the server itself): in the client's `env` block, see [mcp.md](mcp.md):

```json
"env": {"AGENTSEARCHER_PROXY_URL": "socks5://127.0.0.1:1080"}
```

**Check what is in effect**: `GET /stats` (or `curl -s localhost:8484/stats`) returns a `settings` block with every value the server actually uses. Keys show only whether they are set, and a proxy password is hidden. A misspelled variable name is silently ignored, so if a value there is still the default, check the spelling.

## Usually worth knowing

| variable | default | description |
|---|---|---|
| `HOST`, `PORT` | `127.0.0.1`, `8484` | HTTP API address. `0.0.0.0` opens it to the network: set `API_KEYS` then. MCP in `--http` mode uses `PORT+1` |
| `API_KEYS` | empty (allow everyone) | comma-separated keys, see [api.md](api.md#authentication) |
| `PROXY_URL` | empty | proxy for search engines, curl_cffi and the browser (`http://...`, `socks5://...`). Helps when search engines or sites block your IP |
| `LLM_BASE_URL` | empty | OpenAI-compatible endpoint for `include_answer`: `http://localhost:11434/v1` (Ollama), `http://localhost:8080/v1` (llama.cpp) |
| `LLM_MODEL` | `local` | model name for `LLM_BASE_URL` |
| `LLM_API_KEY` | empty | key for `LLM_BASE_URL` |
| `MAX_REQUESTS` | CPU cores | search/extract/crawl/map requests running at once; the rest wait in a queue, see [how-it-works.md](how-it-works.md#concurrency) |
| `MAX_CRAWLS` | cores / 4, at least 1 | of those, crawl and map requests at once, so they don't take every slot |
| `ALLOW_PRIVATE_URLS` | `0` | `1`: allow requests to localhost and private networks (local docs, intranet), see [security.md](security.md) |
| `MCP_TAVILY_NAMES` | `0` | `1`: MCP tools are named `tavily_*` instead of `web_*` |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail, e.g. which search engine failed |

## Recipes

**Low RAM (4 GB or less) or a weak CPU.** The peak is mostly Chromium and the model, ~1 GB with the browser open; heavy crawls in parallel add to it. Run fewer things at once:

```bash
AGENTSEARCHER_MAX_REQUESTS=2 AGENTSEARCHER_MAX_RENDERS=1 AGENTSEARCHER_MAX_CPU_JOBS=1 agentsearcher serve
```

Requests above the limit wait instead of failing. `IDLE_TTL=30` frees the browser and model sooner after a burst.

**Many cores and plenty of RAM.** `MAX_REQUESTS` and `MAX_CRAWLS` already follow the core count. Raise what they share: `MAX_CPU_JOBS` (up to the core count), `MAX_FETCHES=16`, `MAX_RENDERS=4`, `RERANK_THREADS=4`, `RERANK_BUDGET=300`.

**Unbounded.** No queue and no caps, everything runs at once (like v0.1.1b0, which had no limits at all). Fastest with headroom; under heavy parallel load pages can time out:

```bash
AGENTSEARCHER_MAX_REQUESTS=1000 AGENTSEARCHER_MAX_CRAWLS=1000 AGENTSEARCHER_MAX_CPU_JOBS=1000 \
AGENTSEARCHER_RERANK_BUDGET=100000 agentsearcher serve
```

**A server for several people or agents.** `HOST=0.0.0.0` with `API_KEYS`, see [security.md](security.md#exposing-the-server).

**Search engines return nothing / captchas.** `PROXY_URL`, `SEARXNG_URL` as a fallback, or drop the failing engine from `SEARCH_BACKENDS`; see [limitations.md](limitations.md).

## Fine tuning

Rarely needs changing.

| variable | default | description |
|---|---|---|
| `SEARCH_BACKENDS` | `duckduckgo,yahoo,yandex,brave,mojeek,bing` | ddgs search engines, queried in parallel |
| `SEARXNG_URL` | empty | SearXNG as a fallback when every search engine returned nothing or an error |
| `DOMAIN_RULES` | empty | path to a file of `block`/`lower`/`raise <domain>` lines for search, see [how-it-works.md](how-it-works.md#ranking) |
| `RESPECT_ROBOTS` | `1` | `0`: crawl and map ignore `Disallow` and `Crawl-delay` (sitemap URLs from robots.txt are still used) |
| `IDLE_TTL` | `120` | seconds of inactivity before the model is unloaded and the browser closed |
| `CACHE_TTL` | `900` (15 min) | how long loaded pages and search engine results are reused, see [how-it-works.md](how-it-works.md#cache). `0` turns the cache off |
| `CACHE_MB` | `64` | approximate page cache size (characters of page text); least recently used pages are dropped first |
| `MAX_CPU_JOBS` | cores / 2, at least 1 | page parsing and reranking jobs at once, across all requests |
| `QUEUE_TIMEOUT` | `120` | seconds a request may wait in the queue before it fails with "server busy" (HTTP 503) |
| `MAX_FETCHES` | `8` | concurrent page downloads for the whole server. Raise on a fast machine and network |
| `MAX_RENDERS` | `2` | concurrent browser renders (and Chromium renderer processes). Each extra render costs more RAM |
| `RERANK_THREADS` | `2` | ONNX Runtime threads for the reranker |
| `RERANK_BUDGET` | `100` | at most this many passages per request are rescored by the model, the best by BM25; the rest follow in BM25 order. Matters for crawl with `instructions`. Raise for better ranking of large crawls at the cost of CPU time |
| `CHUNK_CHARS` | `600` | text chunk size |
| `MAX_RAW_CHARS` | `20000` | `raw_content` limit per page |
| `MAX_RESPONSE_CHARS` | `60000` | limit for the whole search and crawl response |
| `MAX_HTML_BYTES` | `3145728` (3 MB) | HTML beyond this is not downloaded; the rest is cut off |
| `MAX_PDF_BYTES` | `10485760` (10 MB) | same for PDF. A truncated PDF can't be read, and the page counts as failed |

## Downloaded files

They live outside the project:
- the reranker `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (`onnx/model_quint8_avx2.onnx`) is in the Hugging Face cache, `~/.cache/huggingface/hub`. Change the location with `HF_HOME`;
- Chromium is in `~/.cache/ms-playwright`. Change the location with `PLAYWRIGHT_BROWSERS_PATH`.
