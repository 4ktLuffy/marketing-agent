"""Outbound fetching with an SSRF guard: public http(s) only, re-checked on every redirect."""
import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

USER_AGENT = "marketing-agent/1.0 (+page-extractor)"
TIMEOUT = 15.0
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5


class BlockedURL(ValueError):
    """The URL is not allowed: wrong scheme, or it resolves to a non-public address."""


class FetchError(Exception):
    """The upstream could not be fetched."""


@dataclass
class Page:
    url: str  # final URL, after redirects
    content: bytes
    encoding: str | None  # charset from the Content-Type header, if any
    headers: httpx.Headers


def resolve(host: str) -> list[str]:
    """Every address the hostname resolves to. Tests patch this."""
    return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


def _is_public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%")[0])  # drop an IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # is_global is False for loopback, private, link-local, reserved, unspecified, CGNAT.
    # IPv6 forms that embed an IPv4 address (NAT64, 6to4, IPv4-compatible/mapped) are judged
    # by that IPv4 address: is_global alone can call them global (security audit, low).
    if ip.version == 6:
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip in ipaddress.ip_network("64:ff9b::/96"):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is None and ip in ipaddress.ip_network("::/96"):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return embedded.is_global and not embedded.is_multicast
    return ip.is_global and not ip.is_multicast


def check_url(url: str) -> None:
    """Raise BlockedURL unless the URL is http(s) and every address of its host is public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedURL("only http and https URLs are allowed")
    host = parts.hostname
    if not host:
        raise BlockedURL("URL has no host")
    if os.getenv("ALLOW_PRIVATE_URLS", "").lower() == "true":
        return
    try:
        addrs = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addrs = resolve(host)
        except (OSError, UnicodeError) as exc:
            raise FetchError(f"cannot resolve host {host!r}") from exc
    for addr in addrs:
        if not _is_public(addr):
            raise BlockedURL(
                f"{host} resolves to non-public address {addr}; "
                "set ALLOW_PRIVATE_URLS=true to allow it"
            )


def _acceptable(content_type: str, html_only: bool) -> bool:
    ctype = content_type.split(";")[0].strip().lower()
    return not html_only or not ctype or "html" in ctype or "xml" in ctype or ctype.startswith("text/")


def fetch(url: str, html_only: bool = True) -> Page:
    """GET a URL, following up to MAX_REDIRECTS redirects and guarding each hop."""
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, trust_env=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                check_url(url)
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
