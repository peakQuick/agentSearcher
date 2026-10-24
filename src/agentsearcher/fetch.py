import asyncio
import ipaddress
import logging
import re
import socket
import time
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from agentsearcher import config

log = logging.getLogger(__name__)

CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-chl",
    "challenge-platform",
    "captcha-delivery",
    "px-captcha",
    "perimeterx",
    "ddos-guard",
)
# checked against the rendered title + visible text: normal Cloudflare-protected pages
# also embed challenge-platform scripts, so raw HTML markers are useless after rendering
BLOCK_TEXT_MARKERS = (
    "just a moment",
    "checking your browser",
    "performing security verification",
    "verify you are human",
    "prove your humanity",
    "are you a robot",
    "attention required",
    "request blocked",
    "access denied",
)
VISIBLE_HEAD = (
    "document.title + ' ' + (document.body ? document.body.innerText.slice(0, 1500) : '')"
)
EMPTY_ROOT = re.compile(
    r"<div[^>]+id=[\"'](root|app|__next|__nuxt)[\"'][^>]*>\s*</div>", re.IGNORECASE
)
NOSCRIPT_JS = re.compile(r"<noscript[^>]*>[^<]*(enable|need|requires?)[^<]*javascript", re.I)
SCRIPT = re.compile(r"<script[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
WORD = re.compile(r"\w{4,}")
META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([\w-]+)", re.IGNORECASE)
HTML_TYPES = ("text/html", "application/xhtml", "text/plain")
PDF_TYPES = ("application/pdf",)
REDIRECTS = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 10
BLOCKED_ADDRESS = "blocked: private or local address"
BLOCKED_RESOURCES = {"image", "media", "font"}
DOMAIN_TTL = 3600.0
BROWSER_ARGS = [
    "--disable-gpu",
    "--in-process-gpu",
    "--js-flags=--max-old-space-size=256",
    "--renderer-process-limit=2",
    "--disable-site-isolation-trials",
    "--disable-features=IsolateOrigins,site-per-process,Translate,OptimizationHints,MediaRouter",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--no-first-run",
]


class FetchError(Exception):
    pass


@dataclass
class Fetched:
    url: str
    status: int
    html: str
    challenge: bool = False
    pdf: bytes | None = None


def is_challenge(status: int, headers: dict, html: str) -> bool:
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    head = html[:20000].lower()
    return status in (403, 429, 503) and any(m in head for m in CHALLENGE_MARKERS)


def is_block_page(title: str, text: str) -> bool:
    head = f"{title} {text[:1500]}".lower()
    return len(text) < 1000 and any(m in head for m in BLOCK_TEXT_MARKERS)


def snippet_overlap(snippet: str, text: str) -> float | None:
    words = {w.lower() for w in WORD.findall(snippet)}
    if len(words) < 4:
        return None
    lower = text.lower()
    return sum(w in lower for w in words) / len(words)


def needs_browser(f: Fetched, text: str, snippet: str | None, strict: bool) -> str | None:
    if f.challenge:
        return "challenge"
    if len(text) < (600 if strict else 300):
        return "spa: little text"
    if len(text) < 2000 and (EMPTY_ROOT.search(f.html) or NOSCRIPT_JS.search(f.html)):
        return "spa: empty root"
    script_len = sum(len(s) for s in SCRIPT.findall(f.html))
    if script_len > 0.7 * len(f.html) and len(text) < 3000:
        return "spa: script-heavy"
    if snippet:
        overlap = snippet_overlap(snippet, text)
        if overlap is not None and overlap < (0.5 if strict else 0.3):
            return f"snippet mismatch ({overlap:.0%})"
    return None


class DomainMemory:
    def __init__(self):
        self._until: dict[str, float] = {}

    def wants(self, host: str) -> bool:
        return self._until.get(host, 0) > time.monotonic()

    def add(self, host: str):
        self._until[host] = time.monotonic() + DOMAIN_TTL


js_domains = DomainMemory()
_global_sem = asyncio.Semaphore(8)
_host_sems: defaultdict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(2))


