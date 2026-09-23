"""Page extractor: clean title, headings, main text, links and Open Graph from a URL or HTML."""
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, model_validator

from app.net import BlockedURL, FetchError, fetch

app = FastAPI(title="page-extractor")

MAX_TEXT = 20_000
# Chrome, not content. Removed before the main text is taken.
DROP_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "template", "svg"]


class ExtractRequest(BaseModel):
    url: str | None = None
    html: str | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.url is None) == (self.html is None):
            raise ValueError("give exactly one of 'url' or 'html'")
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


def extract(soup: BeautifulSoup, url: str | None) -> dict:
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
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/extract")
def extract_endpoint(req: ExtractRequest):
    if req.html is not None:
        return extract(BeautifulSoup(req.html, "html.parser"), None)
    try:
        page = fetch(req.url.strip())
    except BlockedURL as exc:
        raise HTTPException(422, str(exc))
    except FetchError as exc:
        raise HTTPException(502, f"could not fetch {req.url}: {exc}")
    soup = BeautifulSoup(page.content, "html.parser", from_encoding=page.encoding)
    return extract(soup, page.url)
