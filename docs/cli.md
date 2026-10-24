[← Back to README](../README.md)

# CLI

Every command runs one request and prints compact text. With `--json`, it prints the raw response in the Tavily API format. Logs go to stderr.

## Commands

| command | what it does |
|---|---|
| `serve [--host H] [--port P]` | HTTP API, `127.0.0.1:8484` by default |
| `mcp [--http] [--host H] [--port P]` | MCP server: stdio, or streamable HTTP on `PORT+1` (8485) with `--http` |
| `search QUERY` | search |
| `extract URL [URL ...]` | page contents |
| `crawl URL` | crawl a site with page contents |
| `map URL` | list a site's URLs |
| `fetch-model` | download the reranker and check that it works |

### `search`

| flag | default | notes |
|---|---|---|
| `-d, --depth` | `basic` | `ultra-fast`, `fast`, `basic`, `advanced` |
| `-n, --max-results` | `5` | |
| `--topic` | `general` | `general`, `news`, `finance` |
| `--answer` | off | add `answer` |
| `--raw` | off | add the full page text (`raw_content`) |
| `--max-per-domain N` | no limit | at most N results per domain |
| `--no-dedup` | off | keep reprints and near-duplicate chunks |
| `--json` | off | raw JSON |

### `extract`

| flag | default | notes |
|---|---|---|
| `-q, --query` | none | return only chunks relevant to the query |
| `-d, --depth` | `basic` | `basic` (10 s timeout), `advanced` (30 s) |
| `--format` | `markdown` | `markdown`, `text` |
| `--json` | off | |

### `crawl` and `map`

Shared flags:

| flag | default | notes |
|---|---|---|
| `-i, --instructions` | none | what to look for: sets the crawl order, and in crawl also which chunks to return |
| `--max-depth` | `1` | how many link hops |
| `--max-breadth` | `20` | how many new links to take from one page |
| `--limit` | `50` | crawl: how many pages to fetch; map: how many URLs to return |
| `--select-path` | none | path regex, repeatable |
| `--exclude-path` | none | path regex, repeatable |
| `--external` | off | follow links to other domains too (on by default in the HTTP API, as in Tavily) |
| `--timeout` | `150` | seconds for the whole crawl |
| `--json` | off | |

`crawl` only: `-d, --depth` (`basic`, `advanced`), `--format` (`markdown`, `text`), `--no-dedup`.

## Examples

```bash
agentsearcher search "what's new in python 3.14"                # basic, 5 results
agentsearcher search "rust async runtimes" -d advanced -n 8
agentsearcher search "python asyncio tutorial" -d ultra-fast     # snippets only, ~1 s
agentsearcher search "nvidia earnings" --topic news --answer
agentsearcher search "react hooks" --raw
agentsearcher search "react hooks" --max-per-domain 1
agentsearcher search "nvidia earnings" --topic news --no-dedup

agentsearcher extract https://react.dev/learn
agentsearcher extract https://react.dev/learn -q "useState"
agentsearcher extract https://vk.com/python -d advanced          # slow JS site
agentsearcher extract https://arxiv.org/pdf/1706.03762 -q "multi-head attention"

agentsearcher map https://docs.python.org/3/
agentsearcher map https://docs.python.org/3/ --max-depth 2 --limit 100 --select-path '/3/library/.*'
agentsearcher crawl https://react.dev/learn --limit 10
agentsearcher crawl https://react.dev/learn -i "state hooks" --limit 10
```

## Log

The log shows how each page was handled:

```
static   https://realpython.com/...       (16597 chars)       ← plain HTTP was enough
browser  https://vk.com/python            (spa: little text)  ← needed the browser, reason in parentheses
browser  https://stackoverflow.com/...    (challenge)         ← anti-bot, the browser waits for the check to pass
pdf      https://arxiv.org/pdf/1706.03762 (39769 chars)       ← PDF text layer
markdown https://.../page.md              (5120 chars)        ← .md version of the page (site with llms.txt)
failed   https://www.ozon.ru/             (HTTP 403)          ← failed to load; search uses the snippet instead
dropped  https://...                      (enough pages or budget spent)
engines answered: 5/5                                         ← how many search engines responded
site     https://... robots=True llms.txt=False (0 links)
sitemap  https://...: 4307 urls from 3 files
blocked browser request to http://10...                       ← SSRF protection aborted a browser request
```

Reasons for switching to the browser: `challenge`, `spa: little text`, `spa: empty root`, `spa: script-heavy`, `snippet mismatch (N%)`, `known js domain`.

More detail: `AGENTSEARCHER_LOG_LEVEL=DEBUG agentsearcher search ...` (this also shows which search engine failed).
