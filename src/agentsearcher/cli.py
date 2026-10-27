import argparse
import asyncio
import json
import logging
import subprocess
import sys

from agentsearcher import config


def _setup_logging(stream=sys.stderr):
    logging.basicConfig(
        level=config.LOG_LEVEL.upper(),
        stream=stream,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in (
        "httpx",
        "httpx2",
        "huggingface_hub",
        "primp",
        "trafilatura",
        "htmldate",
        "courlan",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def _run_once(coro):
    from agentsearcher import pipeline

    try:
        return await coro
    finally:
        await pipeline.shutdown()


def _cmd_serve(args):
    import uvicorn

    from agentsearcher.api import with_mcp

    uvicorn.run(with_mcp(args.host), host=args.host, port=args.port, log_level="info")


def _cmd_mcp(args):
    from agentsearcher.mcp_server import server

    if args.http:
        import uvicorn

        from agentsearcher.api import cors

        app = cors(server.streamable_http_app(host=args.host))
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    else:
        server.run("stdio")


def _cmd_search(args):
    from agentsearcher import pipeline
    from agentsearcher.mcp_server import format_search
    from agentsearcher.schemas import SearchRequest

    req = SearchRequest(
        query=args.query,
        search_depth=args.depth,
        max_results=args.max_results,
        topic=args.topic,
        include_answer=args.answer,
        include_raw_content="markdown" if args.raw else False,
        max_per_domain=args.max_per_domain,
        dedup=not args.no_dedup,
    )
    response = asyncio.run(_run_once(pipeline.search(req)))
    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return
    print(format_search(response))
    chars = sum(len(r["content"]) + len(r["raw_content"] or "") for r in response["results"])
    print(f"\n-- {len(response['results'])} results, {chars} chars, {response['response_time']}s")


def _cmd_extract(args):
    from agentsearcher import pipeline
    from agentsearcher.mcp_server import format_extract
    from agentsearcher.schemas import ExtractRequest

    req = ExtractRequest(
        urls=args.urls, query=args.query, extract_depth=args.depth, format=args.format
    )
    response = asyncio.run(_run_once(pipeline.extract(req)))
    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return
    print(format_extract(response))
    print(
        f"\n-- {len(response['results'])} ok, {len(response['failed_results'])} failed, "
        f"{response['response_time']}s"
    )


def _walk_fields(args) -> dict:
    return {
        "url": args.url,
        "instructions": args.instructions,
        "max_depth": args.max_depth,
        "max_breadth": args.max_breadth,
        "limit": args.limit,
        "select_paths": args.select_path,
        "exclude_paths": args.exclude_path,
        "allow_external": args.external,
        "timeout": args.timeout,
    }


def _cmd_crawl(args):
    from agentsearcher import crawl
    from agentsearcher.mcp_server import format_extract
    from agentsearcher.schemas import CrawlRequest

    req = CrawlRequest(
        **_walk_fields(args),
        extract_depth=args.depth,
        format=args.format,
        dedup=not args.no_dedup,
    )
    response = asyncio.run(_run_once(crawl.crawl(req)))
    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return
    print(format_extract(response))
    chars = sum(len(r["raw_content"]) for r in response["results"])
    print(f"\n-- {len(response['results'])} pages, {chars} chars, {response['response_time']}s")


def _cmd_map(args):
    from agentsearcher import crawl
    from agentsearcher.schemas import MapRequest

    response = asyncio.run(_run_once(crawl.map_site(MapRequest(**_walk_fields(args)))))
    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return
    print("\n".join(response["results"]))
    print(f"\n-- {len(response['results'])} urls, {response['response_time']}s")


def _cmd_fetch_model(_args):
    from agentsearcher.rank import reranker

    print(f"reranker check score: {reranker.score('test', ['test passage'])[0]:.3f}")


def _cmd_setup(args):
    install = [sys.executable, "-m", "patchright", "install", "chromium"]
    subprocess.run(install + (["--with-deps"] if args.with_deps else []), check=True)
    _cmd_fetch_model(args)


def main():
    parser = argparse.ArgumentParser(
        prog="agentsearcher", description="Local Tavily-compatible search API"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the Tavily-compatible HTTP API and MCP at /mcp")
    serve.add_argument("--host", default=config.HOST)
    serve.add_argument("--port", type=int, default=config.PORT)
    serve.set_defaults(func=_cmd_serve)

    mcp = sub.add_parser("mcp", help="run the MCP server (stdio by default)")
    mcp.add_argument("--http", action="store_true", help="use streamable HTTP instead of stdio")
    mcp.add_argument("--host", default=config.HOST)
    mcp.add_argument("--port", type=int, default=config.PORT + 1)
    mcp.set_defaults(func=_cmd_mcp)

    search = sub.add_parser("search", help="run one search and print the result")
    search.add_argument("query")
    search.add_argument(
        "-d", "--depth", default="basic", choices=["ultra-fast", "fast", "basic", "advanced"]
    )
    search.add_argument("-n", "--max-results", type=int, default=5)
    search.add_argument("--topic", default="general", choices=["general", "news", "finance"])
    search.add_argument("--answer", action="store_true", help="include an answer")
    search.add_argument("--raw", action="store_true", help="include raw page content")
    search.add_argument("--max-per-domain", type=int, help="at most N results from one domain")
    search.add_argument("--no-dedup", action="store_true", help="keep near-duplicate content")
    search.add_argument("--json", action="store_true", help="print the raw API response")
    search.set_defaults(func=_cmd_search)

    extract = sub.add_parser("extract", help="extract one or more URLs and print the result")
    extract.add_argument("urls", nargs="+")
    extract.add_argument("-q", "--query", help="return only passages relevant to this query")
    extract.add_argument("-d", "--depth", default="basic", choices=["basic", "advanced"])
    extract.add_argument("--format", default="markdown", choices=["markdown", "text"])
    extract.add_argument("--json", action="store_true", help="print the raw API response")
    extract.set_defaults(func=_cmd_extract)

    walk = argparse.ArgumentParser(add_help=False)
    walk.add_argument("url")
    walk.add_argument("-i", "--instructions", help="what to look for; guides link order")
    walk.add_argument("--max-depth", type=int, default=1)
    walk.add_argument("--max-breadth", type=int, default=20)
    walk.add_argument("--limit", type=int, default=50)
    walk.add_argument("--select-path", action="append", default=[], help="path regex, repeatable")
    walk.add_argument("--exclude-path", action="append", default=[], help="path regex, repeatable")
    walk.add_argument("--external", action="store_true", help="also follow other domains")
    walk.add_argument("--timeout", type=float, default=150.0)
    walk.add_argument("--json", action="store_true", help="print the raw API response")

    crawl = sub.add_parser("crawl", parents=[walk], help="crawl a site and print page contents")
    crawl.add_argument("-d", "--depth", default="basic", choices=["basic", "advanced"])
    crawl.add_argument("--format", default="markdown", choices=["markdown", "text"])
    crawl.add_argument("--no-dedup", action="store_true", help="keep near-duplicate passages")
    crawl.set_defaults(func=_cmd_crawl)

    site_map = sub.add_parser("map", parents=[walk], help="list the URLs of a site")
    site_map.set_defaults(func=_cmd_map)

    fetch_model = sub.add_parser("fetch-model", help="download and smoke-test the reranker")
    fetch_model.set_defaults(func=_cmd_fetch_model)

    setup = sub.add_parser("setup", help="download Chromium and the reranker (one time)")
    setup.add_argument(
        "--with-deps", action="store_true", help="also install Chromium's system libraries (root)"
    )
    setup.set_defaults(func=_cmd_setup)

    args = parser.parse_args()
    # stdio MCP owns stdout, so logs must never go there
    _setup_logging()
    args.func(args)


if __name__ == "__main__":
    main()