def _decode(body: bytes, charset: str | None) -> str:
    if not charset and (m := META_CHARSET.search(body[:4096])):
        charset = m.group(1).decode()
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


@dataclass
class Raw:
    url: str
    status: int
    content_type: str
    body: bytes
    charset: str | None

    def text(self) -> str:
        return _decode(self.body, self.charset)


def _public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def check_url(url: str):
    """Refuse non-http(s) urls and hosts that resolve to private, loopback or link-local
    addresses, so the server can't be used to reach its own network (SSRF)."""
    if config.ALLOW_PRIVATE_URLS:
        return
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(f"blocked: unsupported url {url[:100]}")
    try:
        ips = [ipaddress.ip_address(parts.hostname)]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                parts.hostname, parts.port or 443, type=socket.SOCK_STREAM
            )
        except socket.gaierror:
            return  # curl reports the DNS failure itself
        ips = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not all(_public(ip) for ip in ips):
        raise FetchError(BLOCKED_ADDRESS)


async def _download(url: str, timeout: float, max_bytes: int, types: tuple | None):
    """GET with browser TLS impersonation; body cut at max_bytes (MAX_PDF_BYTES for PDFs),
    rejected early if not `types`. Every redirect hop goes through check_url."""
    host = urlsplit(url).hostname or ""
    deadline = time.monotonic() + timeout
    async with (
        _global_sem,
        _host_sems[host],
        AsyncSession(impersonate="chrome", proxy=config.PROXY_URL) as session,
    ):
        try:
            for _ in range(MAX_REDIRECTS + 1):
                await check_url(url)
                resp = await session.get(
                    url,
                    timeout=max(deadline - time.monotonic(), 0.1),
                    stream=True,
                    allow_redirects=False,
                )
                # the address curl really connected to: catches DNS rebinding after check_url
                checked_ip = resp.primary_ip and not (config.ALLOW_PRIVATE_URLS or config.PROXY_URL)
                if checked_ip and not _public(ipaddress.ip_address(resp.primary_ip)):
                    await resp.aclose()
                    raise FetchError(BLOCKED_ADDRESS)
                location = resp.headers.get("location")
                if resp.status_code not in REDIRECTS or not location:
                    break
                await resp.aclose()
                url = urljoin(url, location)
            else:
                raise FetchError("too many redirects")
            ctype = resp.headers.get("content-type", "text/html").lower()
            if types and not ctype.startswith(types):
                await resp.aclose()
                raise FetchError(f"unsupported content type: {ctype.split(';')[0]}")
            if ctype.startswith(PDF_TYPES):
                max_bytes = config.MAX_PDF_BYTES
            body = bytearray()
            async for part in resp.aiter_content():
                body += part
                if len(body) > max_bytes:
                    break
            await resp.aclose()
        except RequestException as e:
            raise FetchError(str(e).split(". See")[0]) from e
    return resp, bytes(body)


async def fetch_bytes(url: str, timeout: float, max_bytes: int) -> Raw:
    resp, body = await _download(url, timeout, max_bytes, None)
    ctype = resp.headers.get("content-type", "").lower()
    return Raw(str(resp.url), resp.status_code, ctype, body, resp.charset_encoding)


async def fetch_static(url: str, timeout: float) -> Fetched:
    resp, body = await _download(url, timeout, config.MAX_HTML_BYTES, HTML_TYPES + PDF_TYPES)
    if resp.headers.get("content-type", "").lower().startswith(PDF_TYPES):
        return Fetched(str(resp.url), resp.status_code, "", pdf=body)
    html = _decode(body, resp.charset_encoding)
    headers = {k.lower(): v for k, v in resp.headers.items()}
    return Fetched(
        str(resp.url), resp.status_code, html, is_challenge(resp.status_code, headers, html)
    )


