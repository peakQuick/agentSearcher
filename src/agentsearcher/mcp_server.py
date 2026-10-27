from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from agentsearcher import config, crawl, pipeline, stats
from agentsearcher.schemas import (
    CrawlRequest,
    ExtractRequest,
    MapRequest,
    SearchRequest,
    first_error,
)

server = MCPServer("agentsearcher")
PREFIX = "tavily" if config.MCP_TAVILY_NAMES else "web"

# parameter descriptions are what the agent sees in the tool schema; keep them to one line
SiteUrl = Annotated[str, Field(description="Root URL of the site, e.g. https://docs.python.org/3/")]
Instructions = Annotated[
    str | None,
    Field(description="What you are looking for; the most relevant links are followed first"),
]
MaxDepth = Annotated[int, Field(description="How many link hops from the root (1-5)")]
MaxBreadth = Annotated[int, Field(description="How many new links to take from each page")]
SelectPaths = Annotated[
    list[str] | None,
    Field(description='Only URLs whose path matches one of these regexes, e.g. ["/docs/.*"]'),
]
ExcludePaths = Annotated[
    list[str] | None, Field(description="Skip URLs whose path matches one of these regexes")
]
AllowExternal = Annotated[
    bool,
    Field(description="Also follow links to other domains; by default the crawl stays on the site"),
]
ExtractDepth = Annotated[
    Literal["basic", "advanced"],
    Field(description="advanced: slower (30 s per page), gets more text from complex pages"),
]
Dedup = Annotated[bool, Field(description="Drop repeated and near-duplicate passages")]


async def _run(call, request_type, **params) -> dict:
    """Build the request and run it. Anything but a ToolError raised in a tool body reaches
    the agent as a bare "Error executing tool", so bad arguments and a full queue become one."""
    try:
        return await call(request_type(**params))
    except ValidationError as e:
        raise ToolError(first_error(e.errors())) from None
    except stats.Busy as e:
        raise ToolError(str(e)) from None


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
    f"already trimmed to fit a small context window. To read a whole result page, "
    f"pass its URL to {PREFIX}_extract.",
)
async def web_search(
    query: Annotated[
        str, Field(description="Search query, as you would type it in a search engine")
    ],
    max_results: Annotated[int, Field(description="Number of results (0-20)")] = 5,
    search_depth: Annotated[
        Literal["basic", "advanced", "fast", "ultra-fast"],
        Field(
            description="ultra-fast: snippets only; fast: pages without JS; "
            "basic: + JS pages and neural ranking; advanced: + more candidates, slower"
        ),
    ] = "basic",
    topic: Annotated[
        Literal["general", "news"], Field(description="news: search news articles")
    ] = "general",
    time_range: Annotated[
        Literal["day", "week", "month", "year"] | None,
        Field(description="Only results from the last day/week/month/year"),
    ] = None,
    include_domains: Annotated[
        list[str] | None, Field(description="Only results from these domains, e.g. ['github.com']")
    ] = None,
    exclude_domains: Annotated[
        list[str] | None, Field(description="Never return results from these domains")
    ] = None,
    include_raw_content: Annotated[
        bool, Field(description="Also return the full text of each page (long)")
    ] = False,
    country: Annotated[
        str | None,
        Field(description="Search region, e.g. 'germany'; guessed from the query if empty"),
    ] = None,
    max_per_domain: Annotated[
        int | None, Field(description="At most this many results from one domain")
    ] = None,
    dedup: Dedup = True,
) -> str:
    response = await _run(
        pipeline.search,
        SearchRequest,
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
    return format_search(response)


@server.tool(
    name=f"{PREFIX}_extract",
    description="Extract page content from URLs (HTML, JS pages, PDF). With `query`, returns only "
    "the passages relevant to it instead of the whole page.",
)
async def web_extract(
    urls: Annotated[list[str], Field(description="1-20 URLs to read")],
    query: Annotated[
        str | None,
        Field(description="Return only passages relevant to this question instead of whole pages"),
    ] = None,
    extract_depth: ExtractDepth = "basic",
    format: Annotated[
        Literal["markdown", "text"], Field(description="text: plain text without markdown")
    ] = "markdown",
) -> str:
    response = await _run(
        pipeline.extract,
        ExtractRequest,
        urls=urls,
        query=query,
        extract_depth=extract_depth,
        format=format,
    )
    return format_extract(response)


@server.tool(
    name=f"{PREFIX}_crawl",
    description="Crawl a website from a root URL and return page contents. With "
    "`instructions`, follows the most relevant links first and returns only matching passages. "
    f"To only list a site's URLs, use {PREFIX}_map.",
)
async def web_crawl(
    url: SiteUrl,
    instructions: Instructions = None,
    max_depth: MaxDepth = 1,
    max_breadth: MaxBreadth = 20,
    limit: Annotated[int, Field(description="Maximum number of pages to return")] = 20,
    select_paths: SelectPaths = None,
    exclude_paths: ExcludePaths = None,
    allow_external: AllowExternal = False,
    extract_depth: ExtractDepth = "basic",
    dedup: Dedup = True,
) -> str:
    response = await _run(
        crawl.crawl,
        CrawlRequest,
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
    return format_extract(response)


@server.tool(
    name=f"{PREFIX}_map",
    description="List the URLs of a website (site map) starting from a root URL, without content.",
)
async def web_map(
    url: SiteUrl,
    instructions: Instructions = None,
    max_depth: MaxDepth = 1,
    max_breadth: MaxBreadth = 20,
    limit: Annotated[int, Field(description="Maximum number of URLs to return")] = 50,
    select_paths: SelectPaths = None,
    exclude_paths: ExcludePaths = None,
    allow_external: AllowExternal = False,
) -> str:
    response = await _run(
        crawl.map_site,
        MapRequest,
        url=url,
        instructions=instructions,
        max_depth=max_depth,
        max_breadth=max_breadth,
        limit=limit,
        select_paths=select_paths or [],
        exclude_paths=exclude_paths or [],
        allow_external=allow_external,
    )
    lines = response["results"] + [
        f"Failed: {r['url']}: {r['error']}" for r in response["failed_results"]
    ]
    return "\n".join(lines)
