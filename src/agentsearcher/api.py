import asyncio
import time
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from agentsearcher import config, pipeline, stats
from agentsearcher import crawl as crawl_module
from agentsearcher import search as search_module
from agentsearcher.fetch import renderer
from agentsearcher.rank import reranker
from agentsearcher.schemas import CrawlRequest, ExtractRequest, MapRequest, SearchRequest

usage = {"requests": 0, "credits": 0}
DASHBOARD = Path(__file__).with_name("dashboard.html")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await pipeline.shutdown()


app = FastAPI(title="agentsearcher", lifespan=lifespan)


@app.exception_handler(ApiError)
async def _api_error(_request: Request, exc: ApiError):
    return JSONResponse({"detail": {"error": exc.message}}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first["loc"] if p != "body")
    message = f"{where}: {first['msg']}" if where else first["msg"]
    return JSONResponse({"detail": {"error": message}}, status_code=400)


def _authorize(request: Request, body_key: str | None):
    if not config.API_KEYS:
        return
    header = request.headers.get("authorization", "")
    key = header.removeprefix("Bearer ").strip() or body_key
    if key not in config.API_KEYS:
        raise ApiError(401, "Unauthorized: missing or invalid API key.")


def _count(response: dict, credits: int):
    usage["requests"] += 1
    usage["credits"] += credits
    return response


@app.post("/search")
async def search(req: SearchRequest, request: Request):
    _authorize(request, req.api_key)
    return _count(await pipeline.search(req), 2 if req.search_depth == "advanced" else 1)


@app.post("/extract")
async def extract(req: ExtractRequest, request: Request):
    _authorize(request, req.api_key)
    response = await pipeline.extract(req)
    return _count(response, len(response["results"]))


@app.post("/crawl")
async def crawl(req: CrawlRequest, request: Request):
    _authorize(request, req.api_key)
    response = await crawl_module.crawl(req)
    return _count(response, len(response["results"]))


@app.post("/map")
async def map_site(req: MapRequest, request: Request):
    _authorize(request, req.api_key)
    response = await crawl_module.map_site(req)
    return _count(response, len(response["results"]))


@app.get("/usage")
async def get_usage(request: Request):
    _authorize(request, None)
    return {
        "key": {"usage": usage["credits"], "limit": None},
        "account": {"current_plan": "local", "plan_usage": usage["credits"], "plan_limit": None},
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stats")
async def get_stats(request: Request):
    _authorize(request, None)
    cache = pipeline.page_cache
    return {
        "version": version("agentsearcher"),
        "uptime": round(time.time() - stats.started),
        "requests": stats.requests,
        "pages": stats.pages,
        "cache": {
            "pages": len(cache._items),
            "mb": round(cache._size / 2**20, 1),
            "searches": len(search_module._hits_cache),
        },
        "components": {
            "browser": renderer._browser is not None,
            "model": reranker._session is not None,
        },
        "system": await asyncio.to_thread(stats.system),
        "recent": list(stats.recent),
    }


@app.get("/", include_in_schema=False)
async def dashboard():
    return HTMLResponse(DASHBOARD.read_text(encoding="utf-8"))


def cors(asgi_app):
    """Browser clients (llama.cpp webui) need CORS preflight and a readable session header."""
    return CORSMiddleware(
        asgi_app,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )


def with_mcp(host: str):
    """The API plus the MCP server (streamable HTTP) at /mcp, in one process: one page cache,
    browser and model. API_KEYS applies to /mcp as well."""
    from agentsearcher.mcp_server import server

    mcp_app = server.streamable_http_app(host=host)

    async def guarded_mcp(scope, receive, send):
        key = Headers(scope=scope).get("authorization", "").removeprefix("Bearer ").strip()
        if config.API_KEYS and key not in config.API_KEYS:
            await JSONResponse(
                {"detail": {"error": "Unauthorized: missing or invalid API key."}}, 401
            )(scope, receive, send)
        else:
            await mcp_app(scope, receive, send)

    mcp_cors = cors(guarded_mcp)

    async def dispatch(scope, receive, send):
        if scope["path"].rstrip("/") == "/mcp":
            await mcp_cors(scope, receive, send)
        else:
            await app(scope, receive, send)

    @asynccontextmanager
    async def both_lifespans(_app):
        # mounted apps don't get lifespan events, so run both here
        async with server.session_manager.run():
            yield
        await pipeline.shutdown()

    return Starlette(routes=[Mount("", app=dispatch)], lifespan=both_lifespans)
