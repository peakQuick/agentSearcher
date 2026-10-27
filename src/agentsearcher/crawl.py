import asyncio
import logging
import math
import re
import time
import uuid
from urllib.parse import urlsplit

from agentsearcher import config, stats
from agentsearcher.chunk import chunk_markdown
from agentsearcher.extract import Page, to_text
from agentsearcher.fetch import url_error
from agentsearcher.pipeline import (
    SEPARATOR,
    cpu_bound,
    ensure_janitor,
    load_or_error,
    matches_domain,
    norm_domain,
    trim_heap,
)
from agentsearcher.rank import bm25, dedup, rank_chunks
from agentsearcher.schemas import CrawlRequest, MapRequest
from agentsearcher.search import url_key
from agentsearcher.site import (
    FILE_TIMEOUT,
    SiteInfo,
    origin_of,
    resolve,
    site_info,
    sitemap_urls,
)

log = logging.getLogger(__name__)

BATCH = 8
TRUNCATED_CHARS = 300


def _allowed(url: str, req: MapRequest, root: str, hosts: list[str], info: SiteInfo) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    if not req.allow_external and not matches_domain(url, hosts):
        return False
    if origin_of(url) == origin_of(root) and not info.allowed(url):
        return False
    if req.select_domains and not any(re.search(p, host) for p in req.select_domains):
        return False
    if any(re.search(p, host) for p in req.exclude_domains):
        return False
    if req.select_paths and not any(re.match(p, path) for p in req.select_paths):
        return False
    return not any(re.match(p, path) for p in req.exclude_paths)


def _in_scope(url: str, root: str) -> bool:
    """Sitemaps cover the whole site; keep only urls under the root's directory."""
    root_path = urlsplit(root).path or "/"
    last = root_path.rsplit("/", 1)[-1]
    if root_path.endswith("/"):
        base = root_path
    elif "." in last:  # a file like /docs/index.html: its directory
        base = root_path.removesuffix(last)
    else:  # /learn means /learn/...
        base = root_path + "/"
    path = urlsplit(url).path or "/"
    return path.startswith(base) or path == root_path


Link = tuple[str, str]


def page_key(url: str) -> str:
    """url_key that treats a page and its llms.txt-style .md version as the same page."""
    parts = urlsplit(url)
    path = parts.path.removesuffix("index.html.md").removesuffix(".md")
    return url_key(parts._replace(path=path).geturl())


def _candidates(links: list[Link], allowed, seen: set[str]) -> dict[str, Link]:
    """New allowed links keyed by page_key, as (url, text used for relevance)."""
    out: dict[str, Link] = {}
    for link, text in links:
        # sitemaps and llms.txt are not validated like page links
        if url_error(link):
            continue
        key = page_key(link)
        if key not in seen and key not in out and allowed(link):
            out[key] = (link, f"{text} {urlsplit(link).path.replace('/', ' ')}")
    return out


def _by_relevance(items: list[Link], instructions: str | None) -> list[Link]:
    if not instructions or not items:
        return items
    scores = bm25(instructions, [text for _, text in items])
    return [items[i] for i in sorted(range(len(items)), key=lambda i: -scores[i])]


def _take(candidates: dict[str, Link], seen: set[str], limit: int | None = None) -> list[Link]:
    keys = [k for k in candidates if k not in seen][:limit]
    seen.update(keys)
    return [candidates[k] for k in keys]


