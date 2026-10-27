import asyncio
import ctypes
import ctypes.util
import logging
import math
import sys
import time
import uuid
from collections import Counter, OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlsplit

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from agentsearcher import config, stats
from agentsearcher.chunk import SENTENCE_END, Chunk, chunk_markdown
from agentsearcher.extract import Page, default_favicon, extract_pdf, to_text
from agentsearcher.extract import extract as extract_page
from agentsearcher.fetch import (
    FetchError,
    fetch_static,
    is_block_page,
    js_domains,
    needs_browser,
    renderer,
    url_error,
)
from agentsearcher.rank import bm25, dedup, rank_chunks, reranker
from agentsearcher.schemas import ExtractRequest, SearchRequest
from agentsearcher.search import image_search, pick_region, web_search
from agentsearcher.site import markdown_page

log = logging.getLogger(__name__)

SEPARATOR = " [...] "
TIMELIMITS = {"day": "d", "week": "w", "month": "m", "year": "y"}
TIMELIMIT_DAYS = {"d": 1, "w": 7, "m": 31, "y": 365}
GONE = (404, 410)
GRACE = 1.0
DOMAIN_BOOST = 0.15


@dataclass(frozen=True)
class Depth:
    fetch: bool
    browser: bool
    model: bool
    recall: bool
    budget: float
    extra_candidates: int


DEPTHS = {
    "ultra-fast": Depth(False, False, False, False, 3.0, 0),
    "fast": Depth(True, False, False, False, 4.0, 0),
    "basic": Depth(True, True, True, False, 8.0, 0),
    "advanced": Depth(True, True, True, True, 15.0, 2),
}

_janitor: asyncio.Task | None = None
_cpu_jobs = asyncio.Semaphore(config.MAX_CPU_JOBS)
_page_budget: ContextVar[asyncio.Timeout | None] = ContextVar("page_budget", default=None)
_libc = ctypes.CDLL(ctypes.util.find_library("c")) if sys.platform == "linux" else None


def trim_heap():
    """Hand freed heap back to the OS: glibc otherwise keeps ~300 MB after parsing pages."""
    if trim := getattr(_libc, "malloc_trim", None):
        trim(0)


async def _janitor_loop():
    while True:
        await asyncio.sleep(30)
        await renderer.close_if_idle()
        await asyncio.to_thread(reranker.unload_if_idle)
        page_cache.prune()
        await asyncio.to_thread(trim_heap)


def ensure_janitor():
    global _janitor
    loop = asyncio.get_running_loop()
    if _janitor is None or _janitor.done() or _janitor.get_loop() is not loop:
        _janitor = loop.create_task(_janitor_loop())


async def shutdown():
    if _janitor:
        _janitor.cancel()
    await renderer.close()


async def cpu_bound(func, *args, **kwargs):
    """Run parsing or ranking in a thread, at most MAX_CPU_JOBS at a time across all requests:
    a few big pages (Wikipedia) would otherwise take every core and a lot of memory at once.

    Waiting for a slot doesn't count against the page's load timeout. A thread can't be
    stopped, so a cancelled caller leaves it running and its slot taken until it ends.
    """
    budget = _page_budget.get()
    deadline = budget.when() if budget and not budget.expired() else None
    if budget and deadline is not None:
        budget.reschedule(None)
    began = time.monotonic()
    try:
        await _cpu_jobs.acquire()
    finally:
        if budget and deadline is not None:
            budget.reschedule(deadline + time.monotonic() - began)
    job = asyncio.ensure_future(asyncio.to_thread(func, *args, **kwargs))
    job.add_done_callback(_release_cpu)
    return await asyncio.shield(job)


def _release_cpu(job: asyncio.Future):
    _cpu_jobs.release()
    if not job.cancelled():
        job.exception()  # retrieved, so an abandoned job's error isn't logged as unhandled


def _usable(fetched, page: Page) -> Page:
    if fetched.challenge or is_block_page(page.title, page.markdown):
        raise FetchError("blocked by anti-bot challenge")
    # anti-bot systems sometimes keep an error status on a page that rendered real content
    real_content = page.rendered and len(page.markdown) >= 1000
    if fetched.status in GONE or (fetched.status >= 400 and not real_content):
        raise FetchError(f"HTTP {fetched.status}")
    if not page.markdown.strip():
        raise FetchError("no content extracted")
    return page


