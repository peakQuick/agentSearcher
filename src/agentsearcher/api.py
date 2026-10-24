from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agentsearcher import config, pipeline
from agentsearcher import crawl as crawl_module
from agentsearcher.schemas import CrawlRequest, ExtractRequest, MapRequest, SearchRequest

usage = {"requests": 0, "credits": 0}


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
