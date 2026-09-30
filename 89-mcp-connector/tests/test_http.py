"""Streamable HTTP on /mcp: token rules, rate limit, size cap, redacted log."""
import json
import logging

import pytest
from starlette.testclient import TestClient

from app import config
from app.server import TOOL_NAMES
from app.web import build_app

from .conftest import TOKEN, cfg

ENV = {"INTERNAL_API_KEY": "k" * 20, "BRAND_URL": "http://brand.test", "TASK_BRIDGE_URL": "http://tasks.test"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def remote(**kw):
    return cfg(host="0.0.0.0", token=TOKEN, **kw)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "my.server.example"])
def test_token_required_for_non_local_bind(host):
    with pytest.raises(config.ConfigError, match="without MCP_TOKEN"):
        config.load({**ENV, "MCP_HOST": host}, "http")
    assert config.load({**ENV, "MCP_HOST": host, "MCP_TOKEN": TOKEN}, "http").token == TOKEN


def test_local_bind_may_run_without_token_and_short_token_refused():
    assert config.load({**ENV, "MCP_HOST": "127.0.0.1"}, "http").token is None
    with pytest.raises(config.ConfigError, match="too short"):
        config.load({**ENV, "MCP_TOKEN": "short"}, "http")
    with pytest.raises(config.ConfigError, match="needs MCP_TOKEN"):
        config.load({**ENV, "MCP_TOKEN_IN_PATH": "true"}, "http")


def rpc(client, body, headers=None, path="/mcp"):
    r = client.post(path, content=json.dumps(body), headers={**MCP_HEADERS, **(headers or {})})
    return r


def test_health_needs_no_token():
    with TestClient(build_app(remote())) as c:
        r = c.get("/health")
    assert r.status_code == 200 and r.json()["auth"] == "bearer"


def test_bearer_required():
    with TestClient(build_app(remote())) as c:
        assert rpc(c, INIT).status_code == 401
        assert rpc(c, INIT, {"authorization": "Bearer wrong"}).status_code == 401
        assert rpc(c, INIT, {"authorization": TOKEN}).status_code == 401
        assert rpc(c, INIT, path=f"/mcp/{TOKEN}").status_code in (401, 404)  # path token off by default
        r = rpc(c, INIT, {"authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200, r.text
        assert r.json()["result"]["serverInfo"]["name"] == "marketing-facts"
        r = rpc(c, LIST, {"authorization": f"Bearer {TOKEN}", "mcp-protocol-version": "2025-06-18"})
        assert r.status_code == 200, r.text
        assert sorted(t["name"] for t in r.json()["result"]["tools"]) == sorted(TOOL_NAMES)


def test_token_in_path_when_enabled(caplog):
    caplog.set_level(logging.INFO)
    with TestClient(build_app(remote(token_in_path=True))) as c:
        assert rpc(c, INIT, path=f"/mcp/{TOKEN}").status_code == 200
        assert rpc(c, INIT, path="/mcp/" + "y" * 44).status_code == 401
        assert rpc(c, INIT, {"authorization": f"Bearer {TOKEN}"}).status_code == 200
    # server-side records only (httpx2 here is the test client logging its own request)
    server_log = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    assert TOKEN not in server_log and "/mcp/***" in server_log


def test_rate_limit():
    with TestClient(build_app(remote(http_requests_per_min=2))) as c:
        h = {"authorization": f"Bearer {TOKEN}"}
        assert rpc(c, INIT, h).status_code == 200
        assert rpc(c, INIT, {"authorization": "Bearer bad"}).status_code == 401  # failed tries count too
        r = rpc(c, INIT, h)
        assert r.status_code == 429 and r.headers["retry-after"] == "60"


def test_body_size_cap():
    with TestClient(build_app(remote(max_body_bytes=20_000))) as c:
        big = {**INIT, "params": {**INIT["params"], "pad": "x" * 30_000}}
        assert rpc(c, big, {"authorization": f"Bearer {TOKEN}"}).status_code == 413


def test_localhost_bind_rejects_foreign_host_header():
    local = cfg(host="127.0.0.1")
    with TestClient(build_app(local), base_url="http://evil.example") as c:
        assert rpc(c, INIT).status_code == 421
    with TestClient(build_app(local), base_url="http://127.0.0.1:8000") as c:
        assert rpc(c, INIT).status_code == 200


def test_allowed_hosts_for_a_tunnel():
    c_ = cfg(host="127.0.0.1", token=TOKEN, allowed_hosts=("abc.ngrok-free.app",))
    with TestClient(build_app(c_), base_url="https://abc.ngrok-free.app") as c:
        assert rpc(c, INIT, {"authorization": f"Bearer {TOKEN}"}).status_code == 200
    with TestClient(build_app(c_), base_url="https://other.example") as c:
        assert rpc(c, INIT, {"authorization": f"Bearer {TOKEN}"}).status_code == 421


def test_stdio_process_lists_the_tools():
    """The real `python -m app stdio` process, driven by the SDK's stdio client."""
    import os
    import pathlib
    import sys

    import anyio
    from mcp import Client, StdioServerParameters

    root = pathlib.Path(__file__).resolve().parent.parent
    params = StdioServerParameters(command=sys.executable, args=["-m", "app", "stdio"], cwd=str(root),
                                   env={"PATH": os.environ.get("PATH", ""), **ENV})

    async def go():
        async with Client(params) as c:
            return [t.name for t in (await c.list_tools()).tools]
    assert sorted(anyio.run(go)) == sorted(TOOL_NAMES)


def test_oauth_discovery_is_a_plain_404():
    with TestClient(build_app(remote())) as c:
        for p in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server"):
            assert c.get(p).status_code == 404
        assert c.get("/anything-else").status_code == 401
