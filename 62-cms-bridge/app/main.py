"""CMS bridge: turns the publisher's (39) webhook call for a blog item into a CMS post.

CMS facts this file relies on (WordPress and Ghost docs, September 2026):
- WordPress REST API: `POST {WP_URL}/wp-json/wp/v2/posts` {title, content, excerpt, status,
  date_gmt?, featured_media?} -> {id, link, status}. Auth: Application Password as HTTP Basic.
  Media: `POST /wp-json/wp/v2/media` with the raw file as body and a Content-Disposition
  filename -> {id, source_url}. Errors: {code, message, data: {status}}.
- Ghost Admin API: `POST {GHOST_URL}/ghost/api/admin/posts/?source=html`
  {posts: [{title, html, status, custom_excerpt, feature_image?, published_at?}]} ->
  {posts: [{id, url, status}]}. Auth: `Authorization: Ghost <jwt>`, the JWT signed HS256 with
  the hex-decoded secret of the Admin API key `id:secret`, header kid = id, aud `/admin/`,
  at most 5 minutes valid. Images: `POST /ghost/api/admin/images/upload/` multipart `file`
  -> {images: [{url}]}. Errors: {errors: [{message, context, type}]}.
See README "CMS API reference used" for the exact URLs.
"""
import base64
import hmac
import html
import logging
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from markdown_it import MarkdownIt
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="cms-bridge")
log = logging.getLogger("cms-bridge")

TIMEOUT_S = 30.0
IMAGE_TIMEOUT_S = 10.0
IMAGE_MAX_BYTES = 5 * 1024 * 1024
EXCERPT_MAX = 300  # Ghost custom_excerpt limit; also a sane WordPress excerpt
GHOST_ACCEPT_VERSION = "v5.0"
CHANNEL = "blog"

WP_STATUSES = {"draft", "pending", "private", "publish", "future"}
GHOST_STATUSES = {"draft", "published", "scheduled"}
DEFAULT_IMAGE_HOSTS = "image-cards,localhost"

# html=False: raw HTML in the model's markdown is escaped, never passed to the CMS as markup.
_md = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")

# id+channel -> result of a created post, so a retry by the publisher (e.g. when it could not
# mark the item published) does not create a second draft. In memory only: lost on restart.
_published: dict[str, dict] = {}


# ---------- config


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def dry_run() -> bool:
    """Anything but an explicit false value keeps DRY_RUN on (safe default)."""
    return env("DRY_RUN", "true").lower() not in ("false", "0", "no", "off")


def cms() -> str:
    return env("CMS", "wordpress").lower()


def api_key() -> str:
    """This service's own key; falls back to the stack-wide INTERNAL_API_KEY."""
    return env("CMS_BRIDGE_KEY") or env("INTERNAL_API_KEY")


def wp_base() -> str:
    return env("WP_URL").rstrip("/")


def ghost_base() -> str:
    return env("GHOST_URL").rstrip("/")


def wp_status() -> str:
    return env("WP_STATUS", "draft").lower()


def ghost_status() -> str:
    return env("GHOST_STATUS", "draft").lower()


def ghost_key() -> tuple[str, bytes] | None:
    """GHOST_ADMIN_KEY `id:secret` -> (id, secret bytes), or None when unusable."""
    raw = env("GHOST_ADMIN_KEY")
    kid, sep, secret = raw.partition(":")
    if not sep or not kid or not secret:
        return None
    try:
        return kid, bytes.fromhex(secret)
    except ValueError:
        return None


