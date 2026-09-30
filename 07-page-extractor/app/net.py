"""Outbound fetching with an SSRF guard (app/safe_http.py): public http(s) only, checked and
pinned on every redirect hop."""
from dataclasses import dataclass

import httpx

# The guard lives in safe_http.py (kept identical across the deploys that fetch outside URLs).
# Tests patch safe_http.resolve (net has no resolve of its own, so a stale patch fails loudly).
from app.safe_http import BlockedURL, FetchError, GuardedClient, _is_public, check_url  # noqa: F401

USER_AGENT = "marketing-agent/1.0 (+page-extractor)"
TIMEOUT = 15.0
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_PDF_BYTES = 10 * 1024 * 1024   # a PDF (allow_pdf=True) may be larger than a page
PDF_TYPES = ("application/pdf", "application/x-pdf")


@dataclass
class Page:
    url: str  # final URL, after redirects
    content: bytes
    encoding: str | None  # charset from the Content-Type header, if any
    headers: httpx.Headers


def _ctype(content_type: str) -> str:
    return content_type.split(";")[0].strip().lower()


def is_pdf(page: "Page") -> bool:
    return _ctype(page.headers.get("content-type", "")) in PDF_TYPES


def _acceptable(content_type: str, html_only: bool, allow_pdf: bool = False) -> bool:
    ctype = _ctype(content_type)
    if allow_pdf and ctype in PDF_TYPES:
        return True
    return not html_only or not ctype or "html" in ctype or "xml" in ctype or ctype.startswith("text/")


def fetch(url: str, html_only: bool = True, allow_pdf: bool = False) -> Page:
    """GET a URL, following up to MAX_REDIRECTS redirects and guarding each hop.

    allow_pdf=True also accepts application/pdf (up to MAX_PDF_BYTES instead of MAX_BYTES)."""
    try:
        with GuardedClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            for _ in range(MAX_REDIRECTS + 1):
                # GuardedClient checks this hop's URL, then connects only to the checked address.
                with client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        url = str(resp.url.join(resp.headers["location"]))
                        continue
                    if resp.status_code >= 400:
                        raise FetchError(f"upstream returned HTTP {resp.status_code}")
                    ctype = resp.headers.get("content-type", "")
                    if not _acceptable(ctype, html_only, allow_pdf):
                        raise FetchError(f"not an HTML page ({resp.headers['content-type']})")
                    pdf = allow_pdf and _ctype(ctype) in PDF_TYPES
                    limit, words = (MAX_PDF_BYTES, "10 MB") if pdf else (MAX_BYTES, "5 MB")
                    if int(resp.headers.get("content-length") or 0) > limit:
                        raise FetchError(f"response larger than {words}")
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > limit:
                            raise FetchError(f"response larger than {words}")
                    return Page(str(resp.url), bytes(body), resp.charset_encoding, resp.headers)
    except httpx.HTTPError as exc:
        raise FetchError(f"{type(exc).__name__}: {str(exc) or 'request failed'}") from exc
    raise FetchError(f"more than {MAX_REDIRECTS} redirects")
