import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ARTICLE = (
    "<html><head><title>Static widgets</title>"
    '<meta property="article:published_time" content="2025-05-01">'
    '<link rel="icon" href="/icon.png"></head><body><nav>Home About</nav><article>'
    "<h1>Static widgets</h1>"
    + "".join(
        f"<p>Widgets are small mechanical parts used in machine number {i}. They are cheap.</p>"
        for i in range(12)
    )
    + "<h2>Maintenance</h2>"
    + "".join(
        f"<p>Lubricate widget bearings every six months to avoid wear in unit {i}.</p>"
        for i in range(6)
    )
    + '<img src="/photo.jpg"></article></body></html>'
)
DYNAMIC = "Quantum gizmos accelerate teleportation pipelines dramatically. "
SPA = (
    '<html><head><title>SPA</title></head><body><div id="root"></div><script>'
    "setTimeout(() => { document.getElementById('root').innerHTML ="
    f" '<article><h1>Rendered gizmos</h1><p>' + '{DYNAMIC}'.repeat(30) + '</p></article>'"
    "}, 300);</script></body></html>"
)
HALF_SSR = (
    "<html><head><title>Half</title></head><body><header><p>"
    + "Welcome to the gizmo portal, the home of all gizmo news and community resources. " * 6
    + '</p></header><main id="content"></main><script>'
    "setTimeout(() => { document.getElementById('content').innerHTML ="
    f" '<article><p>' + '{DYNAMIC}'.repeat(30) + '</p></article>'"
    "}, 300);</script></body></html>"
)
CHALLENGE = (
    "<html><head><title>Just a moment...</title></head><body>Checking your browser<script>"
    "setTimeout(() => { document.title = 'Passed';"
    f" document.body.innerHTML = '<article><h1>Passed</h1><p>' + '{DYNAMIC}'.repeat(30)"
    " + '</p></article>'; }, 800);</script></body></html>"
)


def doc(topic: str, links: list[tuple[str, str]]) -> str:
    """A static page with enough unique text to never need the browser, plus links."""
    paragraphs = "".join(
        f"<p>{topic} explained in detail, part {i} of the guide.</p>" for i in range(12)
    )
    anchors = "".join(f'<a href="{href}">{text}</a>' for href, text in links)
    return (
        f"<html><head><title>{topic}</title></head><body><nav>{anchors}</nav>"
        f"<article><h1>{topic}</h1>{paragraphs}</article></body></html>"
    )


