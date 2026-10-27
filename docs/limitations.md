[← Back to README](../README.md)

# Limitations and known issues

What the project can't do, and what can go wrong in practice: what it looks like and what to do about it.

- [Where it was tested](#where-it-was-tested)
- [Search engines](#search-engines)
- [Anti-bot and closed sites](#anti-bot-and-closed-sites)
- [Speed](#speed)
- [Memory](#memory)
- [PDF](#pdf)
- [Crawling (crawl, map)](#crawling-crawl-map)
- [Dedup and filters](#dedup-and-filters)
- [Tavily compatibility](#tavily-compatibility)
- [Security](#security)

## Where it was tested

Development and tests: Linux (Arch), Python 3.14, i7-7500U, 7.5 GB RAM, no GPU. It has not been run on macOS, Windows or other Python versions. The syntax is checked for 3.11 compatibility with ruff, but the tests have not been run on 3.11.

## Search engines

- Search goes through ddgs, which scrapes search engine result pages. When an engine changes its markup or rate-limits requests, it starts silently returning nothing.
  - **Symptom:** the log shows `engines answered: 3/6` instead of `6/6`, and results are thinner.
  - **Fix:** `AGENTSEARCHER_LOG_LEVEL=DEBUG` shows which engine failed. `PROXY_URL`, `SEARXNG_URL` as a fallback, updating `ddgs`, or removing the engine from `SEARCH_BACKENDS` can help.
- ddgs 9.16 disables bing, google and yandex. We enable yandex ourselves: it works and is the best at Russian-language search. Bing is enabled too, but its results with empty links are discarded. Google is not used.
- If no engine responds and `SEARXNG_URL` is not set, search returns an empty `results` list, not an error.

## Anti-bot and closed sites

- The headless browser doesn't always get past Cloudflare on StackOverflow. Reddit ("Prove your humanity"), Ozon and Figma block it.
  - **Symptom:** `failed ... (blocked by anti-bot challenge)` or `(HTTP 403)`. In extract the URL goes to `failed_results`; in search the snippet is used instead of the page.
  - **Fix:** a `PROXY_URL` with a "clean" IP sometimes helps. The project can't solve captchas.
- Pages behind a login are not accessible: cookies and sessions are not used.

## Speed

- Everything runs on your CPU, so it's slower than cloud Tavily: `basic` takes 6–9 s on an i7-7500U. When speed matters, use `fast` or `ultra-fast`.
- The first request after startup or inactivity takes longer: ~1.5 s to load the model and ~1 s to start the browser.
- Slow SPAs (VK loads its content in ~8 s) may not fit in the `basic` timeout (10 s per page). Use `extract_depth: "advanced"` (30 s) or `timeout`.
- At most 2 concurrent requests go to one host, so crawling a single site runs at roughly 1 page per second (20 pages of docs.python.org took ~20 s). It's even slower with `Crawl-delay` in robots.txt.
- Very large pages sometimes don't fit in the page timeout.

## Memory

- Peak ~1 GB while the browser is open: Chromium ~300–380 MB, patchright node driver ~125 MB, model ~225 MB. Everything is unloaded after `IDLE_TTL` (120 s) of inactivity.
- There is no limit on concurrent requests to the server, only on what they share: downloads (`MAX_FETCHES`), browser renders (`MAX_RENDERS`) and 2 requests per host; everything above that waits. Many parallel heavy requests (crawl, `advanced`) on a low-RAM machine can run out of memory. This has not been load-tested.

## PDF

- Text layer only. Scans can't be read without OCR: `PDF has no text layer`.
- At most 100 pages are read; the rest are skipped.
- A file larger than `MAX_PDF_BYTES` (10 MB) is truncated, a truncated PDF can't be read, and the page counts as failed. If you need large PDFs, raise the limit.
- Tables and multi-column layouts are converted to text as is, without markup. Columns may get merged line by line.
- PDFs are parsed strictly one at a time (pdfium isn't thread-safe), so a batch of large PDFs is processed sequentially.
- Links inside PDFs are not extracted; crawl doesn't go past a PDF.

## Crawling (crawl, map)

- sitemap: at most 10 files and 10,000 URLs per site. News and image sitemaps, `lastmod` and `llms-full.txt` are not used.
- robots.txt is parsed only with the rules for `*`, without per-user-agent rules. `Crawl-delay` is capped at 2 s.
- If a site answers every path with a placeholder page and status 200, it is filtered out for robots.txt, llms.txt and `.md` versions (HTML instead of text). But a regular page with such a "soft 404" will show up in the results as a normal page.
- In map, last-level URLs are returned without being fetched, so they may include broken links.

## Dedup and filters

- `dedup` can drop pages that are legitimately different but very similar, for example docs for two versions of a library. If you need everything, pass `dedup=false`.
- `max_per_domain` is applied before pages are fetched. If there are few matching domains, you get fewer than `max_results` results.

## Tavily compatibility

- Accepted but have no effect: `exact_match`, `filter_by_language`, `topic: "finance"` (behaves like `general`). `auto_parameters` only echoes back the given `topic` and `search_depth`.
- `include_image_descriptions` returns the image title from search, not a generated description.
- `follow_up_questions` is always `null`.
- `/usage` counts "credits" in process memory: the counter has no effect on anything and resets on restart.
- Caches live in process memory only: loaded pages and search engine results (`CACHE_TTL`, 15 min), robots.txt, sitemap and llms.txt (1 h), the list of JS domains (1 h). They are lost on restart, and the HTTP API and the MCP server run as separate processes with separate caches. A page changed within `CACHE_TTL` is returned in its old version.

## Security

Remaining SSRF risks in the browser (DNS rebinding inside Chromium, WebSockets) and recommendations for an internet-facing server are in [security.md](security.md).
