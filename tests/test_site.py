import gzip
import time

import pytest
from conftest import RichHandler

from agentsearcher import config, crawl, pipeline, site
from agentsearcher.schemas import ExtractRequest, MapRequest
from agentsearcher.site import _markdown_url, parse_sitemap

URLSET = (
    b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<url><loc>https://a.com/x</loc></url><url><loc> https://a.com/y </loc></url></urlset>"
)


def paths(base, urls):
    return [u.removeprefix(base) for u in urls]


@pytest.fixture
def page_loads(monkeypatch):
    """Records every page load the crawler makes."""
    loads = []
    real = crawl.load_or_error

    async def counting(url, *args, **kwargs):
        loads.append(url)
        return await real(url, *args, **kwargs)

    monkeypatch.setattr(crawl, "load_or_error", counting)
    return loads


def test_parse_sitemap_variants():
    assert parse_sitemap(URLSET) == (["https://a.com/x", "https://a.com/y"], [])
    assert parse_sitemap(gzip.compress(URLSET))[0] == ["https://a.com/x", "https://a.com/y"]
    index = (
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://a.com/s1.xml</loc></sitemap></sitemapindex>"
    )
    assert parse_sitemap(index) == ([], ["https://a.com/s1.xml"])
    assert parse_sitemap(b"<<<not xml") == ([], [])
    assert parse_sitemap(b"<rss><channel/></rss>") == ([], [])
    assert parse_sitemap(b"\x1f\x8bbroken gzip") == ([], [])


def test_markdown_url():
    assert _markdown_url("https://a.com/docs/") == "https://a.com/docs/index.html.md"
    assert _markdown_url("https://a.com/docs/intro") == "https://a.com/docs/intro.md"
    assert _markdown_url("https://a.com/intro.md") == "https://a.com/intro.md"
    assert _markdown_url("https://a.com/p?lang=en#top") == "https://a.com/p.md?lang=en"


async def test_map_from_llms_and_sitemap_without_loading_pages(rich_site, page_loads):
    response = await crawl.map_site(MapRequest(url=f"{rich_site}/docs/", limit=4))
    # llms.txt first, then the sitemap; orphan /docs/caching is found although nothing links
    # to it, and llms.txt's /docs/a.md collapses into /docs/a
    assert paths(rich_site, response["results"]) == [
        "/docs/",
        "/docs/a",
        "/docs/b",
        "/docs/caching",
    ]
    assert page_loads == []


async def test_map_follows_root_redirect(rich_site):
    response = await crawl.map_site(MapRequest(url=f"{rich_site}/moved/", limit=4))
    assert paths(rich_site, response["results"]) == [
        "/docs/",
        "/docs/a",
        "/docs/b",
        "/docs/caching",
    ]


async def test_map_merges_links_robots_and_scope(rich_site):
    response = await crawl.map_site(MapRequest(url=f"{rich_site}/docs/", allow_external=False))
    found = paths(rich_site, response["results"])
    assert found[:3] == ["/docs/", "/docs/a", "/docs/b"]
    assert "/docs/caching" in found and "/blog/x" in found  # sitemap + link from the root page
    assert "/docs/private/p" not in found  # robots.txt Disallow
    assert "/other/z" not in found  # sitemap url outside the /docs/ root


async def test_select_paths_override_sitemap_scope(rich_site):
    req = MapRequest(url=f"{rich_site}/docs/", select_paths=["/other/.*"], limit=2)
    assert paths(rich_site, (await crawl.map_site(req))["results"]) == ["/docs/", "/other/z"]


async def test_robots_can_be_ignored(rich_site, monkeypatch):
    monkeypatch.setattr(config, "RESPECT_ROBOTS", False)
    response = await crawl.map_site(MapRequest(url=f"{rich_site}/docs/", limit=10))
    assert "/docs/private/p" in paths(rich_site, response["results"])


async def test_instructions_rank_llms_and_sitemap_urls(rich_site):
    req = MapRequest(url=f"{rich_site}/docs/", limit=2, instructions="caching")
    assert paths(rich_site, (await crawl.map_site(req))["results"]) == ["/docs/", "/docs/caching"]


async def test_markdown_version_used_and_its_links_followed(rich_site):
    req = MapRequest(url=f"{rich_site}/docs/", max_depth=2, allow_external=False)
    assert "/docs/a/deep" in paths(rich_site, (await crawl.map_site(req))["results"])


async def test_extract_prefers_markdown_and_ignores_robots(rich_site):
    response = await pipeline.extract(
        ExtractRequest(
            urls=[f"{rich_site}/docs/a", f"{rich_site}/docs/b", f"{rich_site}/docs/private/p"]
        )
    )
    by_path = {r["url"].removeprefix(rich_site): r["raw_content"] for r in response["results"]}
    assert by_path["/docs/a"].startswith("# State hooks (markdown version)")
    assert by_path["/docs/b"].startswith("# Styling components")  # no .md: regular HTML path
    assert "Private internal page" in by_path["/docs/private/p"]


async def test_extract_works_without_llms_txt(site):
    response = await pipeline.extract(ExtractRequest(urls=[f"{site}/docs/a"]))
    assert response["results"][0]["raw_content"].startswith("# State hooks guide")


async def test_crawl_delay_respected(rich_site):
    started = time.monotonic()
    req = MapRequest(url=f"{rich_site}/docs/", max_depth=2, limit=50, allow_external=False)
    await crawl.map_site(req)
    assert time.monotonic() - started >= 2  # root + at least one more sequential batch


async def test_site_files_fetched_once_per_origin(rich_site):
    site._cache.clear()
    before = RichHandler.hits.count("/robots.txt")
    for _ in range(3):
        await crawl.map_site(MapRequest(url=f"{rich_site}/docs/", limit=3))
    assert RichHandler.hits.count("/robots.txt") == before + 1


async def test_broken_site_files_change_nothing(site, broken_site):
    req = {"max_depth": 2, "allow_external": False}
    plain = await crawl.map_site(MapRequest(url=f"{site}/docs/", **req))
    broken = await crawl.map_site(MapRequest(url=f"{broken_site}/docs/", **req))
    assert paths(broken_site, broken["results"]) == paths(site, plain["results"])
