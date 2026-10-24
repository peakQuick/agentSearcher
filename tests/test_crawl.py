import pytest
from pydantic import ValidationError

from agentsearcher import crawl
from agentsearcher.fetch import js_domains
from agentsearcher.schemas import CrawlRequest, MapRequest


def paths(site, urls):
    return [u.removeprefix(site) for u in urls]


@pytest.fixture(autouse=True)
def _fresh_domains():
    js_domains._until.clear()


async def test_map_depth_1_lists_links_without_fetching_them(site):
    response = await crawl.map_site(MapRequest(url=f"{site}/docs/", allow_external=False))
    # fragment link collapses into the root, duplicate links are dropped
    assert paths(site, response["results"]) == [
        "/docs/",
        "/docs/a",
        "/docs/b",
        "/blog/x",
        "/files/manual.pdf",
    ]
    assert set(response) >= {"base_url", "results", "response_time", "request_id"}


async def test_map_depth_2_and_external_links(site):
    response = await crawl.map_site(MapRequest(url=f"{site}/docs/", max_depth=2))
    found = paths(site, response["results"])
    assert "https://external.invalid/page" in found
    assert {"/docs/a/deep", "/docs/b/deep"} <= set(found)


async def test_map_limit_and_breadth(site):
    req = MapRequest(url=f"{site}/docs/", max_depth=2, max_breadth=1, allow_external=False)
    assert paths(site, (await crawl.map_site(req))["results"]) == [
        "/docs/",
        "/docs/a",
        "/docs/a/deep",
    ]
    req = MapRequest(url=f"{site}/docs/", max_depth=2, limit=3)
    assert len((await crawl.map_site(req))["results"]) == 3


async def test_path_filters(site):
    req = MapRequest(
        url=f"{site}/docs/", max_depth=2, select_paths=["/docs/.*"], exclude_paths=["/docs/b"]
    )
    assert paths(site, (await crawl.map_site(req))["results"]) == [
        "/docs/",
        "/docs/a",
        "/docs/a/deep",
    ]


async def test_instructions_pick_relevant_branch_first(site):
    req = MapRequest(
        url=f"{site}/docs/", max_breadth=1, allow_external=False, instructions="styling css"
    )
    assert paths(site, (await crawl.map_site(req))["results"]) == ["/docs/", "/docs/b"]


async def test_crawl_returns_page_contents(site):
    req = CrawlRequest(url=f"{site}/docs/", allow_external=False, include_favicon=True)
    response = await crawl.crawl(req)
    by_path = {r["url"].removeprefix(site): r for r in response["results"]}
    assert set(by_path) == {"/docs/", "/docs/a", "/docs/b", "/blog/x", "/files/manual.pdf"}
    assert "# State hooks guide" in by_path["/docs/a"]["raw_content"]
    assert by_path["/docs/a"]["favicon"].endswith("/favicon.ico")


async def test_crawl_with_instructions_returns_relevant_chunks(site):
    req = CrawlRequest(
        url=f"{site}/docs/",
        max_depth=2,
        select_paths=["/docs/a.*"],
        instructions="useState deep dive",
        chunks_per_source=1,
    )
    response = await crawl.crawl(req)
    by_path = {r["url"].removeprefix(site): r["raw_content"] for r in response["results"]}
    assert "useState" in by_path["/docs/a/deep"]
    assert all(len(raw) <= 700 for raw in by_path.values())


async def test_crawl_limit_counts_pages(site):
    req = CrawlRequest(url=f"{site}/docs/", max_depth=2, limit=2, allow_external=False)
    assert len((await crawl.crawl(req))["results"]) == 2


async def test_crawl_follows_links_rendered_by_js(site):
    response = await crawl.map_site(MapRequest(url=f"{site}/spa-nav", allow_external=False))
    assert paths(site, response["results"]) == ["/spa-nav", "/docs/b"]


async def test_timeout_returns_partial_result(site):
    req = MapRequest(url=f"{site}/slow")
    req.timeout = 2.5
    response = await crawl.map_site(req)
    assert response["results"] == [f"{site}/slow"]
    assert response["response_time"] < 3


def test_invalid_regex_rejected():
    with pytest.raises(ValidationError, match="invalid regex"):
        MapRequest(url="https://a.com", select_paths=["/docs/("])