async def _render(url: str, timeout: float, recall: bool) -> Page:
    fetched = await renderer.render(url, timeout)
    stats.pages["rendered"] += 1
    page = await cpu_bound(extract_page, fetched.html, fetched.url, recall=recall)
    page.rendered = True
    return _usable(fetched, page)


async def load_page(
    url: str, snippet: str | None, *, browser: bool, strict: bool, recall: bool, timeout: float
) -> Page:
    """Fetch statically; switch to the browser only when the static result looks incomplete."""
    started = time.monotonic()
    parts = urlsplit(url)
    host = parts.hostname or ""
    # headless Chromium doesn't display PDFs
    if browser and js_domains.wants(host) and not parts.path.lower().endswith(".pdf"):
        log.info("browser %s (known js domain)", url)
        return await _render(url, timeout, recall)
    fetched = await fetch_static(url, min(timeout, 10.0))
    if fetched.status in GONE or (fetched.status >= 400 and not browser):
        raise FetchError(f"HTTP {fetched.status}")
    if fetched.pdf is not None:
        page = await cpu_bound(extract_pdf, fetched.pdf, fetched.url)
        if not page.markdown:
            raise FetchError("PDF has no text layer")
        log.info("pdf     %s (%d chars)", url, len(page.markdown))
        return page
    page = await cpu_bound(extract_page, fetched.html, fetched.url, recall=recall)
    if fetched.status >= 400 and not fetched.challenge:
        reason = f"HTTP {fetched.status}"
    else:
        reason = needs_browser(fetched, page.markdown, snippet, strict) if browser else None
    if reason is None:
        log.info("static  %s (%d chars)", url, len(page.markdown))
        return _usable(fetched, page)
    log.info("browser %s (%s)", url, reason)
    remaining = max(timeout - (time.monotonic() - started), 3.0)
    try:
        rendered = await _render(url, remaining, recall)
    except FetchError:
        if fetched.challenge or fetched.status >= 400 or not page.markdown.strip():
            raise
        return page
    blocked = fetched.challenge or fetched.status >= 400
    # remember the domain only when the browser really helped, so one sparse index
    # page doesn't send the rest of the site through the browser
    if blocked or len(rendered.markdown) > 2 * len(page.markdown) + 500:
        js_domains.add(host)
    if blocked or len(rendered.markdown) >= len(page.markdown):
        return rendered
    return page


class PageCache:
    """Loaded pages for CACHE_TTL seconds, least recently used dropped above CACHE_MB.

    Keyed by (url, recall). A page loaded without the browser is not reused when the
    browser is allowed: the static version may be incomplete.
    """

    def __init__(self):
        self._items: OrderedDict[tuple[str, bool], tuple[float, bool, Page, int]] = OrderedDict()
        self._size = 0

    def get(self, url: str, recall: bool, browser: bool) -> Page | None:
        key = (url, recall)
        item = self._items.get(key)
        if item is None or item[0] < time.monotonic() or (browser and not item[1]):
            return None
        self._items.move_to_end(key)
        return item[2]

    def put(self, url: str, recall: bool, browser: bool, page: Page):
        if config.CACHE_TTL <= 0:
            return
        self._drop((url, recall))
        size = len(page.markdown) + sum(len(u) + len(t) for u, t in page.links)
        self._items[(url, recall)] = (time.monotonic() + config.CACHE_TTL, browser, page, size)
        self._size += size
        while self._size > config.CACHE_MB * 2**20 and self._items:
            self._drop(next(iter(self._items)))

    def prune(self):
        now = time.monotonic()
        for key in [k for k, item in self._items.items() if item[0] < now]:
            self._drop(key)

    def _drop(self, key: tuple[str, bool]):
        if item := self._items.pop(key, None):
            self._size -= item[3]


page_cache = PageCache()


async def _markdown_or_page(url: str, snippet: str | None, markdown_first: bool, **kwargs) -> Page:
    if markdown_first and (page := await markdown_page(url)):
        return page
    return await load_page(url, snippet, **kwargs)