def make_pdf(pages: list[list[str]]) -> str:
    """A minimal valid PDF with one Helvetica text line per string; [] makes a page without text."""
    count = len(pages)
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids ["
        + " ".join(f"{4 + 2 * i} 0 R" for i in range(count))
        + f"] /Count {count} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, lines in enumerate(pages):
        stream = "BT /F1 12 Tf 14 TL 72 720 Td " + " ".join(f"({t}) Tj T*" for t in lines) + " ET"
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    out, offsets = "%PDF-1.4\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets)
    return out + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"


PDF_ROUTES = {
    "/files/manual.pdf": (
        200,
        "application/pdf",
        make_pdf(
            [
                ["Widget manual", "Install the widget on a flat surface."],
                ["Calibrate the flux capacitor of the widget yearly."],
            ]
        ),
    ),
    "/files/scan.pdf": (200, "application/pdf", make_pdf([[]])),
}


def reprint(outlet: str) -> str:
    """The same news story as published by different outlets: own header and footer."""
    story = "".join(
        f"<p>The city council approved the new tram line, stage {i}, after a long debate.</p>"
        for i in range(10)
    )
    return (
        f"<html><head><title>{outlet}: tram line approved</title></head><body>"
        f"<header><p>{outlet} daily news, subscribe today</p></header><article>"
        f"<h1>Tram line approved</h1><p>Reported by {outlet} staff.</p>{story}"
        f"<p>Follow {outlet} for more local stories.</p></article></body></html>"
    )


SPA_NAV = (
    '<html><head><title>SPA nav</title></head><body><div id="root"></div><script>'
    "setTimeout(() => { document.getElementById('root').innerHTML ="
    f" '<article><p>' + '{DYNAMIC}'.repeat(30) + '</p><a href=\"/docs/b\">Styling</a></article>'"
    "}, 300);</script></body></html>"
)
SITE = {
    "/docs/": doc(
        "Documentation home",
        [
            ("/docs/a", "State hooks guide"),
            ("/docs/b", "Styling components"),
            ("/blog/x", "Company blog"),
            ("https://external.invalid/page", "External site"),
            ("/docs/#intro", "Intro"),
            ("/docs/a", "State hooks again"),
            ("/files/manual.pdf", "Manual PDF"),
        ],
    ),
    "/docs/a": doc("State hooks guide", [("/docs/a/deep", "Deep dive"), ("/docs/", "Home")]),
    "/docs/b": doc("Styling components", [("/docs/b/deep", "Advanced styling")]),
    "/docs/a/deep": doc("Deep dive into state hooks and useState", []),
    "/docs/b/deep": doc("Advanced styling with CSS modules", []),
    "/blog/x": doc("Company blog post about hiring", []),
}

ROUTES = {
    "/article": (200, "text/html; charset=utf-8", ARTICLE),
    "/spa": (200, "text/html", SPA),
    "/spa-nav": (200, "text/html", SPA_NAV),
    "/sparse": (
        200,
        "text/html",
        "<html><body><h1>Index</h1><p>Just a short list.</p></body></html>",
    ),
    "/half": (200, "text/html", HALF_SSR),
    "/challenge": (503, "text/html", CHALLENGE),
    "/pdf": (200, "application/pdf", "%PDF-1.4"),
    "/image": (200, "image/png", "png"),
    "/to-private": (302, "text/html", "http://10.255.255.1/secret"),
    "/fetch-private": (
        200,
        "text/html",
        '<html><body><div id="root"></div><script>'
        "fetch('http://10.255.255.1/secret').then(r => r.text(), e => 'request failed: ' + e)"
        ".then(t => { document.getElementById('root').innerHTML = '<article><p>' + t + ' '"
        f" + '{DYNAMIC}'.repeat(30) + '</p></article>' }})</script></body></html>",
    ),
    "/news/a": (200, "text/html", reprint("Morning Herald")),
    "/news/b": (200, "text/html", reprint("Evening Post")),
    **{path: (200, "text/html", body) for path, body in SITE.items()},
    **PDF_ROUTES,
}


MARKDOWN_A = (
    "# State hooks (markdown version)\n\n"
    + "Clean markdown about useState and other state hooks, served for LLMs. " * 5
    + "\n\nSee the [deep dive](/docs/a/deep).\n"
)
SOFT_404 = "<html><body><h1>Page not found</h1><p>Try the search.</p></body></html>"
# ORIGIN is replaced with the server's own http://host:port when served
RICH_ROUTES = {
    **{path: (200, "text/html", body) for path, body in SITE.items()},
    **PDF_ROUTES,
    "/docs/caching": (200, "text/html", doc("Orphan page about caching", [])),
    "/docs/private/p": (200, "text/html", doc("Private internal page", [])),
    "/other/z": (200, "text/html", doc("Unrelated section", [])),
    "/docs/a.md": (200, "text/markdown; charset=utf-8", MARKDOWN_A),
    "/robots.txt": (
        200,
        "text/plain",
        "User-agent: *\nDisallow: /docs/private\nCrawl-delay: 1\n"
        "Sitemap: ORIGIN/sitemap_index.xml\n",
    ),
    "/sitemap_index.xml": (
        200,
        "application/xml",
        '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>ORIGIN/sitemap-docs.xml.gz</loc></sitemap></sitemapindex>",
    ),
    "/sitemap-docs.xml.gz": (
        200,
        "application/gzip",
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(
            f"<url><loc>ORIGIN{p}</loc><lastmod>2026-01-01</lastmod></url>"
            for p in ["/docs/", "/docs/a", "/docs/caching", "/docs/private/p", "/other/z"]
        )
        + "</urlset>",
    ),
    "/llms.txt": (
        200,
        "text/plain",
        "# Rich docs\n\n> Docs for tests.\n\n- [State hooks](/docs/a): hooks\n"
        "- [State hooks, markdown](/docs/a.md): same page\n"
        "- [Styling guide](/docs/b): css\n",
    ),
    "/moved/": (301, "text/html", "/docs/"),
}
BROKEN_ROUTES = {
    **{path: (200, "text/html", body) for path, body in SITE.items()},
    **PDF_ROUTES,
    "/robots.txt": (200, "text/html", SOFT_404),
    "/llms.txt": (200, "text/html", SOFT_404),
    "/sitemap.xml": (200, "application/xml", "<<<this is not xml"),
    "/sitemap_index.xml": (200, "application/xml", "<rss><channel></channel></rss>"),
}


class Handler(BaseHTTPRequestHandler):
    routes = ROUTES
    hits: list[str] = []

    def do_GET(self):
        self.hits.append(self.path)
        if self.path == "/slow":
            time.sleep(5)
        status, ctype, body = self.routes.get(self.path, (404, "text/html", "<h1>Not found</h1>"))
        data = body.replace("ORIGIN", f"http://{self.headers['Host']}").encode()
        if self.path.endswith(".gz"):
            data = gzip.compress(data)
        self.send_response(status)
        if status in (301, 302):
            self.send_header("Location", body)
            data = b""
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        """Minimal OpenAI-compatible /v1/chat/completions that echoes the question back."""
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        question = body["messages"][-1]["content"].rsplit("Question: ", 1)[1]
        data = json.dumps({"choices": [{"message": {"content": f"LLM says: {question}"}}]})
        self.send_response(200 if self.path == "/v1/chat/completions" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data.encode())

    def log_message(self, *args):
        pass


class RichHandler(Handler):
    routes = RICH_ROUTES
    hits: list[str] = []


class BrokenHandler(Handler):
    routes = BROKEN_ROUTES
    hits: list[str] = []


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="session")
def site():
    server = _serve(Handler)
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="session")
def rich_site():
    """Same pages as `site`, plus robots.txt, a gzipped sitemap index, llms.txt and a .md page."""
    server = _serve(RichHandler)
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="session")
def broken_site():
    """Same pages as `site`, with soft-404 robots/llms.txt and invalid sitemaps."""
    server = _serve(BrokenHandler)
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="session", autouse=True)
def _allow_fixture_sites():
    """Fixture sites live on 127.0.0.1, which the SSRF guard blocks by default."""
    from agentsearcher import config

    config.ALLOW_PRIVATE_URLS = True
    # tests load the same urls again and expect real fetches; cache tests turn it back on
    config.CACHE_TTL = 0


@pytest.fixture(scope="session", autouse=True)
async def _close_browser():
    yield
    from agentsearcher import pipeline

    await pipeline.shutdown()
