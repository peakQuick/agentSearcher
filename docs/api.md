[← Back to README](../README.md)

# HTTP API

```bash
agentsearcher serve            # http://127.0.0.1:8484, address set with --host/--port or env HOST/PORT
```

All POST endpoints accept JSON and respond in the Tavily API format. Unknown parameters are ignored, so newer client versions don't break.

- [Authentication](#authentication)
- [Errors](#errors)
- [`POST /search`](#post-search)
- [`POST /extract`](#post-extract)
- [`POST /map`](#post-map)
- [`POST /crawl`](#post-crawl)
- [`GET /usage`, `GET /health`](#get-usage-get-health)
- [`GET /stats`, `GET /`](#get-stats-get-)
- [tavily-python](#tavily-python)

## Authentication

If `AGENTSEARCHER_API_KEYS` is not set, any request with any key is accepted. If it is set (`tvly-a,tvly-b`), a key is required in the `Authorization: Bearer <key>` header or as `api_key` in the body. A wrong key returns 401.

## Errors

Errors come as `{"detail": {"error": "..."}}`:
- 400: invalid parameters (type, range, incompatible fields, bad regex);
- 401: wrong key.

A URL that fails to load (404, anti-bot, timeout, blocked address) is not a request error. In extract it goes to `failed_results`; in search, the search engine snippet is used instead of the page.

## `POST /search`

| parameter | type / values | default | notes |
|---|---|---|---|
| `query` | string, required | | |
| `search_depth` | `ultra-fast`, `fast`, `basic`, `advanced` | `basic` | see [how-it-works](how-it-works.md#search_depth-modes) |
| `max_results` | 0–20 | 5 | |
| `chunks_per_source` | 1–3 | 3 | how many of the page's best chunks go into `content` |
| `topic` | `general`, `news`, `finance` | `general` | `news` searches news; `finance` behaves like `general` |
| `time_range` | `day`, `week`, `month`, `year` (or `d`, `w`, `m`, `y`) | none | search engine time filter |
| `days` | ≥ 1 | none | same, converted to the nearest `time_range` |
| `start_date`, `end_date` | `YYYY-MM-DD` | none | results with a known date outside the window are dropped |
| `filter_by_published_date` | bool | false | drop results with no date or outside the window |
| `include_published_date` | bool | false | add `published_date` (always added for `news`) |
| `include_domains` | up to 300 domains | [] | only these domains |
| `include_domains_mode` | `restrict`, `prefer` | `restrict` | `prefer`: don't filter, boost these domains' score instead (+0.15) |
| `exclude_domains` | up to 150 domains | [] | |
| `include_answer` | bool, `basic`, `advanced` | false | `answer`: short (`basic`) or detailed (`advanced`) |
| `include_raw_content` | bool, `markdown`, `text` | false | full page text, up to `MAX_RAW_CHARS` |
| `include_images` | bool | false | `images` in the response (image search) and up to 5 page images in each result |
| `include_image_descriptions` | bool | false | `images` as `{url, description}`, where description is the image title from search |
| `include_favicon` | bool | false | |
| `country`, `language` | string | none | search engine region; without them the region is guessed from the query language |
| `safe_search` | bool | false | |
| `include_usage` | bool | false | `usage.credits` |
| `dedup` | bool | true | extension: drop reprints and near-duplicate chunks |
| `max_per_domain` | ≥ 1 | none | extension: at most N results per domain |

Accepted for compatibility but have no effect: `exact_match` and `filter_by_language` (requires `language`). `auto_parameters` only echoes back the given `topic` and `search_depth`.

Response:

```jsonc
{
  "query": "...",
  "answer": "...",          // or null
  "images": [],
  "results": [
    {"url": "...", "title": "...", "content": "chunk [...] chunk", "score": 0.93,
     "raw_content": null, "id": "..."}
  ],
  "follow_up_questions": null,
  "response_time": 6.2,
  "request_id": "..."
}
```

`content` is the page's best `chunks_per_source` chunks (~600 characters each), joined with ` [...] `. If the page failed to load, `content` is the search engine snippet. `score` ranges from 0 to 1: 80% comes from the relevance of the best chunk, 20% from the position in search results.

`answer`: without `LLM_BASE_URL`, it is the two most relevant sentences from the results. With `LLM_BASE_URL`, an OpenAI-compatible model generates the answer from the 4 best chunks (8 for `advanced`). If the model is unavailable, it falls back to the extractive answer.

`max_per_domain` is applied to candidates before pages are fetched. If there are few other domains in the results, you get fewer than `max_results` results.

## `POST /extract`

| parameter | type / values | default | notes |
|---|---|---|---|
| `urls` | string or list of 1–20 URLs | | |
| `query` | string | none | return only chunks relevant to the query |
| `chunks_per_source` | 1–5 | 3 | how many chunks to return with `query` |
| `extract_depth` | `basic`, `advanced` | `basic` | page timeout 10 / 30 s; `advanced` is stricter about "empty" pages and enables `favor_recall` |
| `format` | `markdown`, `text` | `markdown` | |
| `timeout` | 1–60 s | by `extract_depth` | |
| `include_images`, `include_favicon` | bool | false | |
| `include_usage` | bool | false | |

Response: `{"results": [{"url", "raw_content", "images"?, "favicon"?}], "failed_results": [{"url", "error"}], "response_time", "request_id"}`.

If the site has `llms.txt` (or the requested URL ends in `.md`), the `.md` version of the page is tried first. robots.txt is not applied in extract: the URL was requested explicitly. PDFs are supported.

## `POST /map`

| parameter | type / values | default | notes |
|---|---|---|---|
| `url` | string; `https://` is added if there is no scheme | | crawl root |
| `instructions` | string | none | links with matching text and path go first |
| `max_depth` | 1–5 | 1 | how many link hops |
| `max_breadth` | 1–500 | 20 | how many new links to take from one page |
| `limit` | ≥ 1 | 50 | how many URLs to return |
| `select_paths`, `exclude_paths` | path regex | [] | `"/docs/.*"` |
| `select_domains`, `exclude_domains` | host regex | [] | |
| `allow_external` | bool | true | follow links to other domains |
| `timeout` | 10–150 s | 150 | when it runs out, whatever was collected is returned |
| `include_usage` | bool | false | |

Response: `{"base_url", "results": ["url", ...], "response_time", "request_id"}`. URLs at the last level are returned without being fetched. How sitemap, robots.txt and llms.txt are used is described in [how-it-works](how-it-works.md#crawling-map-and-crawl).

## `POST /crawl`

The `/map` parameters, plus:

| parameter | type / values | default | notes |
|---|---|---|---|
| `chunks_per_source` | 1–5 | 3 | with `instructions`: how many chunks per page to return |
| `extract_depth` | `basic`, `advanced` | `basic` | page timeout 10 / 30 s |
| `format` | `markdown`, `text` | `markdown` | |
| `include_images`, `include_favicon` | bool | false | |
| `dedup` | bool | true | extension: with `instructions`, drop repeated chunks (headers, banners) and reprints |

Response: `{"base_url", "results": [{"url", "raw_content"}], "response_time", "request_id"}`. Without `instructions`, `raw_content` is the whole page (up to `MAX_RAW_CHARS`); with `instructions`, only the relevant chunks joined with ` [...] `. When the response exceeds `MAX_RESPONSE_CHARS`, the last pages are cut to 300 characters each.

## `GET /usage`, `GET /health`

`/usage` returns a "credits" counter in the Tavily format. It has no effect on anything and lives in process memory, so it resets on restart. `/health` returns `{"status": "ok"}`.

## `GET /stats`, `GET /`

`/stats` is what the dashboard shows, as JSON (requires the key if `API_KEYS` is set):
- `requests`: `ok`, `failed` and total `seconds` per kind (`search`, `extract`, `crawl`, `map`), counted for the API, MCP and CLI alike;
- `pages`: `loaded`, `failed`, `cached` (served from the page cache), `rendered` (went through the browser);
- `cache`, `components` (`browser` running, `model` loaded), `recent` (last 30 requests);
- `system`: CPU and memory of the server with its child processes (Chromium, the node driver; PSS on Linux, RSS elsewhere), and of the whole machine.

Everything resets on restart.

`/` is the dashboard: the same numbers, refreshed every 2 s, and a playground to run search, extract, crawl and map from the browser. The page itself opens without a key; with `API_KEYS`, it asks for one and keeps it in the browser's local storage.

## tavily-python

```python
from tavily import TavilyClient

client = TavilyClient(api_key="local", api_base_url="http://127.0.0.1:8484")
r = client.search("python asyncio tutorial", search_depth="basic", max_results=3)
r = client.extract(urls=["https://react.dev/learn"], query="useState")
r = client.map("https://docs.python.org/3/", max_depth=2, limit=50, allow_external=False)
r = client.crawl("https://react.dev/learn", instructions="state hooks", limit=10)
```

Other software connects the same way: wherever Tavily's base URL or endpoint is configurable, set it to `http://127.0.0.1:8484`.