async def load_or_error(
    url: str,
    snippet: str | None,
    *,
    browser: bool,
    strict: bool,
    recall: bool,
    timeout: float,
    markdown_first: bool = False,
) -> Page | str:
    """load_page() bounded by `timeout`; returns the error text instead of raising.

    `markdown_first` tries the site's clean .md version (llms.txt convention) first.
    Successful loads are reused from page_cache.
    """
    if error := url_error(url):
        log.info("failed  %s (%s)", url, error)
        stats.pages["failed"] += 1
        return error
    if page := page_cache.get(url, recall, browser):
        log.info("cached  %s", url)
        stats.pages["cached"] += 1
        return page
    try:
        async with asyncio.timeout(timeout) as budget:
            token = _page_budget.set(budget)
            try:
                page = await _markdown_or_page(
                    url,
                    snippet,
                    markdown_first,
                    browser=browser,
                    strict=strict,
                    recall=recall,
                    timeout=timeout,
                )
            finally:
                _page_budget.reset(token)
    except TimeoutError:
        error = "timed out"
    except FetchError as e:
        error = str(e)
    else:
        page_cache.put(url, recall, browser, page)
        stats.pages["loaded"] += 1
        return page
    log.info("failed  %s (%s)", url, error)
    stats.pages["failed"] += 1
    return error


def norm_domain(domain: str) -> str:
    host = urlsplit(domain).hostname if "//" in domain else domain.split("/")[0]
    return (host or "").lower().removeprefix("www.")


def matches_domain(url: str, domains: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in domains)


def load_domain_rules(path: str) -> dict[str, list[str]]:
    """`block|lower|raise <domain>` lines from the DOMAIN_RULES file; `#` starts a comment."""
    rules: dict[str, list[str]] = {"block": [], "lower": [], "raise": []}
    if not path:
        return rules
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split("#", 1)[0].split()
            if not parts:
                continue
            if len(parts) != 2 or parts[0] not in rules:
                log.warning("domain rules: skipped line %r", line.strip())
                continue
            rules[parts[0]].append(norm_domain(parts[1]))
    return rules


domain_rules = load_domain_rules(config.DOMAIN_RULES)


def _timelimit(req: SearchRequest) -> str | None:
    if req.time_range:
        return TIMELIMITS.get(req.time_range, req.time_range)
    if req.days:
        return next((k for k, v in TIMELIMIT_DAYS.items() if req.days <= v), "y")
    return None


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _in_window(published: str | None, req: SearchRequest, timelimit: str | None) -> bool:
    start, end = req.start_date, req.end_date
    if req.filter_by_published_date and timelimit and not start:
        start = date.today() - timedelta(days=TIMELIMIT_DAYS[timelimit])
    if not (start or end or req.filter_by_published_date):
        return True
    day = _parse_date(published)
    if day is None:
        return not req.filter_by_published_date
    return (not start or day >= start) and (not end or day <= end)


