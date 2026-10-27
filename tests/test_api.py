import os
import socket
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from agentsearcher import api, config, pipeline


@pytest.fixture
def client(monkeypatch):
    async def fake_search(req):
        return {"query": req.query, "results": []}

    monkeypatch.setattr(pipeline, "search", fake_search)
    return TestClient(api.app)


def test_validation_error_is_tavily_shaped(client):
    resp = client.post("/search", json={"query": "x", "search_depth": "deep"})
    assert resp.status_code == 400
    assert "search_depth" in resp.json()["detail"]["error"]


def test_unknown_fields_ignored(client):
    resp = client.post("/search", json={"query": "x", "some_future_param": True})
    assert resp.status_code == 200


def test_domains_mode_requires_domains(client):
    resp = client.post("/search", json={"query": "x", "include_domains_mode": "prefer"})
    assert resp.status_code == 400


def test_crawl_invalid_regex_is_400(client):
    resp = client.post("/crawl", json={"url": "https://a.com", "exclude_paths": ["("]})
    assert resp.status_code == 400
    assert "invalid regex" in resp.json()["detail"]["error"]


def test_auth(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEYS", {"secret"})
    assert client.post("/search", json={"query": "x"}).status_code == 401
    headers = {"Authorization": "Bearer secret"}
    assert client.post("/search", json={"query": "x"}, headers=headers).status_code == 200
    body = {"query": "x", "api_key": "secret"}
    assert client.post("/search", json=body).status_code == 200


def test_dashboard_and_stats(client, monkeypatch):
    page = client.get("/")
    assert page.status_code == 200
    assert "<title>agentsearcher</title>" in page.text

    stats = client.get("/stats").json()
    assert set(stats) == {
        "version",
        "uptime",
        "requests",
        "pages",
        "cache",
        "components",
        "system",
        "recent",
    }
    assert set(stats["requests"]) == {"search", "extract", "crawl", "map"}
    assert stats["system"]["memory_mb"] > 0
    assert stats["system"]["processes"] >= 1

    monkeypatch.setattr(config, "API_KEYS", {"secret"})
    assert client.get("/stats").status_code == 401
    assert client.get("/").status_code == 200
    assert client.get("/stats", headers={"Authorization": "Bearer secret"}).status_code == 200


@pytest.fixture(scope="module")
def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {
        **os.environ,
        "AGENTSEARCHER_API_KEYS": "tvly-local",
        "AGENTSEARCHER_ALLOW_PRIVATE_URLS": "1",
        "AGENTSEARCHER_CACHE_TTL": "0",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentsearcher.cli", "serve", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait()


def test_tavily_python_extract_contract(server, site):
    from tavily import TavilyClient

    tavily = TavilyClient(api_key="tvly-local", api_base_url=server)
    response = tavily.extract(
        urls=[f"{site}/article", f"{site}/missing"], include_images=True, include_favicon=True
    )
    [result] = response["results"]
    assert result["url"] == f"{site}/article"
    assert "Lubricate" in result["raw_content"]
    assert result["images"] and result["favicon"]
    assert response["failed_results"][0]["error"] == "HTTP 404"
    assert isinstance(response["response_time"], float)


async def test_mcp_on_api_port(server, site):
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient() as anonymous:
        response = await anonymous.post(f"{server}/mcp", json={})
    assert response.status_code == 401

    headers = {"Authorization": "Bearer tvly-local"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=30) as http,
        streamable_http_client(f"{server}/mcp", http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert {t.name for t in tools} == {"web_search", "web_extract", "web_crawl", "web_map"}
        result = await session.call_tool("web_extract", {"urls": [f"{site}/article"]})
        assert "Lubricate widget bearings" in result.content[0].text


def test_mcp_tool_parameters_are_described():
    import asyncio

    from agentsearcher.mcp_server import server

    for tool in asyncio.run(server.list_tools()):
        props = tool.input_schema["properties"]
        assert all(p.get("description") for p in props.values()), tool.name


def test_tavily_python_crawl_and_map_contract(server, site):
    from tavily import TavilyClient

    tavily = TavilyClient(api_key="tvly-local", api_base_url=server)
    response = tavily.map(f"{site}/docs/", max_depth=2, allow_external=False)
    assert response["base_url"] == f"{site}/docs/"
    assert f"{site}/docs/a/deep" in response["results"]

    response = tavily.crawl(
        f"{site}/docs/", instructions="state hooks", select_paths=["/docs/a.*"], limit=5
    )
    assert response["base_url"] == f"{site}/docs/"
    assert {r["url"] for r in response["results"]} == {f"{site}/docs/", f"{site}/docs/a"}
    assert all(r["raw_content"] for r in response["results"])


@pytest.mark.live
@pytest.mark.parametrize("depth", ["ultra-fast", "fast", "basic", "advanced"])
def test_tavily_python_search_contract(server, depth):
    from tavily import TavilyClient

    tavily = TavilyClient(api_key="tvly-local", api_base_url=server)
    response = tavily.search("python asyncio tutorial", search_depth=depth, max_results=3)
    assert response["query"] == "python asyncio tutorial"
    assert 1 <= len(response["results"]) <= 3
    for r in response["results"]:
        assert set(r) >= {"url", "title", "content", "score", "raw_content"}
        assert 0 <= r["score"] <= 1
