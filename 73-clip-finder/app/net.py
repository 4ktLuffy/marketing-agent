"""Outbound fetching with an SSRF guard: public http(s) only, re-checked on every redirect.

The guard (resolve, _is_public, check_url) is copied from 07-page-extractor/app/net.py; only
the download part differs: a media file streamed to disk with a size cap and a content-type
check, instead of an HTML page held in memory.
"""
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

import httpx

USER_AGENT = "marketing-agent/1.0 (+clip-finder)"
TIMEOUT = 30.0
MAX_REDIRECTS = 5
CHUNK = 1024 * 1024

# Direct media files only. application/octet-stream (what many file hosts send) is accepted
# when the URL path ends in one of the media extensions.
MEDIA_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus")
MEDIA_TYPES = ("video/", "audio/")
GENERIC_TYPES = ("application/octet-stream", "binary/octet-stream", "application/x-download")


class BlockedURL(ValueError):
    """The URL is not allowed: wrong scheme, or it resolves to a non-public address."""


class FetchError(Exception):
    """The upstream could not be fetched."""


class NotMedia(FetchError):
    """The URL answered, but not with an audio or video file."""


class TooLarge(FetchError):
    """The file is larger than the cap."""


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


def has_media_extension(path: str) -> bool:
    return path.lower().rstrip("/").endswith(MEDIA_EXTENSIONS)


def acceptable_media(content_type: str, url: str) -> bool:
    ctype = content_type.split(";")[0].strip().lower()
    if ctype.startswith(MEDIA_TYPES):
        return True
    return (not ctype or ctype in GENERIC_TYPES) and has_media_extension(urlsplit(url).path)


def download(url: str, dest: Path, max_bytes: int, timeout: float = TIMEOUT, progress=None) -> dict:
    """Stream a media file to dest, following up to MAX_REDIRECTS redirects and guarding each hop.

    Returns {"url": final url, "content_type", "bytes"}. Raises BlockedURL, NotMedia, TooLarge
    or FetchError; a partial file is removed.
    """
    mb = max_bytes / (1024 * 1024)
    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, trust_env=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                check_url(url)
                with client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        url = str(resp.url.join(resp.headers["location"]))
                        continue
                    if resp.status_code >= 400:
                        raise FetchError(f"upstream returned HTTP {resp.status_code}")
                    ctype = resp.headers.get("content-type", "")
                    if not acceptable_media(ctype, str(resp.url)):
                        raise NotMedia(f"not an audio or video file ({ctype.split(';')[0] or 'no content-type'})")
                    length = int(resp.headers.get("content-length") or 0)
                    if length > max_bytes:
                        raise TooLarge(f"file larger than {mb:g} MB")
                    got = 0
                    try:
                        with open(dest, "wb") as f:
                            for chunk in resp.iter_bytes(CHUNK):
                                got += len(chunk)
                                if got > max_bytes:
                                    raise TooLarge(f"file larger than {mb:g} MB")
                                f.write(chunk)
                                if progress and length:
                                    progress(got / length)
                    except BaseException:
                        dest.unlink(missing_ok=True)
                        raise
                    return {"url": str(resp.url), "content_type": ctype.split(";")[0].strip(), "bytes": got}
    except httpx.HTTPError as exc:
        raise FetchError(f"{type(exc).__name__}: {str(exc) or 'request failed'}") from exc
    raise FetchError(f"more than {MAX_REDIRECTS} redirects")
