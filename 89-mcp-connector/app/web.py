"""Streamable HTTP on /mcp, behind a bearer token, a request rate limit and a body size cap.

`GET /health` needs no token. Everything else needs `Authorization: Bearer $MCP_TOKEN`, or, only
with MCP_TOKEN_IN_PATH=true, the token as the last path segment (`/mcp/<token>`) for clients that
cannot send a header. The access log never shows the token or any request body.
"""
import hmac
import json
import logging
import threading
import time
from collections import deque

from mcp.server.transport_security import TransportSecuritySettings

from .config import LOCAL_HOSTS, Config
from .server import build_server

log = logging.getLogger("mcp_connector.http")
LOCAL_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost", "[::1]"]
LOCAL_ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


async def _send_json(send, status: int, body: dict, headers: list | None = None):
    raw = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()),
                            (b"cache-control", b"no-store")] + (headers or [])})
    await send({"type": "http.response.body", "body": raw})


def transport_security(cfg: Config) -> TransportSecuritySettings | None:
    """Host/Origin checks against DNS rebinding. On by default for a localhost bind; with
    MCP_ALLOWED_HOSTS (e.g. the tunnel's hostname) those hosts are accepted too."""
    if cfg.allowed_hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=LOCAL_ALLOWED_HOSTS + list(cfg.allowed_hosts),
                                         allowed_origins=LOCAL_ALLOWED_ORIGINS + ["https://claude.ai"])
    if cfg.host in LOCAL_HOSTS:
        return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=LOCAL_ALLOWED_HOSTS,
                                         allowed_origins=LOCAL_ALLOWED_ORIGINS)
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)  # the token protects it


class Guard:
    """Pure ASGI wrapper: health, rate limit, token, redacted access log."""

    def __init__(self, app, cfg: Config):
        self.app, self.cfg = app, cfg
        self._hits: deque = deque()
        self._lock = threading.Lock()
        self._bearer = f"Bearer {cfg.token}".encode() if cfg.token else None

    def _limited(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > 60:
                self._hits.popleft()
            if len(self._hits) >= self.cfg.http_requests_per_min:
                return True
            self._hits.append(now)
            return False

    def _authorized(self, scope) -> tuple[bool, str]:
        """(ok, path to route). Constant-time comparisons only."""
        path = scope.get("path", "")
        if self._bearer is None:
            return True, path
        if self.cfg.token_in_path and path.startswith("/mcp/"):
            given = path[len("/mcp/"):].rstrip("/").encode()
            return hmac.compare_digest(given, self.cfg.token.encode()), "/mcp"
        auth = dict(scope.get("headers") or []).get(b"authorization", b"")
        return hmac.compare_digest(auth, self._bearer), path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method, path = scope.get("method"), scope.get("path", "")
        shown = "/mcp/***" if path.startswith("/mcp/") else path[:40]
        if path == "/health" and method == "GET":
            return await _send_json(send, 200, {"status": "ok", "service": "mcp-connector",
                                                "auth": "bearer" if self._bearer else "none (localhost only)"})
        if path.startswith("/.well-known/"):
            # No OAuth yet: say so plainly (404), so a client does not start an OAuth flow on a 401.
            return await _send_json(send, 404, {"error": "no OAuth metadata: this server uses a bearer token"})
        if self._limited():
            log.warning("%s %s 429", method, shown)
            return await _send_json(send, 429, {"error": "too many requests; try again in a minute"},
                                    [(b"retry-after", b"60")])
        ok, route = self._authorized(scope)
        if not ok:
            log.warning("%s %s 401", method, shown)
            return await _send_json(send, 401, {"error": "missing or wrong bearer token"},
                                    [(b"www-authenticate", b'Bearer realm="mcp"')])
        if route != path:
            scope = {**scope, "path": route, "raw_path": route.encode()}
        status = {}

        async def send_logged(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        await self.app(scope, receive, send_logged)
        log.info("%s %s %s", method, shown, status.get("code"))


def build_app(cfg: Config):
    server = build_server(cfg)
    app = server.streamable_http_app(streamable_http_path="/mcp", json_response=cfg.json_response,
                                     stateless_http=True, max_request_body_size=cfg.max_body_bytes,
                                     transport_security=transport_security(cfg), host=cfg.host)
    return Guard(app, cfg)
