"""The only HTTP calls this connector makes, to the brand service (05) and the task bridge (88).

Every call goes through `call()`, which checks the method and path against ALLOWED before anything
is sent. Nothing here can approve, publish, confirm, retire, export or reconcile: those paths are
not in the list, and the connector has no key that would make them work anyway.
"""
import re

import httpx

from .config import Config

TASK_ID = r"T-[A-Z2-7]{6}"
# (service, method, path pattern). Anything else is refused before a request is made.
ALLOWED = (
    ("brand", "GET", r"/facts/query"),
    ("brand", "GET", r"/facts/v2"),
    ("tasks", "POST", r"/tasks"),
    ("tasks", "GET", rf"/tasks/{TASK_ID}"),
    ("tasks", "POST", rf"/tasks/{TASK_ID}/paste"),
    ("tasks", "POST", rf"/tasks/{TASK_ID}/drafts/\d{{1,9}}/split"),
    ("tasks", "POST", rf"/tasks/{TASK_ID}/submit"),
    ("tasks", "POST", r"/check"),
    ("tasks", "GET", r"/blockers"),
    ("tasks", "GET", r"/occasions"),
    ("tasks", "POST", r"/templates/render"),
    ("tasks", "POST", r"/templates/task"),
    ("tasks", "POST", r"/quote"),
    ("tasks", "POST", r"/audit"),
)
NAMES = {"brand": "the brand service (05)", "tasks": "the task bridge (88)"}


class UpstreamError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, detail=None):
        super().__init__(message)
        self.status = status
        self.detail = detail


def allowed(service: str, method: str, path: str) -> bool:
    return any(s == service and m == method and re.fullmatch(p, path) for s, m, p in ALLOWED)


def _detail(r: httpx.Response):
    try:
        body = r.json()
    except ValueError:
        return (r.text or "")[:300]
    return body.get("detail", body) if isinstance(body, dict) else body


async def call(cfg: Config, service: str, method: str, path: str, *, params=None, json=None):
    if not allowed(service, method, path):
        raise UpstreamError(f"not allowed from this connector: {method} {path}")
    base = cfg.brand_url if service == "brand" else cfg.task_bridge_url
    name = NAMES[service]
    try:
        async with httpx.AsyncClient(timeout=cfg.upstream_timeout, follow_redirects=False) as client:
            r = await client.request(method, base + path, params=params, json=json,
                                     headers={"X-API-Key": cfg.internal_api_key})
    except httpx.TimeoutException:
        raise UpstreamError(f"{name} did not answer in time; try again in a minute") from None
    except httpx.HTTPError:
        raise UpstreamError(f"{name} is not reachable; the person running the marketing agent must start it") from None
    if r.status_code in (401, 403):
        raise UpstreamError(f"{name} refused this connector's key; the person running it must check INTERNAL_API_KEY",
                            r.status_code)
    if r.status_code >= 400:
        raise UpstreamError(f"{name} answered {r.status_code}", r.status_code, _detail(r))
    try:
        return r.json()
    except ValueError:
        raise UpstreamError(f"{name} did not return JSON") from None
