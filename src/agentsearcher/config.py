import os


def _env(name: str, default: str) -> str:
    return os.environ.get(f"AGENTSEARCHER_{name}", default)


HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "8484"))
API_KEYS = {k.strip() for k in _env("API_KEYS", "").split(",") if k.strip()}
SEARXNG_URL = _env("SEARXNG_URL", "").rstrip("/")
SEARCH_BACKENDS = [
    b.strip()
    for b in _env("SEARCH_BACKENDS", "duckduckgo,yahoo,yandex,brave,mojeek,bing").split(",")
]
PROXY_URL = _env("PROXY_URL", "") or None
RESPECT_ROBOTS = _env("RESPECT_ROBOTS", "1") != "0"
MCP_TAVILY_NAMES = _env("MCP_TAVILY_NAMES", "0") == "1"
ALLOW_PRIVATE_URLS = _env("ALLOW_PRIVATE_URLS", "0") == "1"
LOG_LEVEL = _env("LOG_LEVEL", "INFO")

IDLE_TTL = float(_env("IDLE_TTL", "120"))
MAX_RAW_CHARS = int(_env("MAX_RAW_CHARS", "20000"))
MAX_RESPONSE_CHARS = int(_env("MAX_RESPONSE_CHARS", "60000"))
CHUNK_CHARS = int(_env("CHUNK_CHARS", "600"))
MAX_HTML_BYTES = int(_env("MAX_HTML_BYTES", str(3 * 1024 * 1024)))
MAX_PDF_BYTES = int(_env("MAX_PDF_BYTES", str(10 * 1024 * 1024)))

RERANK_THREADS = int(_env("RERANK_THREADS", "2"))

LLM_BASE_URL = _env("LLM_BASE_URL", "").rstrip("/")
LLM_MODEL = _env("LLM_MODEL", "local")
LLM_API_KEY = _env("LLM_API_KEY", "")