async def _load_all(hits, depth: Depth, deadline: float, enough: int) -> list[Page | None]:
    """Load pages until the deadline, or until `enough` succeeded plus a short grace period."""

    async def one(hit):
        page = await load_or_error(
            hit.url,
            hit.snippet,
            browser=depth.browser,
            strict=depth.recall,
            recall=depth.recall,
            timeout=max(deadline - time.monotonic(), 1.0),
        )
        return page if isinstance(page, Page) else None

    tasks = [asyncio.create_task(one(h)) for h in hits]
    pending, loaded = set(tasks), 0
    while pending and (timeout := deadline - time.monotonic()) > 0:
        done, pending = await asyncio.wait(
            pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        before, loaded = loaded, loaded + sum(t.result() is not None for t in done)
        if before < enough <= loaded:
            deadline = min(deadline, time.monotonic() + GRACE)
    for task in pending:
        log.info("dropped %s (enough pages or budget spent)", hits[tasks.index(task)].url)
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return [t.result() if not t.cancelled() else None for t in tasks]


def _extractive_answer(query: str, chunks: list[Chunk]) -> str:
    sentences = [s for c in chunks for s in SENTENCE_END.split(c.text) if len(s) > 20]
    if not sentences:
        return ""
    scores = bm25(query, sentences)
    best = sorted(sorted(range(len(sentences)), key=lambda i: -scores[i])[:2])
    return " ".join(sentences[i] for i in best)


async def _llm_answer(query: str, chunks: list[Chunk], detailed: bool) -> str:
    sources = "\n\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(chunks))
    style = "a detailed answer" if detailed else "a short answer (1-3 sentences)"
    payload = {
        "model": config.LLM_MODEL,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": f"Give {style} to the question using only the sources. "
                "Answer in the language of the question.",
            },
            {"role": "user", "content": f"Sources:\n{sources}\n\nQuestion: {query}"},
        ],
    }
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"} if config.LLM_API_KEY else {}
    async with AsyncSession() as session:
        resp = await session.post(
            f"{config.LLM_BASE_URL}/chat/completions", json=payload, headers=headers, timeout=60
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


async def _answer(query: str, chunks: list[Chunk], detailed: bool) -> str:
    if config.LLM_BASE_URL:
        try:
            return await _llm_answer(query, chunks, detailed)
        except (RequestException, KeyError, IndexError) as e:
            log.warning("LLM answer failed, using extractive answer: %s", e)
    return _extractive_answer(query, chunks)


def _fit(results: list[dict], limit: int):
    """Shrink the tail of the result list until the whole response fits `limit` chars."""
    total = sum(len(r["content"]) + len(r["raw_content"] or "") for r in results)
    for r in reversed(results):
        if total <= limit:
            return
        if r["raw_content"]:
            total -= len(r["raw_content"])
            r["raw_content"] = None
    for r in reversed(results):
        if total <= limit:
            return
        cut = r["content"][:300]
        total -= len(r["content"]) - len(cut)
        r["content"] = cut


@stats.tracked("search", lambda req: req.query)
async def search(req: SearchRequest) -> dict:
    started = time.monotonic()
    ensure_janitor()
    depth = DEPTHS[req.search_depth]
    if req.include_raw_content and not depth.fetch:
        depth = DEPTHS["fast"]
    region = pick_region(req.query, req.country, req.language)
    timelimit = _timelimit(req)
    include = [norm_domain(d) for d in req.include_domains]
    exclude = [norm_domain(d) for d in req.exclude_domains]
    restrict = include and req.include_domains_mode != "prefer"

    query = req.query
    if restrict and len(include) == 1:
        query += f" site:{include[0]}"
    query += "".join(f" -site:{d}" for d in exclude[:3])
    pool = math.ceil(req.max_results * 1.5) + 1 if depth.fetch else req.max_results
    images_task = (
        asyncio.create_task(image_search(req.query, region=region, safe=req.safe_search))
        if req.include_images
        else None
    )
    hits = await web_search(
        query,
        max_results=pool * (3 if restrict and len(include) > 1 else 1),
        news=req.topic == "news",
        region=region,
        timelimit=timelimit,
        safe=req.safe_search,
    )
    hits = [
        h
        for h in hits
        if not matches_domain(h.url, exclude + domain_rules["block"])
        and (not restrict or matches_domain(h.url, include))
    ]
    if req.max_per_domain:
        per_domain: Counter[str] = Counter()
        capped = []
        for h in hits:
            domain = norm_domain(h.url)
            per_domain[domain] += 1
            if per_domain[domain] <= req.max_per_domain:
                capped.append(h)
        hits = capped
    hits = hits[:pool]

    pages: list[Page | None] = [None] * len(hits)
    if depth.fetch and req.max_results:
        pages = await _load_all(hits, depth, time.monotonic() + depth.budget, req.max_results)

    chunks: list[Chunk] = []
    for i, (hit, page) in enumerate(zip(hits, pages, strict=True)):
        if page:
            chunks += chunk_markdown(page.markdown, i, config.CHUNK_CHARS)
        elif hit.snippet:
            chunks.append(Chunk(hit.snippet, "", i))
    per_source = req.chunks_per_source + (depth.extra_candidates if depth.model else 0)
    if req.dedup:
        chunks = dedup(chunks)
    ranked = await cpu_bound(rank_chunks, req.query, chunks, per_source, depth.model)
    by_source: dict[int, list[tuple[Chunk, float]]] = {}
    for c, s in ranked:
        by_source.setdefault(c.source, []).append((c, s))

    show_date = req.include_published_date or req.filter_by_published_date or req.topic == "news"
    results = []
    for i, (hit, page) in enumerate(zip(hits, pages, strict=True)):
        top = by_source.get(i, [])[: req.chunks_per_source if page else 1]
        published = hit.published_date or (page.published_date if page else None)
        if not top or not _in_window(published, req, timelimit):
            continue
        score = 0.8 * top[0][1] + 0.2 * (1 - i / len(hits))
        if include and not restrict and matches_domain(hit.url, include):
            score += DOMAIN_BOOST
        if matches_domain(hit.url, domain_rules["raise"]):
            score += DOMAIN_BOOST
        if matches_domain(hit.url, domain_rules["lower"]):
            score -= DOMAIN_BOOST
        score = min(max(score, 0.0), 1.0)
        raw = None
        if req.include_raw_content and page:
            text = to_text(page.markdown) if req.include_raw_content == "text" else page.markdown
            raw = text[: config.MAX_RAW_CHARS]
        result = {
            "url": hit.url,
            "title": hit.title or (page.title if page else ""),
            "content": SEPARATOR.join(c.text for c, _ in top),
            "score": round(score, 4),
            "raw_content": raw,
        }
        if show_date:
            result["published_date"] = published
        if req.include_favicon:
            result["favicon"] = page.favicon if page else default_favicon(hit.url)
        if req.include_images:
            result["images"] = [{"url": u} for u in (page.images[:5] if page else [])]
        result["id"] = uuid.uuid4().hex
        results.append(result)
    results.sort(key=lambda r: -r["score"])
    results = results[: req.max_results]
    _fit(results, config.MAX_RESPONSE_CHARS)

    answer = None
    if req.include_answer and ranked:
        detailed = req.include_answer == "advanced"
        answer = await _answer(req.query, [c for c, _ in ranked[: 8 if detailed else 4]], detailed)

    images = await images_task if images_task else []
    response = {
        "query": req.query,
        "follow_up_questions": None,
        "answer": answer,
        "images": images if req.include_image_descriptions else [i["url"] for i in images],
        "results": results,
        "response_time": round(time.monotonic() - started, 2),
        "request_id": str(uuid.uuid4()),
    }
    if req.auto_parameters:
        response["auto_parameters"] = {"topic": req.topic, "search_depth": req.search_depth}
    if req.include_usage:
        response["usage"] = {"credits": 2 if req.search_depth == "advanced" else 1}
    await asyncio.to_thread(trim_heap)
    return response


@stats.tracked("extract", lambda req: " ".join(req.urls))
async def extract(req: ExtractRequest) -> dict:
    started = time.monotonic()
    ensure_janitor()
    advanced = req.extract_depth == "advanced"
    timeout = req.timeout or (30.0 if advanced else 10.0)

    outcomes = await asyncio.gather(
        *(
            load_or_error(
                u,
                None,
                browser=True,
                strict=advanced,
                recall=advanced,
                timeout=timeout,
                markdown_first=True,
            )
            for u in req.urls
        )
    )
    by_source: dict[int, list[Chunk]] = {}
    if req.query:
        chunks = [
            c
            for i, page in enumerate(outcomes)
            if isinstance(page, Page)
            for c in chunk_markdown(page.markdown, i, config.CHUNK_CHARS)
        ]
        ranked = await cpu_bound(rank_chunks, req.query, chunks, req.chunks_per_source + 2, True)
        for c, _ in ranked:
            by_source.setdefault(c.source, []).append(c)

    results, failed = [], []
    for i, (url, page) in enumerate(zip(req.urls, outcomes, strict=True)):
        if not isinstance(page, Page):
            failed.append({"url": url, "error": page})
            continue
        if req.query:
            texts = [c.text for c in by_source.get(i, [])[: req.chunks_per_source]]
            raw = SEPARATOR.join(to_text(t) if req.format == "text" else t for t in texts)
        else:
            raw = to_text(page.markdown) if req.format == "text" else page.markdown
        result = {"url": url, "raw_content": raw[: config.MAX_RAW_CHARS]}
        if req.include_images:
            result["images"] = page.images
        if req.include_favicon:
            result["favicon"] = page.favicon
        results.append(result)

    response = {
        "results": results,
        "failed_results": failed,
        "response_time": round(time.monotonic() - started, 2),
        "request_id": str(uuid.uuid4()),
    }
    if req.include_usage:
        response["usage"] = {"credits": math.ceil(len(results) / 5) * (2 if advanced else 1)}
    await asyncio.to_thread(trim_heap)
    return response
