import re
import threading
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlsplit

import lxml.html
import trafilatura

MD_SYNTAX = re.compile(r"^#{1,6}\s+|^\s*[-*+]\s+|^\s*>\s?|\*\*|__|`|^\|[\s|:-]*\|$", re.MULTILINE)
SKIP_IMAGE = re.compile(r"\.(svg|ico|gif)(\?|$)|^data:|logo|icon|sprite|pixel", re.IGNORECASE)
SKIP_LINK = re.compile(
    r"\.(jpe?g|png|gif|svg|webp|ico|zip|gz|bz2|xz|tar|rar|7z|iso|exe|dmg|apk|epub"
    r"|mp[34]|avi|mov|webm"
    r"|woff2?|ttf|css|js|xml|json|docx?|xlsx?|pptx?)(\?|$)",
    re.IGNORECASE,
)
MD_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
MD_TITLE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
MAX_PDF_PAGES = 100
# pdfium is not thread-safe and crashes the process on concurrent use
_pdfium_lock = threading.Lock()


@dataclass
class Page:
    url: str
    title: str
    markdown: str
    published_date: str | None = None
    images: list[str] = field(default_factory=list)
    favicon: str | None = None
    rendered: bool = False
    links: list[tuple[str, str]] = field(default_factory=list)


def default_favicon(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/favicon.ico"


def _favicon(tree, url: str) -> str:
    for href in tree.xpath("//link[contains(@rel, 'icon')]/@href"):
        return urljoin(url, href)
    return default_favicon(url)


def _images(tree, url: str, limit: int = 10) -> list[str]:
    seen: list[str] = []
    for src in tree.xpath("//img/@src"):
        src = urljoin(url, src.strip())
        if src.startswith("http") and not SKIP_IMAGE.search(src) and src not in seen:
            seen.append(src)
        if len(seen) == limit:
            break
    return seen


def _crawlable(href: str, base: str) -> str | None:
    """Absolute url without fragment, or None for anchors, scripts and binary files."""
    href = href.strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    absolute = urldefrag(urljoin(base, href)).url
    if absolute.startswith(("http://", "https://")) and not SKIP_LINK.search(absolute):
        return absolute
    return None


def _links(tree, url: str) -> list[tuple[str, str]]:
    """(absolute url, anchor text) of crawlable links, in document order."""
    links = []
    for a in tree.iter("a"):
        if absolute := _crawlable(a.get("href") or "", url):
            links.append((absolute, " ".join(a.text_content().split())))
    return links


def markdown_links(markdown: str, base: str) -> list[tuple[str, str]]:
    """(absolute url, link text) of crawlable [text](url) links, in document order."""
    links = []
    for text, href in MD_LINK.findall(markdown):
        if absolute := _crawlable(href, base):
            links.append((absolute, text.strip()))
    return links


def from_markdown(markdown: str, url: str) -> Page:
    title = next((m.group(1) for m in MD_TITLE.finditer(markdown)), "")
    return Page(
        url=url,
        title=title.strip(),
        markdown=markdown,
        favicon=default_favicon(url),
        links=markdown_links(markdown, url),
    )


def extract_pdf(body: bytes, url: str) -> Page:
    """Text layer of a PDF, one `## Page N` section per page; empty for scans and broken files.

    Title comes from the PDF metadata, else the file name.
    """
    import pypdfium2

    with _pdfium_lock:
        try:
            pdf = pypdfium2.PdfDocument(body)
        except pypdfium2.PdfiumError:
            return Page(url, "", "", favicon=default_favicon(url))
        try:
            title = pdf.get_metadata_value("Title").strip()
            sections = []
            for i in range(min(len(pdf), MAX_PDF_PAGES)):
                page = pdf[i]
                textpage = page.get_textpage()
                text = textpage.get_text_bounded().replace("\r\n", "\n").strip()
                textpage.close()
                page.close()
                if text:
                    sections.append(f"## Page {i + 1}\n\n{text}")
        finally:
            pdf.close()
    # the first text line is often a license or journal header, so fall back to the file name
    title = title or urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return Page(url, title, "\n\n".join(sections), favicon=default_favicon(url))


def _unwrap_code(tree):
    """Lift <pre> out of code-widget wrappers (copy buttons etc.) that trafilatura discards."""
    for pre in list(tree.iter("pre")):
        parent = pre.getparent()
        if (
            parent is not None
            and parent.tag == "div"
            and len(parent.text_content()) - len(pre.text_content()) < 40
        ):
            parent.addprevious(pre)
            parent.drop_tree()


def extract(html: str, url: str, *, recall: bool = False) -> Page:
    try:
        tree = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return Page(url, "", "")
    meta = trafilatura.extract_metadata(tree, default_url=url)
    title = (meta.title if meta and meta.title else "") or (tree.findtext(".//title") or "")
    images, favicon, links = _images(tree, url), _favicon(tree, url), _links(tree, url)
    _unwrap_code(tree)
    markdown = (
        trafilatura.extract(
            tree,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_comments=False,
            include_formatting=True,
            favor_recall=recall,
        )
        or ""
    )
    return Page(
        url=url,
        title=title.strip(),
        markdown=markdown,
        published_date=meta.date if meta else None,
        images=images,
        favicon=favicon,
        links=links,
    )


def to_text(markdown: str) -> str:
    return MD_SYNTAX.sub("", markdown)
