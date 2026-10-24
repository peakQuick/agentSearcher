from agentsearcher import crawl, pipeline
from agentsearcher.schemas import CrawlRequest, ExtractRequest, SearchRequest
from agentsearcher.search import Hit


async def test_extract_pdf_and_scan(site):
    response = await pipeline.extract(
        ExtractRequest(urls=[f"{site}/files/manual.pdf", f"{site}/files/scan.pdf"])
    )
    [result] = response["results"]
    assert result["raw_content"].startswith("## Page 1\n\nWidget manual")
    assert "## Page 2\n\nCalibrate the flux capacitor" in result["raw_content"]
    assert response["failed_results"] == [
        {"url": f"{site}/files/scan.pdf", "error": "PDF has no text layer"}
    ]


async def test_search_reads_pdf_and_falls_back_to_snippet_for_scans(site, monkeypatch):
    hits = [
        Hit(f"{site}/files/manual.pdf", "Manual", "widget manual pdf"),
        Hit(f"{site}/files/scan.pdf", "Scan", "Scanned flux capacitor notes"),
    ]

    async def fake_search(*_args, **_kwargs):
        return hits

    monkeypatch.setattr(pipeline, "web_search", fake_search)
    req = SearchRequest(query="calibrate flux capacitor", search_depth="basic")
    by_url = {r["url"]: r["content"] for r in (await pipeline.search(req))["results"]}
    assert "Calibrate the flux capacitor of the widget yearly." in by_url[hits[0].url]
    assert by_url[hits[1].url] == hits[1].snippet


async def test_crawl_loads_linked_pdf(site):
    req = CrawlRequest(url=f"{site}/docs/", allow_external=False)
    by_path = {
        r["url"].removeprefix(site): r["raw_content"] for r in (await crawl.crawl(req))["results"]
    }
    assert "Install the widget on a flat surface." in by_path["/files/manual.pdf"]
