"""Postiz bridge: turns the publisher's (39) webhook call into a Postiz public-API post.

Postiz facts this file relies on (read from the Postiz source and docs, September 2026):
- Public API lives under `{backend}/public/v1`: cloud `https://api.postiz.com/public/v1`,
  self-hosted `{NEXT_PUBLIC_BACKEND_URL}/public/v1` (official Docker image: `.../api/public/v1`).
- Auth: `Authorization: <api key>` (the raw key, no "Bearer").
- `GET /integrations` -> `[{id, name, identifier, picture, disabled, profile, customer?}]`.
- `POST /posts` body `{type, date, shortLink, tags, posts:[{integration:{id}, value:[{content,
  image:[]}], settings:{__type, ...}}]}` -> `[{postId, integration}]`. No public URL yet:
  the provider publishes asynchronously and `releaseURL` is filled in later.
- Only `POST /posts` is rate limited (API_LIMIT per hour, default 90; cloud 100) -> 429.
See README "Postiz API reference used" for the exact URLs.
"""
import hmac
import html
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="postiz-bridge")
log = logging.getLogger("postiz-bridge")

DEFAULT_POSTIZ_URL = "https://api.postiz.com"
TIMEOUT_S = 30.0
INTEGRATIONS_TTL_S = 300

# Providers whose Postiz validation (checkValidity) rejects a post without media.
# This bridge sends text only, so these would always fail inside Postiz.
MEDIA_REQUIRED = {
    "instagram": "Instagram posts need at least one image or video",
    "instagram-standalone": "Instagram posts need at least one image or video",
    "tiktok": "TikTok posts need a video or images",
    "tiktok-business": "TikTok posts need a video or images",
    "youtube": "YouTube posts need exactly one video",
    "pinterest": "Pinterest pins need at least one image",
    "dribbble": "Dribbble shots need one image",
}

# Settings Postiz requires per provider (docs "Create Post" table + provider DTOs) that the
# bridge cannot guess. Supply them in CHANNEL_MAP as {"id": ..., "settings": {...}}.
REQUIRED_SETTINGS = {
    "reddit": ["subreddit"],
    "lemmy": ["subreddit"],
    "discord": ["channel"],
    "slack": ["channel"],
    "medium": ["title", "subtitle"],
    "devto": ["title"],
    "hashnode": ["title", "tags"],
    "wordpress": ["title", "type"],
    "listmonk": ["subject", "preview", "list"],
}

# Safe defaults for required settings that have an obvious value.
DEFAULT_SETTINGS = {
    "x": {"who_can_reply_post": "everyone"},
}

KNOWN_PROVIDERS = (
    set(MEDIA_REQUIRED) | set(REQUIRED_SETTINGS) | set(DEFAULT_SETTINGS)
    | {"linkedin", "linkedin-page", "facebook", "threads", "bluesky", "mastodon",
       "telegram", "nostr", "vk", "gmb", "wrapcast"}
)

_cache_lock = threading.Lock()
_integrations_cache: dict = {"at": 0.0, "url": None, "items": None}
# id+channel -> result of an accepted post, so a retry by the publisher (e.g. when it could
# not mark the item published) does not post twice. In memory only: lost on restart.
_published: dict[str, dict] = {}


# ---------- config


def dry_run() -> bool:
    """Anything but an explicit false value keeps DRY_RUN on (safe default)."""
    return os.environ.get("DRY_RUN", "true").strip().lower() not in ("false", "0", "no", "off")


def postiz_base() -> str:
    url = (os.environ.get("POSTIZ_URL") or DEFAULT_POSTIZ_URL).strip().rstrip("/")
    if url.endswith("/public/v1"):
        url = url[: -len("/public/v1")]
    return url + "/public/v1"


def postiz_key() -> str:
    return (os.environ.get("POSTIZ_API_KEY") or "").strip()


def load_channel_map() -> tuple[dict[str, dict], str | None]:
    """Parse CHANNEL_MAP. Returns (map, error). Values: "<id>" or {"id","settings"?,"provider"?}."""
    raw = os.environ.get("CHANNEL_MAP", "").strip() or "{}"
    try:
        data = json.loads(raw)
    except ValueError as e:
        return {}, f"CHANNEL_MAP is not valid JSON ({e.msg} at position {e.pos})"
    if not isinstance(data, dict):
        return {}, "CHANNEL_MAP must be a JSON object like {\"linkedin\": \"<integration id>\"}"
    out: dict[str, dict] = {}
    for channel, value in data.items():
        key = str(channel).strip().lower()
        if isinstance(value, str) and value.strip():
            out[key] = {"id": value.strip(), "settings": {}, "provider": None}
        elif isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"].strip():
            settings = value.get("settings") or {}
            if not isinstance(settings, dict):
                return {}, f"CHANNEL_MAP[{channel!r}].settings must be an object"
            provider = value.get("provider")
            out[key] = {"id": value["id"].strip(), "settings": settings,
                        "provider": str(provider).strip().lower() if provider else None}
        else:
            return {}, (f"CHANNEL_MAP[{channel!r}] must be an integration id string or "
                        "{\"id\": \"...\", \"settings\": {...}}")
    return out, None


