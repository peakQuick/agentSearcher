import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlsplit

from curl_cffi.requests import AsyncSession
from ddgs.engines import ENGINES
from ddgs.engines.yandex import Yandex
from ddgs.exceptions import DDGSException

from agentsearcher import config
from agentsearcher.fetch import url_error

log = logging.getLogger(__name__)

COUNTRY_REGIONS = {
    "united states": "us-en",
    "united kingdom": "uk-en",
    "canada": "ca-en",
    "australia": "au-en",
    "india": "in-en",
    "russia": "ru-ru",
    "ukraine": "ua-uk",
    "kazakhstan": "kz-ru",
    "belarus": "by-ru",
    "germany": "de-de",
    "france": "fr-fr",
    "spain": "es-es",
    "italy": "it-it",
    "netherlands": "nl-nl",
    "poland": "pl-pl",
    "brazil": "br-pt",
    "mexico": "mx-es",
    "japan": "jp-jp",
    "china": "cn-zh",
    "south korea": "kr-kr",
    "turkey": "tr-tr",
}
LANGUAGE_REGIONS = {
    "en": "us-en",
    "english": "us-en",
    "ru": "ru-ru",
    "russian": "ru-ru",
    "uk": "ua-uk",
    "ukrainian": "ua-uk",
    "de": "de-de",
    "german": "de-de",
    "fr": "fr-fr",
    "french": "fr-fr",
    "es": "es-es",
    "spanish": "es-es",
    "it": "it-it",
    "italian": "it-it",
    "pt": "br-pt",
    "portuguese": "br-pt",
    "ja": "jp-jp",
    "japanese": "jp-jp",
    "zh": "cn-zh",
    "chinese": "cn-zh",
}
CYRILLIC = re.compile("[а-яё]", re.IGNORECASE)
SEARXNG_RANGES = {"d": "day", "w": "week", "m": "month", "y": "year"}
ENGINE_TIMEOUT = 5
RRF_K = 60
# disabled upstream in ddgs, but works and is the best engine for Russian queries
EXTRA_ENGINES = {"text": {"yandex": Yandex}}


@dataclass
class Hit:
    url: str
    title: str
    snippet: str
    published_date: str | None = None


def pick_region(query: str, country: str | None, language: str | None) -> str:
    if country and country.lower() in COUNTRY_REGIONS:
        return COUNTRY_REGIONS[country.lower()]
    if language and language.lower() in LANGUAGE_REGIONS:
        return LANGUAGE_REGIONS[language.lower()]
    return "ru-ru" if CYRILLIC.search(query) else "us-en"


def _engine_call(engine_cls, query: str, **kwargs) -> list[dict]:
    engine = engine_cls(proxy=config.PROXY_URL, timeout=ENGINE_TIMEOUT)
    return [r.__dict__ for r in engine.search(query, page=1, **kwargs) or []]


def url_key(url: str) -> str:
    parts = urlsplit(url)
    return (parts.netloc.lower().removeprefix("www.") + parts.path.rstrip("/")) + parts.query


def _engines(category: str) -> dict[str, type]:
    return {**ENGINES[category], **EXTRA_ENGINES.get(category, {})}


def fuse(rankings: list[list[dict]]) -> list[dict]:
    """Reciprocal rank fusion: URLs found by several engines go first, so a page pushed up
    by one engine (often SEO spam) loses to pages the engines agree on. Equal scores keep
    the interleaved order; the first engine's row (title, snippet) is kept."""
    rows: dict[str, dict] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    for rank in rankings:
        seen = set()
        for pos, row in enumerate(rank):
            url = row.get("href") or row.get("url") or row.get("image") or ""
            if url_error(url):
                continue
            key = url_key(url)
            if key in seen:
                continue
            seen.add(key)
            rows.setdefault(key, row)
            scores[key] += 1 / (RRF_K + pos + 1)
    return [rows[k] for k in sorted(rows, key=lambda k: -scores[k])]