def allowed_image_hosts() -> set[str]:
    raw = os.environ.get("ALLOWED_IMAGE_HOSTS")
    raw = DEFAULT_IMAGE_HOSTS if raw is None else raw
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def config_errors() -> list[str]:
    """Problems that stop live publishing. Never contains a secret value."""
    errors = []
    if not api_key():
        errors.append("CMS_BRIDGE_KEY / INTERNAL_API_KEY is not set: /publish answers 503")
    kind = cms()
    if kind == "wordpress":
        for name in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
            if not env(name):
                errors.append(f"{name} is not set: only DRY_RUN publishing works")
        if wp_status() not in WP_STATUSES:
            errors.append(f"WP_STATUS must be one of {sorted(WP_STATUSES)}")
    elif kind == "ghost":
        if not ghost_base():
            errors.append("GHOST_URL is not set: only DRY_RUN publishing works")
        if not env("GHOST_ADMIN_KEY"):
            errors.append("GHOST_ADMIN_KEY is not set: only DRY_RUN publishing works")
        elif ghost_key() is None:
            errors.append("GHOST_ADMIN_KEY must be '<id>:<hex secret>' (Ghost Admin → "
                          "Integrations → Admin API key)")
        if ghost_status() not in GHOST_STATUSES:
            errors.append(f"GHOST_STATUS must be one of {sorted(GHOST_STATUSES)}")
    else:
        errors.append(f"CMS must be 'wordpress' or 'ghost', not '{kind}'")
    return errors


def status_errors() -> list[str]:
    """The subset of config errors that also make a dry run meaningless."""
    return [e for e in config_errors() if e.startswith(("CMS must", "WP_STATUS", "GHOST_STATUS"))]


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = api_key()
    if not expected:
        raise HTTPException(503, "CMS_BRIDGE_KEY (or INTERNAL_API_KEY) is not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- secret hygiene


def secrets() -> list[str]:
    out = [env("WP_APP_PASSWORD"), env("GHOST_ADMIN_KEY"), env("CMS_BRIDGE_KEY"),
           env("INTERNAL_API_KEY")]
    pw = env("WP_APP_PASSWORD")
    if pw:
        out.append(pw.replace(" ", ""))  # WordPress accepts it with or without spaces
        out.append(base64.b64encode(f"{env('WP_USER')}:{pw}".encode()).decode())
    _, _, ghost_secret = env("GHOST_ADMIN_KEY").partition(":")
    out.append(ghost_secret)
    return sorted({s for s in out if len(s) >= 4}, key=len, reverse=True)


def scrub(value):
    """Remove every secret from anything that could reach a response or a log line."""
    if isinstance(value, str):
        for s in secrets():
            value = value.replace(s, "***")
        return value
    if isinstance(value, dict):
        return {scrub(k): scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"detail": scrub(exc.detail)}, status_code=exc.status_code,
                        headers=exc.headers)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    # Never echo the exception text: it could carry request details.
    log.error("unexpected error on %s: %s", request.url.path, type(exc).__name__)
    return JSONResponse({"detail": "internal error"}, status_code=500)


# ---------- content


def strip_leading_h1(text: str) -> str:
    """Drop a first-line `# Title`: the CMS shows the title itself, so it would appear twice."""
    lines = text.split("\n")
    if lines and re.match(r"^#\s+\S", lines[0]):
        return "\n".join(lines[1:]).lstrip("\n")
    return text


def build_markdown(text: str, link: str | None) -> str:
    text = strip_leading_h1((text or "").replace("\r\n", "\n").strip())
    link = (link or "").strip()
    if link and link not in text and re.match(r"^https?://\S+$", link):
        text = f"{text}\n\n<{link}>" if text else f"<{link}>"
    return text


def to_html(markdown_text: str) -> str:
    return _md.render(markdown_text)


def excerpt_of(body_html: str) -> str:
    """Plain text of the first paragraph, cut at EXCERPT_MAX characters on a word boundary."""
    m = re.search(r"<p>(.*?)</p>", body_html, re.S)
    if not m:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > EXCERPT_MAX:
        text = text[: EXCERPT_MAX - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def parse_when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"scheduled_at '{value}' is not an ISO 8601 date-time") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # the calendar stores UTC
    return dt.astimezone(timezone.utc)


# ---------- CMS client


