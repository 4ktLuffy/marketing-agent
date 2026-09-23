"""SEO auditor: on-page checks for a URL or raw HTML, each pass / warn / fail, plus a score."""
import re

from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, model_validator

from app.net import BlockedURL, FetchError, fetch

app = FastAPI(title="seo-auditor")

DROP_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "template", "svg"]


class AuditRequest(BaseModel):
    url: str | None = None
    html: str | None = None
    keyword: str | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.url is None) == (self.html is None):
            raise ValueError("give exactly one of 'url' or 'html'")
        return self


def _meta(soup, **attrs):
    """Content of the first <meta> whose attributes match case-insensitively, or None."""
    pattern = {k: re.compile(rf"^{re.escape(v)}$", re.I) for k, v in attrs.items()}
    tag = soup.find("meta", attrs=pattern)
    return (tag.get("content") or "").strip() if tag else None


def _main_text(soup) -> str:
    root = soup.find("article") or soup.find("main") or soup.body or soup
    for tag in root.find_all(DROP_TAGS):
        tag.decompose()
    return " ".join(root.get_text(" ").split())


def _check(id_, status, message):
    return {"id": id_, "status": status, "message": message}


def _range_check(id_, label, value, lo, hi):
    if not value:
        return _check(id_, "fail", f"{label} is missing")
    n = len(value)
    if lo <= n <= hi:
        return _check(id_, "pass", f"{label} is {n} characters")
    return _check(id_, "warn", f"{label} is {n} characters; aim for {lo}-{hi}")


def audit(soup: BeautifulSoup, keyword: str | None, robots_header: str = "") -> list[dict]:
    checks = []
    title = soup.title.get_text(strip=True) if soup.title else ""
    description = _meta(soup, name="description") or ""
    checks.append(_range_check("title", "Title", title, 30, 60))
    checks.append(_range_check("meta_description", "Meta description", description, 70, 160))

    h1s = [" ".join(h.get_text(" ").split()) for h in soup.find_all("h1")]
    if len(h1s) == 1:
        checks.append(_check("h1", "pass", "exactly one h1"))
    elif not h1s:
        checks.append(_check("h1", "fail", "no h1"))
    else:
        checks.append(_check("h1", "fail", f"{len(h1s)} h1 tags; use exactly one"))

    levels = [int(h.name[1]) for h in soup.find_all(re.compile(r"^h[1-6]$"))]
    skips = [f"h{a}->h{b}" for a, b in zip(levels, levels[1:]) if b > a + 1]
    if skips:
        checks.append(_check("heading_order", "warn", f"skipped heading levels: {', '.join(skips)}"))
    else:
        checks.append(_check("heading_order", "pass", "no skipped heading levels"))

    imgs = soup.find_all("img")
    if not imgs:
        checks.append(_check("img_alt", "pass", "no images"))
    else:
        # alt="" is valid for decorative images, so only a missing attribute counts against.
        covered = sum(1 for img in imgs if img.has_attr("alt"))
        ratio = covered / len(imgs)
        status = "fail" if ratio < 0.5 else "warn" if ratio < 0.9 else "pass"
        checks.append(_check("img_alt", status, f"{covered}/{len(imgs)} images have alt text"))

    canonical = next(
        (link for link in soup.find_all("link", href=True)
         if "canonical" in [r.lower() for r in link.get("rel") or []]),
        None,
    )
    checks.append(
        _check("canonical", "pass", f"canonical: {canonical['href']}") if canonical
        else _check("canonical", "warn", "no canonical link")
    )

    missing_og = [p for p in ("og:title", "og:image") if not _meta(soup, property=p)]
    checks.append(
        _check("open_graph", "warn", f"missing {', '.join(missing_og)}") if missing_og
        else _check("open_graph", "pass", "og:title and og:image present")
    )

    lang = soup.html.get("lang") if soup.html else None
    checks.append(
        _check("lang", "pass", f"lang={lang}") if lang else _check("lang", "warn", "<html> has no lang")
    )

    viewport = _meta(soup, name="viewport")
    checks.append(
        _check("viewport", "pass", "viewport meta present") if viewport
        else _check("viewport", "fail", "no viewport meta; page is not mobile-friendly")
    )

    robots = f"{_meta(soup, name='robots') or ''} {robots_header}".lower()
    checks.append(
        _check("indexable", "fail", "robots noindex: page will not be indexed") if "noindex" in robots
        else _check("indexable", "pass", "no noindex")
    )

    text = _main_text(soup)
    words = text.split()
    checks.append(
        _check("word_count", "pass", f"{len(words)} words") if len(words) >= 300
        else _check("word_count", "warn", f"{len(words)} words; aim for 300+")
    )

    if keyword and keyword.strip():
        kw = keyword.strip().lower()
        places = {
            "keyword_in_title": ("title", title),
            "keyword_in_h1": ("h1", " ".join(h1s)),
            "keyword_in_intro": ("first 100 words", " ".join(words[:100])),
            "keyword_in_description": ("meta description", description),
        }
        for id_, (label, value) in places.items():
            if kw in value.lower():
                checks.append(_check(id_, "pass", f"'{keyword}' is in the {label}"))
            else:
                checks.append(_check(id_, "warn", f"'{keyword}' is not in the {label}"))
    return checks


def score(checks: list[dict]) -> int:
    passed = sum(c["status"] == "pass" for c in checks)
    warned = sum(c["status"] == "warn" for c in checks)
    return round(100 * (passed + 0.5 * warned) / len(checks))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/audit")
def audit_endpoint(req: AuditRequest):
    if req.html is not None:
        url, soup, robots_header = None, BeautifulSoup(req.html, "html.parser"), ""
    else:
        try:
            page = fetch(req.url.strip())
        except BlockedURL as exc:
            raise HTTPException(422, str(exc))
        except FetchError as exc:
            raise HTTPException(502, f"could not fetch {req.url}: {exc}")
        url = page.url
        soup = BeautifulSoup(page.content, "html.parser", from_encoding=page.encoding)
        robots_header = page.headers.get("x-robots-tag", "")
    checks = audit(soup, req.keyword, robots_header)
    return {"url": url, "score": score(checks), "checks": checks}