async def _ddgs_search(
    category: str, query: str, region: str, timelimit: str | None, max_results: int, safe: bool
) -> list[dict]:
    """Query each ddgs engine separately and fuse their rankings.

    ddgs' own multi-engine mode gives up on still-running engines as soon as one fails,
    which drops the results of slower working engines whenever another engine is blocked.
    """
    available = _engines(category)
    backends = [b for b in config.SEARCH_BACKENDS if b in available]
    tasks = {
        asyncio.create_task(
            asyncio.to_thread(
                _engine_call,
                available[backend],
                query,
                region=region,
                timelimit=timelimit,
                safesearch="moderate" if safe else "off",
            )
        ): backend
        for backend in backends
    }
    done, _ = await asyncio.wait(tasks, timeout=ENGINE_TIMEOUT + 1)
    rankings = []
    for task, backend in tasks.items():
        if task not in done:
            log.debug("engine %s timed out", backend)
        elif task.exception():
            log.debug("engine %s failed: %s", backend, task.exception())
        else:
            rankings.append(task.result())
    log.info("engines answered: %d/%d", len(rankings), len(backends))
    merged = fuse(rankings)
    if not merged:
        raise DDGSException("no engine returned results")
    return merged[:max_results]


async def _searxng_search(
    category: str, query: str, region: str, timelimit: str | None, safe: bool
) -> list[dict]:
    params = {
        "q": query,
        "format": "json",
        "categories": category,
        "language": region.split("-")[1],
        "safesearch": 1 if safe else 0,
    }
    if timelimit:
        params["time_range"] = SEARXNG_RANGES[timelimit]
    async with AsyncSession() as session:
        resp = await session.get(f"{config.SEARXNG_URL}/search", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("results", [])


_hits_cache: dict[tuple, tuple[float, list[Hit]]] = {}


async def web_search(
    query: str,
    *,
    max_results: int,
    news: bool,
    region: str,
    timelimit: str | None,
    safe: bool,
) -> list[Hit]:
    """_web_search() reused for CACHE_TTL; empty results (engines blocked or down) aren't kept."""
    key = (query, max_results, news, region, timelimit, safe)
    now = time.monotonic()
    if (entry := _hits_cache.get(key)) and entry[0] > now:
        log.info("cached search %r", query)
        return list(entry[1])
    hits = await _web_search(
        query, max_results=max_results, news=news, region=region, timelimit=timelimit, safe=safe
    )
    if hits and config.CACHE_TTL > 0:
        for k in [k for k, (expires, _) in _hits_cache.items() if expires < now]:
            del _hits_cache[k]
        _hits_cache[key] = (now + config.CACHE_TTL, hits)
    return list(hits)


async def _web_search(
    query: str,
    *,
    max_results: int,
    news: bool,
    region: str,
    timelimit: str | None,
    safe: bool,
) -> list[Hit]:
    category = "news" if news else "text"
    try:
        rows = await _ddgs_search(category, query, region, timelimit, max_results, safe)
        if news:
            return [Hit(r["url"], r["title"], r.get("body", ""), r.get("date")) for r in rows]
        return [Hit(r["href"], r["title"], r.get("body", "")) for r in rows]
    except DDGSException as e:
        if not config.SEARXNG_URL:
            log.warning("ddgs failed: %s", e)
            return []
        log.warning("ddgs failed (%s), falling back to SearXNG", e)
    rows = await _searxng_search("news" if news else "general", query, region, timelimit, safe)
    return [
        Hit(r["url"], r.get("title", ""), r.get("content", ""), r.get("publishedDate"))
        for r in rows[:max_results]
        if not url_error(r["url"])
    ]


async def image_search(query: str, *, region: str, safe: bool, limit: int = 5) -> list[dict]:
    try:
        rows = await _ddgs_search("images", query, region, None, limit, safe)
        return [
            {"url": r["image"], "description": r.get("title", "")} for r in rows if r.get("image")
        ]
    except DDGSException as e:
        log.warning("image search failed: %s", e)
        return []