def _router():
    """Route handler that drops heavy resources and any browser request to a private address
    (navigations, JS redirects, fetch/XHR, subresources); one check per host per render."""
    hosts: dict[str, bool] = {}

    async def route_request(route):
        request = route.request
        host = urlsplit(request.url).hostname or ""
        if host not in hosts:
            try:
                await check_url(request.url)
                hosts[host] = True
            except FetchError:
                log.info("blocked browser request to %s", request.url[:100])
                hosts[host] = False
        if request.resource_type in BLOCKED_RESOURCES or not hosts[host]:
            await route.abort()
        else:
            await route.continue_()

    return route_request


async def _while_navigating(page, call):
    """Run a page call, retrying while the page is mid-navigation (redirects, challenges)."""
    from patchright.async_api import Error as PlaywrightError

    for _ in range(5):
        try:
            return await call()
        except PlaywrightError as e:
            if "navigat" not in str(e) and "context was destroyed" not in str(e):
                raise
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(300)
    return await call()


async def _content(page) -> str:
    return await _while_navigating(page, page.content)


async def _evaluate(page, script: str):
    return await _while_navigating(page, lambda: page.evaluate(script))


class Renderer:
    """One lazily started patchright Chromium, shut down after IDLE_TTL without use."""

    def __init__(self):
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(2)
        self._active = 0
        self._last_used = 0.0

    async def _ensure(self):
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return self._browser
            from patchright.async_api import async_playwright

            log.info("starting browser")
            self._pw = await async_playwright().start()
            proxy = {"server": config.PROXY_URL} if config.PROXY_URL else None
            self._browser = await self._pw.chromium.launch(
                channel="chromium",
                headless=True,
                proxy=proxy,
                args=BROWSER_ARGS,
            )
            return self._browser

    async def render(self, url: str, timeout: float) -> Fetched:
        async with self._sem:
            self._active += 1
            try:
                browser = await self._ensure()
                context = await browser.new_context(locale="en-US")
                from patchright.async_api import Error as PlaywrightError

                try:
                    return await asyncio.wait_for(self._load(context, url), timeout)
                except TimeoutError as e:
                    raise FetchError("browser render timed out") from e
                except PlaywrightError as e:
                    raise FetchError(str(e).splitlines()[0]) from e
                finally:
                    await context.close()
            finally:
                self._active -= 1
                self._last_used = time.monotonic()

    async def _load(self, context, url: str) -> Fetched:
        await check_url(url)
        page = await context.new_page()
        await page.route("**/*", _router())
        resp = await page.goto(url, wait_until="domcontentloaded")
        status = resp.status if resp else 0
        challenged = False
        for _ in range(20):
            head = (await _evaluate(page, VISIBLE_HEAD)).lower()
            if not any(m in head for m in BLOCK_TEXT_MARKERS):
                break
            challenged = True
            await page.wait_for_timeout(500)
        else:
            return Fetched(page.url, status, await _content(page), challenge=True)
        await self._wait_stable(page)
        await page.mouse.wheel(0, 4000)
        await page.wait_for_timeout(300)
        if challenged:
            status = 200
        return Fetched(page.url, status, await _content(page))

    @staticmethod
    async def _wait_stable(page):
        """Wait until visible text nearly stops growing; 'Loading...' placeholders don't count.

        Live pages (feeds, counters) never become byte-stable, so <5% growth is enough.
        """
        last = -1
        for i in range(20):
            size = await _evaluate(page, "document.body ? document.body.innerText.length : 0")
            settled = last >= 0 and size - last < 0.05 * size
            if settled and (size >= 500 or i >= 10):
                return
            last = size
            await page.wait_for_timeout(300)

    async def close_if_idle(self):
        idle = time.monotonic() - self._last_used > config.IDLE_TTL
        if self._browser and not self._active and idle:
            log.info("stopping idle browser")
            await self.close()

    async def close(self):
        async with self._lock:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
            self._browser = self._pw = None


renderer = Renderer()
