import asyncio

import pytest
from conftest import DYNAMIC, Handler

from agentsearcher import config, pipeline, stats
from agentsearcher import search as search_module
from agentsearcher.fetch import FetchError, js_domains
from agentsearcher.schemas import ExtractRequest, SearchRequest
from agentsearcher.search import Hit


async def load(url, snippet=None, browser=True, timeout=15.0):
    js_domains._until.clear()
    return await pipeline.load_page(
        url, snippet, browser=browser, strict=False, recall=False, timeout=timeout
    )


async def test_static_page(site):
    page = await load(f"{site}/article")
    assert not page.rendered
    assert page.title == "Static widgets"
    assert page.published_date == "2025-05-01"
    assert page.favicon == f"{site}/icon.png"
    assert page.images == [f"{site}/photo.jpg"]
    assert "## Maintenance" in page.markdown


async def test_spa_is_rendered(site):
    page = await load(f"{site}/spa")
    assert page.rendered
    assert "Quantum gizmos" in page.markdown


async def test_half_ssr_rendered_on_snippet_mismatch(site):
    page = await load(f"{site}/half", snippet=DYNAMIC)
    assert page.rendered
    assert "teleportation" in page.markdown


async def test_half_ssr_without_snippet_stays_static(site):
    page = await load(f"{site}/half")
    assert not page.rendered


async def test_js_domain_memory(site):
    await load(f"{site}/spa")
    assert js_domains.wants("127.0.0.1")


async def test_sparse_page_does_not_mark_domain(site):
    page = await load(f"{site}/sparse")
    assert "short list" in page.markdown
    assert not js_domains.wants("127.0.0.1")


async def test_challenge_waits_until_passed(site):
    page = await load(f"{site}/challenge")
    assert page.rendered
    assert "teleportation" in page.markdown


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("/missing", "HTTP 404"),
        ("/pdf", "PDF has no text layer"),  # truncated file
        ("/image", "unsupported content type: image/png"),
    ],
)
async def test_fetch_errors(site, path, error):
    with pytest.raises(FetchError, match=error):
        await load(f"{site}{path}")


async def test_extract_with_query_returns_relevant_chunks(site):
    response = await pipeline.extract(
        ExtractRequest(
            urls=[f"{site}/article", f"{site}/missing", f"{site}/slow"],
            query="how often to lubricate bearings",
            chunks_per_source=1,
            timeout=2,
        )
    )
    [result] = response["results"]
    assert "Lubricate widget bearings" in result["raw_content"]
    assert len(result["raw_content"]) <= 700
    errors = {r["url"].rsplit("/", 1)[1]: r["error"] for r in response["failed_results"]}
    assert errors == {"missing": "HTTP 404", "slow": "timed out"}


async def test_search_uses_page_content_and_falls_back_to_snippet(site, monkeypatch):
    hits = [
        Hit(f"{site}/article", "Widgets", "Widgets are small mechanical parts"),
        Hit(f"{site}/missing", "Gone", "Lubricate bearings snippet from search engine"),
    ]

    async def fake_search(*_args, **_kwargs):
        return hits

    monkeypatch.setattr(pipeline, "web_search", fake_search)
    response = await pipeline.search(
        SearchRequest(
            query="lubricate widget bearings",
            search_depth="fast",
            include_raw_content="text",
            include_favicon=True,
            include_answer=True,
        )
    )
    by_url = {r["url"]: r for r in response["results"]}
    article = by_url[f"{site}/article"]
    assert "Lubricate widget bearings" in article["content"]
    assert "##" not in article["raw_content"]
    assert article["favicon"] == f"{site}/icon.png"
    assert by_url[f"{site}/missing"]["content"] == hits[1].snippet
    assert by_url[f"{site}/missing"]["raw_content"] is None
    assert "Lubricate" in response["answer"]
    assert response["results"][0]["score"] >= response["results"][-1]["score"]


