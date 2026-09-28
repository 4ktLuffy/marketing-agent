"""Outbound fetching with an SSRF guard (app/safe_http.py): public http(s) only, checked and
pinned on every redirect hop."""
from dataclasses import dataclass

import httpx

# The guard lives in safe_http.py (kept identical across the deploys that fetch outside URLs).
# Tests patch safe_http.resolve (net has no resolve of its own, so a stale patch fails loudly).
from app.safe_http import BlockedURL, FetchError, GuardedClient, _is_public, check_url  # noqa: F401

USER_AGENT = "marketing-agent/1.0 (+change-monitor)"
TIMEOUT = 15.0
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5


@dataclass
class Page:
    url: str  # final URL, after redirects
    content: bytes
    encoding: str | None  # charset from the Content-Type header, if any
    headers: httpx.Headers


def _acceptable(content_type: str, html_only: bool) -> bool:
    ctype = content_type.split(";")[0].strip().lower()
    return not html_only or not ctype or "html" in ctype or "xml" in ctype or ctype.startswith("text/")


def fetch(url: str, html_only: bool = True) -> Page:
    """GET a URL, following up to MAX_REDIRECTS redirects and guarding each hop."""
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
                    if not _acceptable(resp.headers.get("content-type", ""), html_only):
                        raise FetchError(f"not an HTML page ({resp.headers['content-type']})")
                    if int(resp.headers.get("content-length") or 0) > MAX_BYTES:
                        raise FetchError("response larger than 5 MB")
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise FetchError("response larger than 5 MB")
                    return Page(str(resp.url), bytes(body), resp.charset_encoding, resp.headers)
    except httpx.HTTPError as exc:
        raise FetchError(f"{type(exc).__name__}: {str(exc) or 'request failed'}") from exc
    raise FetchError(f"more than {MAX_REDIRECTS} redirects")