class CMSError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def cms_message(r: httpx.Response) -> str:
    """WordPress: {code, message}; Ghost: {errors: [{message, context}]}; else text."""
    try:
        body = r.json()
    except ValueError:
        return (r.text or "").strip()[:300] or f"HTTP {r.status_code}"
    if isinstance(body, dict):
        if isinstance(body.get("errors"), list) and body["errors"]:
            first = body["errors"][0] if isinstance(body["errors"][0], dict) else {}
            msg = first.get("message") or str(first)
            if first.get("context"):
                msg = f"{msg} ({first['context']})"
            return str(msg)[:300]
        msg = body.get("message") or body.get("error") or body
        if body.get("code") and isinstance(msg, str):
            msg = f"{body['code']}: {msg}"
        return str(msg)[:300]
    return str(body)[:300]


def cms_name() -> str:
    return "WordPress" if cms() == "wordpress" else "Ghost"


def send(method: str, url: str, headers: dict, **kw) -> dict:
    try:
        with httpx.Client(timeout=TIMEOUT_S, follow_redirects=False) as client:
            r = client.request(method, url, headers=headers, **kw)
    except httpx.TimeoutException:
        raise CMSError(None, f"{cms_name()} did not answer within {TIMEOUT_S:.0f} s") from None
    except httpx.HTTPError as e:
        host = urlsplit(url).netloc
        raise CMSError(None, f"cannot reach {cms_name()} at {host}: {type(e).__name__}") from None
    if r.status_code in (401, 403):
        raise CMSError(r.status_code, f"{cms_name()} rejected the credentials: {cms_message(r)}")
    if r.status_code >= 300:
        # 3xx too: WordPress without pretty permalinks or http->https redirects would turn
        # the POST into a GET and "succeed" without creating anything.
        raise CMSError(r.status_code, cms_message(r))
    try:
        data = r.json()
    except ValueError:
        raise CMSError(r.status_code, f"{cms_name()} answered with non-JSON") from None
    if not isinstance(data, dict):
        raise CMSError(r.status_code, f"{cms_name()} answered with an unexpected shape")
    return data


def bad_gateway(e: CMSError) -> HTTPException:
    return HTTPException(502, {"message": f"{cms_name()} error", "cms_status": e.status,
                               "cms_error": e.message})