async def test_answer_from_openai_compatible_llm(site, monkeypatch):
    async def fake_search(*_args, **_kwargs):
        return [Hit("https://a.com/1", "A", "Widgets need lubrication twice a year.")]

    monkeypatch.setattr(pipeline, "web_search", fake_search)
    monkeypatch.setattr(config, "LLM_BASE_URL", f"{site}/v1")
    req = SearchRequest(query="how often lubricate", search_depth="ultra-fast", include_answer=True)
    assert (await pipeline.search(req))["answer"] == "LLM says: how often lubricate"

    monkeypatch.setattr(config, "LLM_BASE_URL", f"{site}/wrong")
    answer = (await pipeline.search(req))["answer"]
    assert answer == "Widgets need lubrication twice a year."


async def test_search_dedup_drops_reprint_unless_disabled(site, monkeypatch):
    hits = [
        Hit(f"{site}/news/a", "Herald", "tram line approved"),
        Hit(f"{site}/news/b", "Post", "tram line approved"),
    ]

    async def fake_search(*_args, **_kwargs):
        return hits

    monkeypatch.setattr(pipeline, "web_search", fake_search)
    req = SearchRequest(query="tram line council debate", search_depth="fast")
    assert [r["url"] for r in (await pipeline.search(req))["results"]] == [hits[0].url]
    req.dedup = False
    assert len((await pipeline.search(req))["results"]) == 2


async def test_search_max_per_domain(monkeypatch):
    hits = [Hit(f"https://a.com/{i}", "A", f"widgets {i} " * 5) for i in range(3)]
    hits.append(Hit("https://www.b.org/1", "B", "widgets b " * 5))

    async def fake_search(*_args, **_kwargs):
        return hits

    monkeypatch.setattr(pipeline, "web_search", fake_search)
    req = SearchRequest(query="widgets", search_depth="ultra-fast", max_per_domain=1)
    urls = {r["url"] for r in (await pipeline.search(req))["results"]}
    assert urls == {"https://a.com/0", "https://www.b.org/1"}


async def test_search_domain_filters(monkeypatch):
    hits = [Hit("https://a.com/1", "A", "x " * 5), Hit("https://sub.b.org/2", "B", "x " * 5)]
    seen = {}

    async def fake_search(query, **_kwargs):
        seen["query"] = query
        return hits

    monkeypatch.setattr(pipeline, "web_search", fake_search)
    req = SearchRequest(query="x", search_depth="ultra-fast", include_domains=["b.org"])
    response = await pipeline.search(req)
    assert seen["query"] == "x site:b.org"
    assert [r["url"] for r in response["results"]] == ["https://sub.b.org/2"]

    req = SearchRequest(query="x", search_depth="ultra-fast", exclude_domains=["https://a.com"])
    response = await pipeline.search(req)
    assert [r["url"] for r in response["results"]] == ["https://sub.b.org/2"]


def test_fuse_prefers_urls_engines_agree_on():
    def row(path):
        return {"href": f"https://site.com/{path}", "title": path}

    one = [row("seo"), row("shared"), row("a")]
    two = [row("b"), row("c"), row("shared")]
    fused = [r["title"] for r in search_module.fuse([one, two])]
    assert fused == ["shared", "seo", "b", "c", "a"]


async def test_domain_rules(monkeypatch, tmp_path):
    rules = tmp_path / "rules.txt"
    rules.write_text(
        "# spam\nblock spam.com\nlower https://www.meh.com/\nraise good.org  # docs\nbogus line\n"
    )
    loaded = pipeline.load_domain_rules(str(rules))
    assert loaded == {"block": ["spam.com"], "lower": ["meh.com"], "raise": ["good.org"]}

    hits = [Hit(f"https://{d}/1", d, f"widgets {d} " * 5) for d in ["spam.com", "meh.com"]]
    hits.append(Hit("https://docs.good.org/1", "G", "widgets good " * 5))

    async def fake_search(*_args, **_kwargs):
        return hits

    monkeypatch.setattr(pipeline, "web_search", fake_search)
    monkeypatch.setattr(pipeline, "domain_rules", loaded)
    req = SearchRequest(query="widgets", search_depth="ultra-fast")
    results = (await pipeline.search(req))["results"]
    assert [r["url"] for r in results] == ["https://docs.good.org/1", "https://meh.com/1"]
    assert results[1]["score"] < 0.9


