import pytest

from agentsearcher.fetch import Fetched, is_block_page, is_challenge, needs_browser, url_error

ARTICLE = "Real article text about widgets and their many uses. " * 40


@pytest.mark.parametrize(
    ("url", "error"),
    [
        ("https://[::1", "invalid url: Invalid IPv6 URL"),
        ("http://a:99999/", "invalid url: Port out of range"),
        ("http://a:x/", "invalid url: Port could not be cast"),
        ("ftp://a.com/f", "blocked: unsupported url"),
        ("https:///path", "blocked: unsupported url"),
        ("https://example.com/a?b=1", None),
    ],
)
def test_url_error(url, error):
    assert (url_error(url) or "").startswith(error or "")
    assert (url_error(url) is None) == (error is None)


def test_challenge_detection():
    html = "<title>Just a moment...</title>"
    assert is_challenge(503, {}, html)
    assert is_challenge(200, {"cf-mitigated": "challenge"}, "")
    assert not is_challenge(200, {}, html)
    assert not is_challenge(404, {}, "<title>Not found</title>")


def test_block_page_detection():
    assert is_block_page("Reddit", "# Prove your humanity\n\nWe're committed to safety.")
    assert not is_block_page("Nginx guide", "Access denied errors explained. " + ARTICLE)


def test_challenge_needs_browser():
    assert needs_browser(Fetched("u", 503, "", challenge=True), ARTICLE, None, False) == "challenge"


def test_little_text_needs_browser():
    assert needs_browser(Fetched("u", 200, "<p>hi</p>"), "hi", None, False) == "spa: little text"


def test_empty_root_needs_browser():
    html = '<div id="root"></div><p>' + "x" * 500 + "</p>"
    assert needs_browser(Fetched("u", 200, html), "x" * 500, None, False) == "spa: empty root"


def test_snippet_mismatch_needs_browser():
    snippet = "Quantum widgets dramatically improve teleportation throughput benchmarks"
    reason = needs_browser(Fetched("u", 200, "<p>a</p>"), ARTICLE, snippet, False)
    assert reason.startswith("snippet mismatch")


def test_normal_page_stays_static():
    snippet = "Real article text about widgets and their uses"
    assert needs_browser(Fetched("u", 200, f"<p>{ARTICLE}</p>"), ARTICLE, snippet, False) is None