def wp_headers() -> dict:
    token = base64.b64encode(f"{env('WP_USER')}:{env('WP_APP_PASSWORD')}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


def ghost_token(now: int | None = None) -> str:
    key = ghost_key()
    if key is None:
        raise CMSError(None, "GHOST_ADMIN_KEY is not a valid '<id>:<hex secret>' key")
    kid, secret = key
    iat = int(time.time()) if now is None else now
    return jwt.encode({"iat": iat, "exp": iat + 5 * 60, "aud": "/admin/"}, secret,
                      algorithm="HS256", headers={"kid": kid, "typ": "JWT"})


def ghost_headers() -> dict:
    return {"Authorization": f"Ghost {ghost_token()}", "Accept-Version": GHOST_ACCEPT_VERSION,
            "Accept": "application/json"}


# ---------- featured image


class ImageSkipped(Exception):
    pass


def check_image_url(url: str) -> str:
    """Strict host allowlist (no DNS tricks to reason about): http(s) and an allowed host."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ImageSkipped("image_url must be http or https")
    host = (parts.hostname or "").lower()
    if not host:
        raise ImageSkipped("image_url has no host")
    if parts.username or parts.password:
        raise ImageSkipped("image_url must not carry credentials")
    if host not in allowed_image_hosts():
        raise ImageSkipped(f"image host '{host}' is not in ALLOWED_IMAGE_HOSTS")
    return host


def fetch_image(url: str) -> tuple[bytes, str, str]:
    """Download image_url -> (bytes, content type, filename). Raises ImageSkipped."""
    check_image_url(url)
    try:
        # No redirects: a redirect could leave the allowlist.
        with httpx.Client(timeout=IMAGE_TIMEOUT_S, follow_redirects=False) as client:
            with client.stream("GET", url) as r:
                if r.status_code != 200:
                    raise ImageSkipped(f"image_url answered HTTP {r.status_code}")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if not ctype.startswith("image/"):
                    raise ImageSkipped(f"image_url is not an image (content-type '{ctype or 'none'}')")
                declared = r.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > IMAGE_MAX_BYTES:
                    raise ImageSkipped("image is larger than 5 MB")
                data = bytearray()
                for chunk in r.iter_bytes():
                    data.extend(chunk)
                    if len(data) > IMAGE_MAX_BYTES:
                        raise ImageSkipped("image is larger than 5 MB")
    except httpx.TimeoutException:
        raise ImageSkipped(f"image_url did not answer within {IMAGE_TIMEOUT_S:.0f} s") from None
    except httpx.HTTPError as e:
        raise ImageSkipped(f"cannot fetch image_url: {type(e).__name__}") from None
    if not data:
        raise ImageSkipped("image_url returned an empty body")
    ext = {"image/jpeg": "jpg", "image/svg+xml": "svg"}.get(ctype, ctype.split("/")[-1] or "img")
    name = os.path.basename(urlsplit(url).path) or "image"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80]
    if "." not in name:
        name = f"{name}.{ext}"
    return bytes(data), ctype, name


def upload_image(url: str) -> tuple[int | str | None, str | None]:
    """Fetch and upload. -> (WordPress media id or Ghost image URL, warning)."""
    try:
        data, ctype, name = fetch_image(url)
    except ImageSkipped as e:
        return None, f"featured image skipped: {e}"
    try:
        if cms() == "wordpress":
            media = send("POST", f"{wp_base()}/wp-json/wp/v2/media",
                         {**wp_headers(), "Content-Type": ctype,
                          "Content-Disposition": f'attachment; filename="{name}"'},
                         content=data)
            if not media.get("id"):
                return None, "featured image skipped: WordPress returned no media id"
            return media["id"], None
        up = send("POST", f"{ghost_base()}/ghost/api/admin/images/upload/", ghost_headers(),
                  files={"file": (name, data, ctype)}, data={"purpose": "image"})
        images = up.get("images") or [{}]
        if not isinstance(images[0], dict) or not images[0].get("url"):
            return None, "featured image skipped: Ghost returned no image url"
        return images[0]["url"], None
    except CMSError as e:
        if e.status in (401, 403):
            raise  # the post would fail the same way; report the real problem
        return None, f"featured image skipped: upload failed ({e.message})"


# ---------- request bodies


def wp_body(title: str, body_html: str, excerpt: str, when: datetime | None) -> dict:
    status = wp_status()
    body = {"title": title, "content": body_html, "excerpt": excerpt, "status": status}
    if status == "future":
        if when is None:
            raise HTTPException(422, "WP_STATUS=future needs scheduled_at in the payload")
        body["date_gmt"] = when.strftime("%Y-%m-%dT%H:%M:%S")
    return body


def ghost_body(title: str, body_html: str, excerpt: str, when: datetime | None) -> dict:
    status = ghost_status()
    post = {"title": title, "html": body_html, "status": status}
    if excerpt:
        post["custom_excerpt"] = excerpt
    if status == "scheduled":
        if when is None:
            raise HTTPException(422, "GHOST_STATUS=scheduled needs scheduled_at in the payload")
        post["published_at"] = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"posts": [post]}


def target_url() -> str:
    if cms() == "wordpress":
        return f"{wp_base() or '<WP_URL>'}/wp-json/wp/v2/posts"
    return f"{ghost_base() or '<GHOST_URL>'}/ghost/api/admin/posts/?source=html"


# ---------- endpoints


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int | str
    channel: str
    title: str | None = None
    # 39 sends `text` (body with the short link swapped in); `body` is accepted too.
    text: str | None = Field(default=None, validation_alias=AliasChoices("text", "body"))
    link: str | None = None
    short_url: str | None = None
    campaign: str | None = None
    scheduled_at: str | None = None
    image_url: str | None = None


@app.get("/health")
def health():
    errors = config_errors()
    return {
        "status": "ok" if not errors else "degraded",
        "cms": cms(),
        "dry_run": dry_run(),
        "configured": not errors,
        "cms_status": wp_status() if cms() == "wordpress" else ghost_status(),
        "allowed_image_hosts": sorted(allowed_image_hosts()),
        "config_errors": errors,
    }


@app.post("/publish", dependencies=[Depends(require_key)])
def publish(req: PublishRequest):
    channel = req.channel.strip().lower()
    if channel != CHANNEL:
        raise HTTPException(422, (
            f"channel '{channel}' is not handled here: cms-bridge only publishes channel "
            f"'{CHANNEL}' to the CMS. Route social channels to postiz-bridge (54)."))
    bad = status_errors()
    if bad:
        raise HTTPException(503, f"{bad[0]}. Fix it and restart; see GET /health.")
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(422, "title is empty: a blog post needs a title")
    markdown_text = build_markdown(req.text or "", req.link or req.short_url)
    if not markdown_text:
        raise HTTPException(422, "text is empty")
    when = parse_when(req.scheduled_at)
    body_html = to_html(markdown_text)
    excerpt = excerpt_of(body_html)
    kind = cms()
    build = wp_body if kind == "wordpress" else ghost_body
    image_url = (req.image_url or "").strip() or None

    if dry_run():
        # No network at all: not the CMS, not the image host.
        warnings = []
        if image_url:
            try:
                check_image_url(image_url)
            except ImageSkipped as e:
                warnings.append(f"featured image skipped: {e}")
        would_send = build(title, body_html, excerpt, when)
        return {
            "status": "dry_run", "cms": kind, "external_id": None, "external_url": None,
            "url": None, "cms_status": wp_status() if kind == "wordpress" else ghost_status(),
            "would_send": {"method": "POST", "url": target_url(), "body": would_send,
                           "featured_image": image_url if not warnings else None},
            "warnings": warnings,
        }

    errors = config_errors()
    if errors:
        raise HTTPException(503, f"{errors[0]}. Set it or keep DRY_RUN=true.")

    dedupe_key = f"{req.id}:{channel}"
    if dedupe_key in _published:
        return {**_published[dedupe_key], "status": "already_created"}

    body = build(title, body_html, excerpt, when)  # 422s before any upload
    warnings = []
    try:
        if image_url:
            media, warning = upload_image(image_url)
            if warning:
                warnings.append(warning)
                log.warning("item %s: %s", req.id, scrub(warning))
            elif kind == "wordpress":
                body["featured_media"] = media
            else:
                body["posts"][0]["feature_image"] = media
        if kind == "wordpress":
            created = send("POST", target_url(), wp_headers(), json=body)
            external_id, external_url, cms_state = created.get("id"), created.get("link"), created.get("status")
        else:
            created = send("POST", target_url(), ghost_headers(), json=body)
            post = (created.get("posts") or [{}])[0]
            post = post if isinstance(post, dict) else {}
            external_id, external_url, cms_state = post.get("id"), post.get("url"), post.get("status")
    except CMSError as e:
        raise bad_gateway(e)
    if not external_id:
        raise bad_gateway(CMSError(200, f"{cms_name()} accepted the request but returned no post id"))

    result = {
        "status": "created", "cms": kind, "external_id": external_id,
        # `url` is what the publisher (39) stores as the item's external_url.
        "external_url": external_url, "url": external_url, "cms_status": cms_state,
        "warnings": warnings,
    }
    _published[dedupe_key] = result
    log.info("item %s created in %s as %s (%s)", req.id, cms_name(), external_id, cms_state)
    return result
