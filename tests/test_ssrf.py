import time

import pytest

from agentsearcher import config, crawl, fetch, pipeline
from agentsearcher.fetch import BLOCKED_ADDRESS, FetchError, check_url, js_domains
from agentsearcher.schemas import ExtractRequest, MapRequest


@pytest.fixture
def guarded(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_PRIVATE_URLS", False)


@pytest.fixture
def loopback_is_public(guarded, monkeypatch):
    """Let the guard reach the fixture site on 127.0.0.1 while blocking every other private net."""
    real = fetch._public
    monkeypatch.setattr(fetch, "_public", lambda ip: ip.is_loopback or real(ip))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8484/health",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",
        "file:///etc/passwd",
    ],
)
async def test_check_url_blocks_private_and_local(guarded, url):
    with pytest.raises(FetchError, match="blocked"):
        await check_url(url)


async def test_check_url_allows_public_ip(guarded):
    await check_url("http://8.8.8.8/")


async def test_endpoints_refuse_private_urls(guarded, site):
    response = await pipeline.extract(ExtractRequest(urls=[f"{site}/docs/a"]))
    assert response["failed_results"] == [{"url": f"{site}/docs/a", "error": BLOCKED_ADDRESS}]
    response = await crawl.map_site(MapRequest(url=f"{site}/docs/", max_depth=2))
    assert response["results"] == [f"{site}/docs/"]  # the root itself, nothing fetched


async def test_redirect_to_private_address_is_not_followed(loopback_is_public, site):
    with pytest.raises(FetchError, match=BLOCKED_ADDRESS):
        await fetch.fetch_static(f"{site}/to-private", 5.0)


async def test_browser_requests_to_private_addresses_are_aborted(loopback_is_public, site):
    js_domains._until.clear()
    started = time.monotonic()
    page = await pipeline.load_page(
        f"{site}/fetch-private", None, browser=True, strict=False, recall=False, timeout=20.0
    )
    assert page.rendered and "request failed" in page.markdown
    assert time.monotonic() - started < 10  # aborted, not waiting on an unroutable address
