[← Back to README](../README.md)

# How it works

- [Search](#search)
- [When the browser kicks in](#when-the-browser-kicks-in)
- [Ranking](#ranking)
- [Response size](#response-size)
- [Deduplication (`dedup`)](#deduplication-dedup)
- [Crawling (map and crawl)](#crawling-map-and-crawl)
- [PDF](#pdf)
- [Memory](#memory)

## Search

1. **Search engines.** The query goes to every engine in `SEARCH_BACKENDS` in parallel (through ddgs, one engine at a time: ddgs's built-in multi-engine mode loses results from slow engines when one of them fails). The rankings are merged with reciprocal rank fusion (each engine adds `1 / (60 + position)` to a URL), so URLs that several engines agree on go first, and a page that only one engine ranks high (often SEO spam) drops down. Duplicate URLs are merged. If no engine responds and `SEARXNG_URL` is set, the query goes to SearXNG.
2. **Candidates.** `ceil(max_results × 1.5) + 1` results are taken, and domain filters, `block` rules from `DOMAIN_RULES` and `max_per_domain` are applied.
3. **Fetching** (except `ultra-fast`). Pages are fetched in parallel: up to `MAX_FETCHES` (8) requests in total and up to 2 per host. Once `max_results` pages are loaded, the rest get 1 more second and are then dropped (`dropped` in the log). The total budget depends on the mode.
4. **Text.** HTML is converted to markdown with trafilatura (with tables and formatting; code blocks are pulled out of their wrappers), and PDF to its text layer.
5. **Chunks and ranking.** Text is split into chunks of ~`CHUNK_CHARS` characters, and each chunk keeps its heading path. Code blocks are never split. Then dedup, BM25 and the cross-encoder (below).
6. **Response.** The best `chunks_per_source` chunks are taken from each page, results are sorted by score, and the response size is capped.

### `search_depth` modes

| mode | pages | browser | cross-encoder | fetch budget |
|---|---|---|---|---|
| `ultra-fast` | no, snippets only | — | no | — |
| `fast` | yes | no | no, BM25 only | 4 s |
| `basic` | yes | yes | yes | 8 s |
| `advanced` | yes | yes | yes, 2 extra candidates per page | 15 s |

`advanced` is also stricter about deciding a page is "empty" (600-character threshold instead of 300, 50% snippet match instead of 30%) and enables `favor_recall` in trafilatura.

`include_raw_content` with `ultra-fast` switches the mode to `fast`: you can't get the full text without fetching it.

## When the browser kicks in

A page is first fetched with curl_cffi using a Chrome TLS fingerprint. The browser (headless Chromium via patchright) is used if:
- `challenge`: an anti-bot page (Cloudflare, DataDome, PerimeterX, DDoS-Guard, etc.);
- `spa: little text`: fewer than 300 characters were extracted (600 in advanced);
- `spa: empty root`: an empty `<div id="root|app|__next|__nuxt">` or a `<noscript>` asking to enable JS, with less than 2000 characters of text;
- `spa: script-heavy`: scripts make up more than 70% of the HTML, with less than 3000 characters of text;
- `snippet mismatch`: the page text barely matches the search engine snippet, which means the content is most likely loaded by JS.

The browser blocks images, media and fonts, waits for the anti-bot check to pass (up to 10 s), waits until the text stops growing, and scrolls the page. If rendering didn't produce more text, the static version is kept.

A domain is remembered as a "JS domain" for 1 hour only if the browser actually helped: there was an anti-bot page or an error, or rendering produced more than `2 × static + 500` characters. Later pages from that domain go straight to the browser. PDFs are never sent to the browser.

## Ranking

- **BM25** over the chunk text with its headings. Words are truncated to their first 6 characters: crude stemming that is good enough for Russian. The best `chunks_per_source` chunks of each page are kept (plus 2 in advanced).
- **Cross-encoder** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual, ONNX int8, CPU) rescores only these candidates. The model loads on the first request and is unloaded after `IDLE_TTL` of inactivity. Tokenization uses sentencepiece: HF `tokenizers` took ~280 MB for this model.
- **Result score** = 0.8 × relevance of the best chunk + 0.2 × position in search results. Domains from `include_domains` in `prefer` mode get +0.15.
- **Domain rules.** `DOMAIN_RULES` points to a text file with one rule per line; `#` starts a comment. `block` removes a domain (and its subdomains) from search results, `lower` takes 0.15 off the score, `raise` adds 0.15. Engine agreement doesn't catch content farms that rank well everywhere; this file does. The rules apply to search only: extract, crawl and map get URLs from the agent.

  ```
  block  pinterest.com
  lower  w3schools.com
  raise  docs.python.org   # official docs first
  ```

## Cache

Pages loaded by search, extract, crawl and map are reused for `CACHE_TTL` (15 min) by URL, so an `/extract` of a URL that a search just read is instant, and so are overlapping results of rephrased queries. A page read without the browser (`fast`) is not reused by modes that allow the browser; `advanced` and the other modes keep separate entries because they extract text differently. Failed loads and timeouts are not cached. The cache holds about `CACHE_MB` (64 MB) of page text; expired pages are dropped every 30 s.

Search engine results are cached for the same time per query and parameters, which also protects against engine rate limits when an agent repeats a query. Empty results are not cached.

## Concurrency

Requests run in parallel in one asyncio process; the shared resources have limits, and anything above a limit waits its turn: `MAX_FETCHES` downloads, 2 downloads per host, `MAX_RENDERS` browser renders. The reranker model is loaded once and shared; parallel requests score with it at the same time, each with `RERANK_THREADS` threads. On a machine with more cores and RAM, raise `MAX_FETCHES`, `MAX_RENDERS` and `RERANK_THREADS`.

## Response size

- `content` = up to `chunks_per_source` chunks of ~600 characters, joined with ` [...] `. 5 results come to roughly 3–5k characters;
- `raw_content` is truncated to `MAX_RAW_CHARS`;
- the whole response is capped at `MAX_RESPONSE_CHARS`: the last results first lose `raw_content`, then `content` is cut to 300 characters;
- `/extract` with `query` and `/crawl` with `instructions` return only the relevant chunks.

## Deduplication (`dedup`)

Texts are compared as sets of word 5-grams (shingles):
- **reprint:** a source with ≥ 80% of its shingles already present in sources ranked above it is dropped entirely. This catches the same article with a different header and footer, mirrors, and another version of the same PDF;
- **near-duplicate chunk:** a chunk with Jaccard similarity ≥ 0.8 to a chunk already kept is dropped. This catches shared banners and repeated blocks. The page itself stays;
- the copy ranked higher in the results is always the one kept.

`dedup=false` disables both checks. Identical URLs from different search engines are always merged.

## Crawling (map and crawl)

- Breadth-first from `url`. The root is first resolved through redirects, and all checks use the final address, while the original domain is also treated as internal (`docs.anthropic.com` → `platform.claude.com/docs/`).
- Level 0 fetches the root. Level 1 gets links from `llms.txt`, links from the root (at most `max_breadth`) and URLs from the sitemap. `max_breadth` doesn't apply to `llms.txt` or the sitemap.
- With `instructions`, candidates are sorted by BM25 over the link text (for `llms.txt`, the title) and the words of the path.
- If `llms.txt` and the sitemap already yield `limit` URLs, map doesn't fetch a single page.
- The `select_*`/`exclude_*` filters, `allow_external` and robots.txt apply to all sources.
- Map also returns last-level URLs without fetching them. Crawl fetches pages up to and including `max_depth`.
- With `instructions`, crawl returns only the relevant chunks of each page.

### sitemap, robots.txt, llms.txt

- **sitemap:** addresses are taken from `robots.txt`; otherwise `/sitemap.xml` and `/sitemap_index.xml` are tried. Sitemap indexes and gzip are supported, and up to 10 files and 10,000 URLs per site are read. If the root isn't `/`, only URLs under its path are taken, unless `select_paths` is set.
- **robots.txt:** honored only in crawl and map, using the rules for `*`. `Disallow` drops the URL, and `Crawl-delay` is respected (capped at 2 s; pages are then fetched one at a time). Disable with `RESPECT_ROBOTS=0`.
- **llms.txt:** markdown links in it become candidates. If the site has `llms.txt`, extract and crawl first try the `.md` version of the page (`page.md`, or `index.html.md` for addresses ending in `/`). It is accepted if the response is 200, the type is `text/markdown` or `text/plain`, it isn't HTML, and it has at least 200 characters. A page and its `.md` count as one URL.
- All three files are optional. A missing file, an error, an HTML placeholder instead of the file, or broken XML all mean "no data". Each file gets 3 s. Resolving the root and fetching these files together take at most two thirds of the crawl timeout; the rest goes to pages. The result, including a failed one, is cached for 1 hour per origin.

## PDF

A PDF is detected by `Content-Type: application/pdf`. The text layer is read with pypdfium2, up to 100 pages, each under a `## Page N` heading. The document title comes from the PDF metadata, or else from the file name. If there's no text (a scan), the page counts as failed: extract returns the error `PDF has no text layer`, and search uses the snippet. A 15-page arxiv paper is parsed in ~0.2 s. pdfium isn't thread-safe, so PDFs are parsed one at a time.

## Memory

Measured on an i7-7500U with 7.5 GB RAM (PSS, no double counting of shared memory):

| state | memory |
|---|---|
| idle server | ~75 MB |
| during `fast` (no model or browser) | ~150–250 MB |
| during `basic`/`advanced`, pages without JS | ~380 MB (~225 MB of it is the model) |
| + browser while rendering | another ~500 MB (Chromium ~300–380 + patchright node driver ~125) |
| after `IDLE_TTL` of inactivity | ~220 MB, model and browser unloaded |

Every 30 s a background task closes an idle browser, unloads the model and calls `malloc_trim`: without it, glibc holds on to ~300 MB after parsing pages.

To measure:

```bash
agentsearcher serve &
PID=$!
# make a few requests, then: total memory of the server and its child processes
ps -e -o pid=,ppid= | awk -v r=$PID '{p[$1]=$2} END {for (i in p) {q=i; while (q > 1 && q != r) q=p[q]; if (q == r) print i}}' \
  | while read p; do awk '/^Pss:/{print $2}' /proc/$p/smaps_rollup 2>/dev/null; done \
  | awk '{t+=$1} END {print int(t/1024) " MB"}'
```