async def walk(
    req: MapRequest, *, fetch_last_level: bool, advanced: bool
) -> tuple[list, list, list]:
    """Breadth-first walk from req.url, seeded with the site's llms.txt and sitemap.

    Returns (discovered urls in discovery order, loaded pages in load order, failed loads
    as {url, error}). Pages are
    fetched for depths 0..max_depth (or 0..max_depth-1 when only links are needed).
    llms.txt and sitemap urls join depth 1 and are not limited by max_breadth.
    """
    deadline = time.monotonic() + req.timeout - 1
    page_timeout = 30.0 if advanced else 10.0
    # docs often move (docs.example.com -> example.com/docs/): scope by where the root lands
    # site files may take at most a third of the budget each; pages need the rest
    root = await resolve(req.url, min(FILE_TIMEOUT, (deadline - time.monotonic()) / 3))
    hosts = list(dict.fromkeys([norm_domain(req.url), norm_domain(root)]))
    try:
        info, sitemap = await asyncio.wait_for(
            asyncio.gather(site_info(root), sitemap_urls(root)),
            min(2 * FILE_TIMEOUT, (deadline - time.monotonic()) / 3),
        )
    except TimeoutError:
        log.info("site files of %s too slow, walking links only", root)
        info, sitemap = SiteInfo(None, False, []), []
    delay = info.crawl_delay()
    batch_size = 1 if delay else BATCH
    seen = {page_key(req.url), page_key(root)}

    def allowed(url: str) -> bool:
        return _allowed(url, req, root, hosts, info)

    llms_seeds = _candidates(info.llms_links, allowed, seen)
    scoped = [(u, "") for u in sitemap if req.select_paths or _in_scope(u, root)]
    sitemap_seeds = _candidates(scoped, allowed, seen)

    if not fetch_last_level and 1 + len({**llms_seeds, **sitemap_seeds}) >= req.limit:
        seeds = _by_relevance(
            _take(llms_seeds, seen) + _take(sitemap_seeds, seen), req.instructions
        )
        log.info("map from llms.txt/sitemap only: %d urls", len(seeds))
        return [root] + [u for u, _ in seeds], [], []

    discovered, pages, failed = [root], [], []
    level: list[Link] = [(root, "")]
    last_fetched = req.max_depth if fetch_last_level else req.max_depth - 1
    for depth in range(last_fetched + 1):
        next_level: list[Link] = []
        for start in range(0, len(level), batch_size):
            used = len(pages) if fetch_last_level else len(discovered) + len(next_level)
            room, remaining = req.limit - used, deadline - time.monotonic()
            if room <= 0 or remaining < 1:
                break
            batch = level[start : start + batch_size]
            if fetch_last_level:
                batch = batch[:room]
            loaded = await asyncio.gather(
                *(
                    load_or_error(
                        url,
                        None,
                        browser=True,
                        strict=advanced,
                        recall=advanced,
                        timeout=min(page_timeout, remaining),
                        markdown_first=True,
                    )
                    for url, _ in batch
                )
            )
            for (url, _), page in zip(batch, loaded, strict=True):
                if not isinstance(page, Page):
                    failed.append({"url": url, "error": page})
                    continue
                seen.add(page_key(page.url))
                pages.append(page)
                if depth < req.max_depth:
                    candidates = _candidates(page.links, allowed, seen)
                    ranked = _by_relevance(list(candidates.values()), req.instructions)
                    picked = {page_key(u): (u, t) for u, t in ranked[: req.max_breadth]}
                    next_level += _take(picked, seen)
            if delay:
                await asyncio.sleep(delay)
        if depth == 0:
            seen.update(llms_seeds)
            root_links = [link for link in next_level if page_key(link[0]) not in llms_seeds]
            next_level = list(llms_seeds.values()) + root_links + _take(sitemap_seeds, seen)
        next_level = _by_relevance(next_level, req.instructions)
        discovered += [u for u, _ in next_level]
        log.info("depth %d: %d pages loaded, %d urls found", depth, len(pages), len(discovered))
        level = next_level
        if not level:
            break
    return discovered, pages, failed


@stats.tracked("map", lambda req: req.url)
async def map_site(req: MapRequest) -> dict:
    started = time.monotonic()
    ensure_janitor()
    discovered, _, failed = await walk(req, fetch_last_level=False, advanced=False)
    results = discovered[: req.limit]
    response = {
        "base_url": req.url,
        "results": results,
        "failed_results": failed,
        "response_time": round(time.monotonic() - started, 2),
        "request_id": str(uuid.uuid4()),
    }
    if req.include_usage:
        response["usage"] = {
            "credits": math.ceil(len(results) / 10) * (2 if req.instructions else 1)
        }
    await asyncio.to_thread(trim_heap)
    return response


@stats.tracked("crawl", lambda req: req.url)
async def crawl(req: CrawlRequest) -> dict:
    started = time.monotonic()
    ensure_janitor()
    advanced = req.extract_depth == "advanced"
    _, pages, failed = await walk(req, fetch_last_level=True, advanced=advanced)

    by_source: dict[int, list[str]] = {}
    if req.instructions:
        chunks = [
            c
            for i, p in enumerate(pages)
            for c in chunk_markdown(p.markdown, i, config.CHUNK_CHARS)
        ]
        if req.dedup:
            chunks = dedup(chunks)
        ranked = await cpu_bound(
            rank_chunks, req.instructions, chunks, req.chunks_per_source + 2, True
        )
        for c, _ in ranked:
            by_source.setdefault(c.source, []).append(c.text)

    results, total = [], 0
    for i, page in enumerate(pages):
        if req.instructions:
            raw = SEPARATOR.join(by_source.get(i, [])[: req.chunks_per_source])
        else:
            raw = page.markdown
        raw = (to_text(raw) if req.format == "text" else raw)[: config.MAX_RAW_CHARS]
        if total + len(raw) > config.MAX_RESPONSE_CHARS:
            raw = raw[:TRUNCATED_CHARS]
        total += len(raw)
        result = {"url": page.url, "raw_content": raw}
        if req.include_images:
            result["images"] = page.images
        if req.include_favicon:
            result["favicon"] = page.favicon
        results.append(result)

    response = {
        "base_url": req.url,
        "results": results,
        "failed_results": failed,
        "response_time": round(time.monotonic() - started, 2),
        "request_id": str(uuid.uuid4()),
    }
    if req.include_usage:
        response["usage"] = {"credits": math.ceil(len(results) / 5) * (2 if advanced else 1)}
    await asyncio.to_thread(trim_heap)
    return response