def config_errors() -> list[str]:
    errors = []
    _, map_error = load_channel_map()
    if map_error:
        errors.append(map_error)
    if not os.environ.get("INTERNAL_API_KEY"):
        errors.append("INTERNAL_API_KEY is not set: /publish answers 503")
    if not postiz_key():
        errors.append("POSTIZ_API_KEY is not set: only DRY_RUN publishing works")
    return errors


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- secret hygiene


def scrub(value):
    """Remove the Postiz key from anything that could reach a response or a log line."""
    key = postiz_key()
    if not key:
        return value
    if isinstance(value, str):
        return value.replace(key, "***")
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


# ---------- Postiz client


class PostizError(Exception):
    def __init__(self, status: int | None, message: str, retry_after: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def http_client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT_S,
        headers={"Authorization": postiz_key(), "Accept": "application/json"},
    )


def postiz_message(r: httpx.Response) -> str:
    """Postiz errors are {"msg"} (public API), {"message"} (Nest/validation) or text."""
    try:
        body = r.json()
    except ValueError:
        return (r.text or "").strip()[:300] or f"HTTP {r.status_code}"
    if isinstance(body, dict):
        msg = body.get("msg") or body.get("message") or body.get("error") or body
        if isinstance(msg, list):
            msg = "; ".join(str(m) for m in msg)
        if body.get("provider") and isinstance(msg, str):
            msg = f"{body.get('name') or body['provider']}: {msg}"
        return str(msg)[:300]
    return str(body)[:300]


def retry_after_of(r: httpx.Response) -> str | None:
    for name, value in r.headers.items():
        if name.lower().startswith("retry-after"):
            return value
    return None


def call_postiz(method: str, path: str, body: dict | None = None):
    if not postiz_key():
        raise PostizError(None, "POSTIZ_API_KEY is not set")
    url = postiz_base() + path
    try:
        with http_client() as client:
            r = client.request(method, url, json=body)
    except httpx.TimeoutException:
        raise PostizError(None, f"Postiz did not answer within {TIMEOUT_S:.0f} s") from None
    except httpx.HTTPError as e:
        raise PostizError(None, f"cannot reach Postiz at {postiz_base()}: {type(e).__name__}") from None
    if r.status_code == 429:
        raise PostizError(429, "Postiz rate limit reached (POST /posts is limited per hour)",
                          retry_after_of(r))
    if r.status_code == 401:
        raise PostizError(401, f"Postiz rejected POSTIZ_API_KEY: {postiz_message(r)}")
    if r.status_code >= 400:
        raise PostizError(r.status_code, postiz_message(r))
    try:
        return r.json()
    except ValueError:
        raise PostizError(r.status_code, "Postiz answered with non-JSON") from None


def list_integrations(fresh: bool = False) -> list[dict]:
    with _cache_lock:
        cached = _integrations_cache
        if (not fresh and cached["items"] is not None and cached["url"] == postiz_base()
                and time.monotonic() - cached["at"] < INTEGRATIONS_TTL_S):
            return cached["items"]
    data = call_postiz("GET", "/integrations")
    if not isinstance(data, list):
        raise PostizError(200, "unexpected /integrations response (not a list)")
    items = [
        {
            "id": i.get("id"),
            "name": i.get("name"),
            "provider": i.get("identifier"),
            "disabled": bool(i.get("disabled")),
            "profile": i.get("profile"),
        }
        for i in data if isinstance(i, dict)
    ]
    with _cache_lock:
        _integrations_cache.update(at=time.monotonic(), url=postiz_base(), items=items)
    return items


def bad_gateway(e: PostizError):
    detail = {"message": "Postiz error", "postiz_status": e.status, "postiz_error": e.message}
    headers = None
    if e.status == 429:
        detail["message"] = "Postiz rate limit reached; retry later"
        detail["retry_after"] = e.retry_after
        if e.retry_after:
            headers = {"Retry-After": e.retry_after}
    return HTTPException(502, detail, headers=headers)


# ---------- content


def to_postiz_html(text: str) -> str:
    """Plain text -> the <p> HTML the Postiz editor stores.

    Postiz sanitizes content as HTML and, for providers, turns each <p> into a new line
    and unescapes &amp; etc. (stripHtmlValidation). Plain text with '&' would otherwise
    reach the network as '&amp;'.
    """
    return "".join(f"<p>{html.escape(line, quote=False)}</p>" for line in text.split("\n"))


def build_content(text: str, link: str | None) -> str:
    text = (text or "").replace("\r\n", "\n").strip()
    link = (link or "").strip()
    if link and link not in text:
        text = f"{text}\n\n{link}" if text else link
    return text


def build_settings(provider: str | None, extra: dict) -> dict:
    settings = dict(DEFAULT_SETTINGS.get(provider or "", {}))
    settings.update(extra)
    if provider:
        settings["__type"] = provider  # Postiz overwrites __type from the integration anyway
    return settings