async def test_stats_count_requests_and_pages(site, monkeypatch):
    search_stats = dict(stats.requests["search"])
    failed_pages = stats.pages["failed"]

    async def fake_search(*_args, **_kwargs):
        return [Hit("https://a.com/1", "A", "widgets " * 5)]

    async def broken_search(*_args, **_kwargs):
        raise RuntimeError("engines down")

    monkeypatch.setattr(pipeline, "web_search", fake_search)
    await pipeline.search(SearchRequest(query="widgets", search_depth="ultra-fast"))
    assert stats.requests["search"]["ok"] == search_stats["ok"] + 1

    monkeypatch.setattr(pipeline, "web_search", broken_search)
    with pytest.raises(RuntimeError):
        await pipeline.search(SearchRequest(query="broken", search_depth="ultra-fast"))
    assert stats.requests["search"]["failed"] == search_stats["failed"] + 1
    assert stats.recent[0]["target"] == "broken"
    assert stats.recent[0]["error"] == "engines down"

    await pipeline.extract(ExtractRequest(urls=[f"{site}/missing"]))
    assert stats.pages["failed"] == failed_pages + 1
    assert stats.recent[0]["kind"] == "extract"


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setattr(config, "CACHE_TTL", 900.0)
    monkeypatch.setattr(pipeline, "page_cache", pipeline.PageCache())
    return pipeline.page_cache


async def cached_load(url, browser=True):
    return await pipeline.load_or_error(
        url, None, browser=browser, strict=False, recall=False, timeout=5.0
    )


async def test_page_cache_reuses_loaded_page(site, cache):
    first = await cached_load(f"{site}/article")
    fetches = Handler.hits.count("/article")
    assert await cached_load(f"{site}/article") is first
    assert Handler.hits.count("/article") == fetches


async def test_page_cache_static_page_not_reused_when_browser_allowed(site, cache):
    await cached_load(f"{site}/article", browser=False)
    assert cache.get(f"{site}/article", False, browser=False)
    assert cache.get(f"{site}/article", False, browser=True) is None


async def test_page_cache_skips_errors(site, cache):
    assert await cached_load(f"{site}/missing") == "HTTP 404"
    assert cache.get(f"{site}/missing", False, browser=False) is None


async def test_page_cache_expiry_and_size_limit(site, cache, monkeypatch):
    page = await cached_load(f"{site}/article")
    monkeypatch.setattr(config, "CACHE_TTL", 0.01)
    cache.put("https://a.com/", False, True, page)
    await asyncio.sleep(0.05)
    assert cache.get("https://a.com/", False, True) is None
    cache.prune()
    assert list(cache._items) == [(f"{site}/article", False)]

    monkeypatch.setattr(config, "CACHE_TTL", 900.0)
    monkeypatch.setattr(config, "CACHE_MB", 1.5 * len(page.markdown) / 2**20)
    cache.put("https://b.com/", False, True, page)
    assert list(cache._items) == [("https://b.com/", False)]


async def test_web_search_cache_keeps_only_non_empty(monkeypatch):
    calls = []

    async def fake_search(query, **_kwargs):
        calls.append(query)
        return [Hit("https://a.com/", "A", "a")] if query == "found" else []

    monkeypatch.setattr(config, "CACHE_TTL", 900.0)
    monkeypatch.setattr(search_module, "_web_search", fake_search)
    monkeypatch.setattr(search_module, "_hits_cache", {})
    args = {"max_results": 5, "news": False, "region": "us-en", "timelimit": None, "safe": True}
    for _ in range(2):
        assert len(await search_module.web_search("found", **args)) == 1
        assert await search_module.web_search("nothing", **args) == []
    assert calls == ["found", "nothing", "nothing"]
