"""Page extractor: clean title, headings, main text, links and Open Graph from a URL or HTML;
the text of a PDF page by page (uploaded as base64, or a URL that serves application/pdf)."""
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, model_validator

from app import youtube
from app.net import BlockedURL, FetchError, fetch, is_pdf
from app.pdf import PDFError, decode_base64, extract_pdf

app = FastAPI(title="page-extractor")

MAX_TEXT = 20_000
# Chrome, not content. Removed before the main text is taken.
DROP_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "template", "svg"]


class ExtractRequest(BaseModel):
    url: str | None = None
    html: str | None = None
    # a PDF file as base64 (optionally a data: URL); `filename` is only echoed back
    pdf_base64: str | None = None
    filename: str | None = None
    # true: also return `link_list`, the internal links as [{"url", "text"}] (first 200, deduped)
    list_links: bool = False

    @model_validator(mode="after")
    def exactly_one(self):
        if sum(x is not None for x in (self.url, self.html, self.pdf_base64)) != 1:
            raise ValueError("give exactly one of 'url', 'html' or 'pdf_base64'")
        return self


def _host(netloc: str) -> str:
    return netloc.lower().removeprefix("www.")


def count_links(soup: BeautifulSoup, base_url: str | None) -> dict:
    internal = external = 0
    base_host = _host(urlsplit(base_url).netloc) if base_url else ""
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        parts = urlsplit(urljoin(base_url or "", href))
        if parts.scheme and parts.scheme not in ("http", "https"):
            continue
        # Without a base URL, relative links are internal and absolute ones external.
        if not parts.netloc or _host(parts.netloc) == base_host:
            internal += 1
        else:
            external += 1
    return {"internal": internal, "external": external}


MAX_LINKS = 200


def internal_links(soup: BeautifulSoup, base_url: str | None) -> list[dict]:
    """Absolute http(s) URLs on the page's own host (ignoring www.), without #fragments."""
    out, seen = [], set()
    base_host = _host(urlsplit(base_url).netloc) if base_url else ""
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url or "", href).split("#", 1)[0]
        parts = urlsplit(full)
        if parts.scheme not in ("http", "https") or _host(parts.netloc) != base_host or full in seen:
            continue
        seen.add(full)
        out.append({"url": full, "text": " ".join(a.get_text(" ").split())[:120]})
        if len(out) >= MAX_LINKS:
            break
    return out


def extract(soup: BeautifulSoup, url: str | None, list_links: bool = False) -> dict:
    title = soup.title.get_text(strip=True) if soup.title else None
    desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    og = {}
    for meta in soup.find_all("meta", attrs={"property": re.compile(r"^og:", re.I)}):
        if meta.get("content") is not None:
            og.setdefault(meta["property"][3:].lower(), meta["content"].strip())
    headings = [
        {"level": int(h.name[1]), "text": " ".join(h.get_text(" ").split())}
        for h in soup.find_all(["h1", "h2", "h3"])
    ]
    headings = [h for h in headings if h["text"]]
    links = count_links(soup, url)
    link_list = internal_links(soup, url) if list_links and url else None

    root = soup.find("article") or soup.find("main") or soup.body or soup
    for tag in root.find_all(DROP_TAGS):
        tag.decompose()
    text = " ".join(root.get_text(" ").split())

    return {
        "url": url,
        "title": title or None,
        "description": ((desc.get("content") or "").strip() or None) if desc else None,
        "lang": (soup.html.get("lang") or None) if soup.html else None,
        "headings": headings,
        "text": text[:MAX_TEXT],
        "word_count": len(text.split()),
        "links": links,
        "og": og,
        **({"link_list": link_list} if link_list is not None else {}),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


def pdf_result(raw: bytes, url: str | None, filename: str | None) -> dict:
    """A PDF in the page shape (so old callers still find title/text) plus `pages` with numbers.
    `text` is capped like a page's; `pages` carries every page (each capped at 20,000 characters)."""
    try:
        doc = extract_pdf(raw, filename)
    except PDFError as exc:
        raise HTTPException(exc.status, str(exc))
    return {"url": url, "title": doc["title"], "description": None, "lang": None, "headings": [],
            "text": doc["text"][:MAX_TEXT], "word_count": doc["word_count"], "links": {"internal": 0, "external": 0},
            "og": {}, "source": "pdf", "filename": doc["filename"], "page_count": doc["page_count"],
            "pages": doc["pages"]}


@app.post("/extract")
def extract_endpoint(req: ExtractRequest):
    if req.html is not None:
        return extract(BeautifulSoup(req.html, "html.parser"), None)
    if req.pdf_base64 is not None:
        try:
            raw = decode_base64(req.pdf_base64)
        except PDFError as exc:
            raise HTTPException(exc.status, str(exc))
        return pdf_result(raw, None, req.filename)
    try:
        vid = youtube.video_id(req.url)
    except youtube.BadVideoURL as exc:
        raise HTTPException(422, str(exc))
    except youtube.NotYouTube:
        vid = None
    if vid:
        try:
            return youtube.extract_video(vid, MAX_TEXT)
        except youtube.TranscriptUnavailable as exc:
            raise HTTPException(422, str(exc))
        except youtube.TranscriptFetchError as exc:
            raise HTTPException(502, str(exc))
    try:
        page = fetch(req.url.strip(), allow_pdf=True)
    except BlockedURL as exc:
        raise HTTPException(422, str(exc))
    except FetchError as exc:
        raise HTTPException(502, f"could not fetch {req.url}: {exc}")
    if is_pdf(page):
        name = page.url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0] or None
        return pdf_result(page.content, page.url, req.filename or name)
    soup = BeautifulSoup(page.content, "html.parser", from_encoding=page.encoding)
    return extract(soup, page.url, req.list_links)
