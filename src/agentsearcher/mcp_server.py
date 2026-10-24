from typing import Literal

from mcp.server.mcpserver import MCPServer

from agentsearcher import config, crawl, pipeline
from agentsearcher.schemas import CrawlRequest, ExtractRequest, MapRequest, SearchRequest

server = MCPServer("agentsearcher")
PREFIX = "tavily" if config.MCP_TAVILY_NAMES else "web"


def format_search(response: dict) -> str:
    lines = []
    if response.get("answer"):
        lines += [f"Answer: {response['answer']}", ""]
    for i, r in enumerate(response["results"], 1):
        lines += [f"## {i}. {r['title']}", f"URL: {r['url']}"]
        if r.get("published_date"):
            lines.append(f"Published: {r['published_date']}")
        lines += [r["content"], ""]
        if r.get("raw_content"):
            lines += ["Raw content:", r["raw_content"], ""]
    return "\n".join(lines).strip() or "No results found."


def format_extract(response: dict) -> str:
    lines = []
    for r in response["results"]:
        lines += [f"## {r['url']}", r["raw_content"], ""]
    for r in response.get("failed_results", []):
        lines.append(f"Failed: {r['url']}: {r['error']}")
    return "\n".join(lines).strip() or "Nothing extracted."


@server.tool(
    name=f"{PREFIX}_search",
    description="Search the web. Returns the most relevant passages of each result page, "
    "already trimmed to fit a small context window.",
)
async def web_search(
    query: str,
    max_results: int = 5,
    search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic",
    topic: Literal["general", "news"] = "general",
    time_range: Literal["day", "week", "month", "year"] | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_raw_content: bool = False,
    country: str | None = None,
    max_per_domain: int | None = None,
    dedup: bool = True,
) -> str:
    req = SearchRequest(
        query=query,
        max_results=max_results,
        search_depth=search_depth,
        topic=topic,
        time_range=time_range,
        include_domains=include_domains or [],
        exclude_domains=exclude_domains or [],
        include_raw_content=include_raw_content,
        country=country,
        max_per_domain=max_per_domain,
        dedup=dedup,
    )
    return format_search(await pipeline.search(req))


@server.tool(
    name=f"{PREFIX}_extract",
    description="Extract page content from URLs. With `query`, returns only the passages "
    "relevant to it instead of the whole page.",
)
async def web_extract(
    urls: list[str],
    query: str | None = None,
    extract_depth: Literal["basic", "advanced"] = "basic",
    format: Literal["markdown", "text"] = "markdown",
) -> str:
    req = ExtractRequest(urls=urls, query=query, extract_depth=extract_depth, format=format)
    return format_extract(await pipeline.extract(req))


@server.tool(
    name=f"{PREFIX}_crawl",
    description="Crawl a website from a root URL and return page contents. With "
    "`instructions`, follows the most relevant links first and returns only matching passages.",
)
async def web_crawl(
    url: str,
    instructions: str | None = None,
    max_depth: int = 1,
    max_breadth: int = 20,
    limit: int = 20,
    select_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    allow_external: bool = False,
    extract_depth: Literal["basic", "advanced"] = "basic",
    dedup: bool = True,
) -> str:
    req = CrawlRequest(
        url=url,
        instructions=instructions,
        max_depth=max_depth,
        max_breadth=max_breadth,
        limit=limit,
        select_paths=select_paths or [],
        exclude_paths=exclude_paths or [],
        allow_external=allow_external,
        extract_depth=extract_depth,
        dedup=dedup,
    )
    return format_extract(await crawl.crawl(req))


@server.tool(
    name=f"{PREFIX}_map",
    description="List the URLs of a website (site map) starting from a root URL, without content.",
)
async def web_map(
    url: str,
    instructions: str | None = None,
    max_depth: int = 1,
    max_breadth: int = 20,
    limit: int = 50,
    select_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    allow_external: bool = False,
) -> str:
    req = MapRequest(
        url=url,
        instructions=instructions,
        max_depth=max_depth,
        max_breadth=max_breadth,
        limit=limit,
        select_paths=select_paths or [],
        exclude_paths=exclude_paths or [],
        allow_external=allow_external,
    )
    return "\n".join((await crawl.map_site(req))["results"])
