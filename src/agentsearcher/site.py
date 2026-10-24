"""What a site publishes about itself: robots.txt, sitemaps, llms.txt and .md page versions.

Every file is optional. Missing, broken, oversized or slow files just mean "no data",
and the result (including the failure) is cached per origin for an hour.
"""

import asyncio
import logging
import time
import zlib
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import lxml.etree

from agentsearcher import config
from agentsearcher.extract import Page, from_markdown, markdown_links
from agentsearcher.fetch import FetchError, Raw, fetch_bytes

log = logging.getLogger(__name__)

FILE_TIMEOUT = 3.0
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SITEMAPS = 10
MAX_SITEMAP_URLS = 10_000
MAX_CRAWL_DELAY = 2.0
CACHE_TTL = 3600.0
MIN_MARKDOWN_CHARS = 200
MARKDOWN_TYPES = ("text/markdown", "text/x-markdown", "text/plain")
XML_PARSER = lxml.etree.XMLParser(recover=True, resolve_entities=False, no_network=True)


@dataclass
class SiteInfo:
    robots: RobotFileParser | None
    has_llms: bool
    llms_links: list[tuple[str, str]]

    def allowed(self, url: str) -> bool:
        return not (config.RESPECT_ROBOTS and self.robots) or self.robots.can_fetch("*", url)

    def crawl_delay(self) -> float:
        if not (config.RESPECT_ROBOTS and self.robots):
            return 0.0
        return min(float(self.robots.crawl_delay("*") or 0), MAX_CRAWL_DELAY)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


async def _get(url: str, timeout: float = FILE_TIMEOUT) -> Raw | None:
    try:
        raw = await asyncio.wait_for(fetch_bytes(url, timeout, MAX_FILE_BYTES), timeout)
    except (FetchError, TimeoutError):
        return None
    return raw if raw.status == 200 and raw.body else None


async def resolve(url: str, timeout: float = FILE_TIMEOUT) -> str:
    """Final url after redirects; the url itself if the site can't be reached in time."""
    try:
        raw = await asyncio.wait_for(fetch_bytes(url, timeout, 1), timeout)
    except (FetchError, TimeoutError):
        return url
    return raw.url


def _is_html(text: str) -> bool:
    """Soft 404s: many sites answer any path with a 200 HTML page."""
    return text.lstrip()[:1] == "<"


async def _robots(origin: str) -> RobotFileParser | None:
    raw = await _get(f"{origin}/robots.txt")
    if not raw or _is_html(text := raw.text()):
        return None
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    return parser


async def _llms(origin: str) -> str | None:
    raw = await _get(f"{origin}/llms.txt")
    if not raw or _is_html(text := raw.text()):
        return None
    return text


async def _load_info(origin: str) -> SiteInfo:
    robots, llms = await asyncio.gather(_robots(origin), _llms(origin))
    links = markdown_links(llms, f"{origin}/") if llms else []
    log.info(
        "site    %s robots=%s llms.txt=%s (%d links)", origin, bool(robots), bool(llms), len(links)
    )
    return SiteInfo(robots, llms is not None, links)


def parse_sitemap(body: bytes) -> tuple[list[str], list[str]]:
    """(page urls, child sitemap urls) of a sitemap or sitemap index; gzip is detected."""
    if body[:2] == b"\x1f\x8b":
        try:
            body = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, MAX_FILE_BYTES)
        except zlib.error:
            return [], []
    try:
        root = lxml.etree.fromstring(body, XML_PARSER)
    except lxml.etree.XMLSyntaxError:
        return [], []
    if root is None:
        return [], []
    kind = lxml.etree.QName(root).localname
    path = {"urlset": "{*}url/{*}loc", "sitemapindex": "{*}sitemap/{*}loc"}.get(kind)
    if path is None:
        return [], []
    locs = [el.text.strip() for el in root.iterfind(path) if el.text and el.text.strip()]
    return (locs, []) if kind == "urlset" else ([], locs)


async def _load_sitemap(origin: str) -> list[str]:
    info = await site_info(origin)
    listed = info.robots.site_maps() if info.robots else None
    pending = listed or [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]
    urls: list[str] = []
    seen: set[str] = set()
    while pending and len(seen) < MAX_SITEMAPS and len(urls) < MAX_SITEMAP_URLS:
        batch = [u for u in dict.fromkeys(pending) if u not in seen][: MAX_SITEMAPS - len(seen)]
        seen.update(batch)
        pending = []
        for raw in await asyncio.gather(*(_get(u) for u in batch)):
            if raw:
                locs, children = parse_sitemap(raw.body)
                urls += locs
                pending += children
    log.info("sitemap %s: %d urls from %d files", origin, len(urls), len(seen))
    return urls[:MAX_SITEMAP_URLS]


_cache: dict[tuple[str, str], tuple[float, asyncio.Task]] = {}


async def _cached(kind: str, origin: str, load):
    """One shared load per (kind, origin) for CACHE_TTL; concurrent callers await the same task."""
    loop = asyncio.get_running_loop()
    entry = _cache.get((kind, origin))
    if entry is None or entry[0] < time.monotonic() or entry[1].get_loop() is not loop:
        entry = (time.monotonic() + CACHE_TTL, loop.create_task(load(origin)))
        _cache[(kind, origin)] = entry
    return await asyncio.shield(entry[1])


async def site_info(url: str) -> SiteInfo:
    return await _cached("info", origin_of(url), _load_info)


async def sitemap_urls(url: str) -> list[str]:
    return await _cached("sitemap", origin_of(url), _load_sitemap)


def _markdown_url(url: str) -> str:
    """llms.txt convention: page.md, and index.html.md for directory urls."""
    parts = urlsplit(url)
    path = parts.path or "/"
    if not path.endswith(".md"):
        path += "index.html.md" if path.endswith("/") else ".md"
    return parts._replace(path=path, fragment="").geturl()


async def markdown_page(url: str) -> Page | None:
    """The clean .md version of a page, when the site publishes one (llms.txt convention)."""
    path = urlsplit(url).path
    if path.lower().endswith(".pdf"):
        return None
    if not path.endswith(".md") and not (await site_info(url)).has_llms:
        return None
    md_url = _markdown_url(url)
    raw = await _get(md_url)
    if not raw or not raw.content_type.startswith(MARKDOWN_TYPES):
        return None
    text = raw.text()
    if len(text) < MIN_MARKDOWN_CHARS or _is_html(text):
        return None
    log.info("markdown %s (%d chars)", md_url, len(text))
    return from_markdown(text, url)
