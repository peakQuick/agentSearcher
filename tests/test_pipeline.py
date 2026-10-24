import pytest
from conftest import DYNAMIC

from agentsearcher import config, pipeline
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
