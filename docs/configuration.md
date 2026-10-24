[← Back to README](../README.md)

# Configuration

Everything is set through environment variables with the `AGENTSEARCHER_` prefix, for example `AGENTSEARCHER_PORT=9000`.

| variable | default | description |
|---|---|---|
| `HOST`, `PORT` | `127.0.0.1`, `8484` | HTTP API address. MCP in `--http` mode uses `PORT+1` |
| `API_KEYS` | empty (allow everyone) | comma-separated keys, see [api.md](api.md#authentication) |
| `SEARCH_BACKENDS` | `duckduckgo,yahoo,yandex,brave,mojeek,bing` | ddgs search engines, queried in parallel |
| `SEARXNG_URL` | empty | SearXNG as a fallback when every search engine returned nothing or an error |
| `PROXY_URL` | empty | proxy for search engines, curl_cffi and the browser (`http://...`, `socks5://...`) |
| `RESPECT_ROBOTS` | `1` | `0`: crawl and map ignore `Disallow` and `Crawl-delay` (sitemap URLs from robots.txt are still used) |
| `ALLOW_PRIVATE_URLS` | `0` | `1`: allow requests to localhost and private networks, see [security.md](security.md) |
| `MCP_TAVILY_NAMES` | `0` | `1`: MCP tools are named `tavily_*` instead of `web_*` |
| `IDLE_TTL` | `120` | seconds of inactivity before the model is unloaded and the browser closed |
| `CHUNK_CHARS` | `600` | text chunk size |
| `MAX_RAW_CHARS` | `20000` | `raw_content` limit per page |
| `MAX_RESPONSE_CHARS` | `60000` | limit for the whole search and crawl response |
| `MAX_HTML_BYTES` | `3145728` (3 MB) | HTML beyond this is not downloaded; the rest is cut off |
| `MAX_PDF_BYTES` | `10485760` (10 MB) | same for PDF. A truncated PDF can't be read, and the page counts as failed |
| `RERANK_THREADS` | `2` | ONNX Runtime threads for the reranker |
| `LLM_BASE_URL` | empty | OpenAI-compatible endpoint for `include_answer`: `http://localhost:11434/v1` (Ollama), `http://localhost:8080/v1` (llama.cpp) |
| `LLM_MODEL` | `local` | model name for `LLM_BASE_URL` |
| `LLM_API_KEY` | empty | key for `LLM_BASE_URL` |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail |

Values are read once, at process start.

Where downloaded files live (outside the project):
- the reranker `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (`onnx/model_quint8_avx2.onnx`) is in the Hugging Face cache, `~/.cache/huggingface/hub`. Change the location with `HF_HOME`;
- Chromium is in `~/.cache/ms-playwright`. Change the location with `PLAYWRIGHT_BROWSERS_PATH`.
