"""Outbound fetching with an SSRF guard (app/safe_http.py): public http(s) only, checked and
pinned on every redirect hop.

The guard is the same file as in 07-page-extractor (app/safe_http.py); only the download part
differs: a media file streamed to disk with a size cap and a content-type check, instead of an
HTML page held in memory.
"""
from pathlib import Path
from urllib.parse import urlsplit

import httpx

# Tests patch safe_http.resolve (net has no resolve of its own, so a stale patch fails loudly).
from app.safe_http import BlockedURL, FetchError, GuardedClient, _is_public, check_url  # noqa: F401

USER_AGENT = "marketing-agent/1.0 (+clip-finder)"
TIMEOUT = 30.0
MAX_REDIRECTS = 5
CHUNK = 1024 * 1024

# Direct media files only. application/octet-stream (what many file hosts send) is accepted
# when the URL path ends in one of the media extensions.
MEDIA_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus")
MEDIA_TYPES = ("video/", "audio/")
GENERIC_TYPES = ("application/octet-stream", "binary/octet-stream", "application/x-download")


class NotMedia(FetchError):
    """The URL answered, but not with an audio or video file."""


class TooLarge(FetchError):
    """The file is larger than the cap."""


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
        with GuardedClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
            for _ in range(MAX_REDIRECTS + 1):
                # GuardedClient checks this hop's URL, then connects only to the checked address.
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