def check_provider(channel: str, provider: str | None, settings: dict):
    if not provider:
        return
    if provider in MEDIA_REQUIRED:
        raise HTTPException(422, (
            f"channel '{channel}' is a Postiz '{provider}' integration: "
            f"{MEDIA_REQUIRED[provider]}. This bridge publishes text only, so post it by hand "
            f"in Postiz or map the channel to a text network."))
    missing = [s for s in REQUIRED_SETTINGS.get(provider, []) if s not in settings]
    if missing:
        raise HTTPException(422, (
            f"channel '{channel}' is a Postiz '{provider}' integration, which needs settings "
            f"{missing}. Add them in CHANNEL_MAP as "
            f"{{\"{channel}\": {{\"id\": \"...\", \"settings\": {{...}}}}}}."))


def post_body(integration_id: str, content: str, settings: dict) -> dict:
    return {
        "type": "now",
        # Postiz requires an ISO date even for "now" (it then uses the current time).
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "shortLink": False,  # links are already shortened by 16 (link-shortener)
        "tags": [],
        "posts": [{
            "integration": {"id": integration_id},
            "value": [{"content": to_postiz_html(content), "image": []}],
            "settings": settings,
        }],
    }


# ---------- endpoints


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")  # 39 also sends short_url

    id: int | str
    channel: str
    title: str | None = None
    text: str
    link: str | None = None
    campaign: str | None = None


@app.get("/health")
def health():
    channel_map, _ = load_channel_map()
    errors = config_errors()
    return {
        "status": "ok" if not errors else "degraded",
        "dry_run": dry_run(),
        "postiz_url": postiz_base(),
        "postiz_key_set": bool(postiz_key()),
        "channels": sorted(channel_map),
        "config_errors": errors,
    }


@app.get("/integrations")
def integrations():
    try:
        items = list_integrations(fresh=True)
    except PostizError as e:
        raise bad_gateway(e)
    channel_map, _ = load_channel_map()
    by_id: dict[str, list[str]] = {}
    for channel, entry in channel_map.items():
        by_id.setdefault(entry["id"], []).append(channel)
    return [{**i, "channels": sorted(by_id.get(i["id"], []))} for i in items]


@app.post("/publish", dependencies=[Depends(require_key)])
def publish(req: PublishRequest):
    channel_map, map_error = load_channel_map()
    if map_error:
        raise HTTPException(503, f"{map_error}. Fix CHANNEL_MAP and restart; see GET /health.")
    channel = req.channel.strip().lower()
    entry = channel_map.get(channel)
    if not entry:
        raise HTTPException(422, (
            f"channel '{channel}' is not in CHANNEL_MAP (mapped: {sorted(channel_map) or 'none'}). "
            "Add it with its Postiz integration id from GET /integrations."))
    content = build_content(req.text, req.link)
    if not content:
        raise HTTPException(422, "text is empty")

    if dry_run():
        # No call to Postiz at all. The provider is only known from a hint here.
        provider = entry["provider"] or (channel if channel in KNOWN_PROVIDERS else None)
        settings = build_settings(provider, entry["settings"])
        check_provider(channel, provider, settings)
        return {
            "status": "dry_run", "url": None, "postiz_id": None,
            "channel": channel, "integration": entry["id"],
            "would_send": post_body(entry["id"], content, settings),
        }

    if not postiz_key():
        raise HTTPException(503, "POSTIZ_API_KEY is not set; set it or keep DRY_RUN=true")

    dedupe_key = f"{req.id}:{channel}"
    if dedupe_key in _published:
        return {**_published[dedupe_key], "status": "already_published"}

    try:
        found = next((i for i in list_integrations() if i["id"] == entry["id"]), None)
        if found is None:  # maybe connected after we cached the list
            found = next((i for i in list_integrations(fresh=True) if i["id"] == entry["id"]), None)
    except PostizError as e:
        raise bad_gateway(e)
    if found is None:
        raise HTTPException(503, (
            f"CHANNEL_MAP maps '{channel}' to integration '{entry['id']}', which Postiz does "
            "not have. Check GET /integrations."))
    if found["disabled"]:
        raise HTTPException(503, f"Postiz integration '{found['name']}' for '{channel}' is disabled")

    provider = found["provider"]
    settings = build_settings(provider, entry["settings"])
    check_provider(channel, provider, settings)

    try:
        created = call_postiz("POST", "/posts", post_body(entry["id"], content, settings))
    except PostizError as e:
        raise bad_gateway(e)
    first = created[0] if isinstance(created, list) and created and isinstance(created[0], dict) else {}
    postiz_id = first.get("postId")
    if not postiz_id:
        raise bad_gateway(PostizError(200, "Postiz accepted the request but returned no postId"))

    result = {
        # Postiz publishes asynchronously; the public URL (releaseURL) exists only later.
        "url": None,
        "postiz_id": postiz_id,
        "status": "queued",
        "channel": channel,
        "provider": provider,
    }
    _published[dedupe_key] = result
    log.info("item %s queued in Postiz as %s (%s)", req.id, postiz_id, provider)
    return result
