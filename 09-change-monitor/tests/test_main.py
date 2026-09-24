import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import net
from app.main import app

client = TestClient(app)
KEY = {"X-API-Key": "test-key"}

V1 = """<html><head><title>Pricing</title><script>var t = 1;</script></head><body>
<nav>Home Pricing</nav><h1>Pricing</h1><p>Starter   $10 per month</p><p>Pro $30 per month</p>
<footer>(c) 2026</footer></body></html>"""
V2 = V1.replace("Pro $30", "Pro $25").replace("(c) 2026", "(c) 2027")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "monitor.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(net, "resolve", lambda host: ["10.0.0.9"] if host == "internal.test" else ["93.184.215.14"])


def add(url="https://example.com/pricing", label="Acme pricing"):
    return client.post("/watches", json={"url": url, "label": label}, headers=KEY)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_watch_crud():
    r = add()
    assert r.status_code == 201
    watch = r.json()
    assert set(watch) == {"id", "url", "label", "created_at", "last_checked_at"}
    assert watch["last_checked_at"] is None
    assert client.get("/watches").json() == [watch]
    assert client.delete(f"/watches/{watch['id']}", headers=KEY).status_code == 204
    assert client.get("/watches").json() == []
    assert client.delete(f"/watches/{watch['id']}", headers=KEY).status_code == 404


def test_writes_need_key():
    assert client.post("/watches", json={"url": "https://example.com"}).status_code == 401
    assert client.post("/watches", json={"url": "https://example.com"}, headers={"X-API-Key": "nope"}).status_code == 401
    assert client.delete("/watches/1").status_code == 401


def test_writes_disabled_without_server_key(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    r = add()
    assert r.status_code == 503
    assert "INTERNAL_API_KEY" in r.json()["detail"]


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://internal.test/", "file:///etc/passwd"])
def test_add_watch_ssrf_guard(url):
    assert add(url).status_code == 422


@respx.mock
def test_check_baseline_then_change():
    route = respx.get("https://example.com/pricing").mock(return_value=httpx.Response(200, html=V1))
    wid = add().json()["id"]

    first = client.post("/check", headers={"X-API-Key": "test-key"}).json()
    assert first == {"changed": [], "unchanged": 1, "errors": []}
    assert client.get("/watches").json()[0]["last_checked_at"] is not None

    assert client.post("/check", headers={"X-API-Key": "test-key"}).json()["unchanged"] == 1  # same content again

    route.mock(return_value=httpx.Response(200, html=V2))
    third = client.post("/check", headers={"X-API-Key": "test-key"}).json()
    assert third["unchanged"] == 0
    [change] = third["changed"]
    assert change["id"] == wid and change["label"] == "Acme pricing"
    assert "-Pro $30 per month" in change["diff"] and "+Pro $25 per month" in change["diff"]
    assert "2027" not in change["diff"]  # footer is ignored
    assert change["added_words"] == 1 and change["removed_words"] == 1

    assert client.post("/check", headers={"X-API-Key": "test-key"}).json()["changed"] == []  # new snapshot stored


@respx.mock
def test_check_one_bad_watch_does_not_fail_others():
    respx.get("https://example.com/pricing").mock(return_value=httpx.Response(200, html=V1))
    respx.get("https://down.example.com/").mock(side_effect=httpx.ConnectError("refused"))
    add()
    bad = add("https://down.example.com/", None).json()
    r = client.post("/check", headers={"X-API-Key": "test-key"}).json()
    assert r["unchanged"] == 1
    assert r["errors"] == [{"id": bad["id"], "url": "https://down.example.com/", "error": "ConnectError: refused"}]


@respx.mock
def test_check_blocks_redirect_to_private():
    respx.get("https://example.com/pricing").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    add()
    [err] = client.post("/check", headers={"X-API-Key": "test-key"}).json()["errors"]
    assert "non-public" in err["error"]


@respx.mock
def test_diff_is_capped():
    route = respx.get("https://example.com/pricing").mock(
        return_value=httpx.Response(200, html="".join(f"<p>line {i}</p>" for i in range(2000)))
    )
    add()
    client.post("/check", headers={"X-API-Key": "test-key"})
    route.mock(return_value=httpx.Response(200, html="".join(f"<p>row {i}</p>" for i in range(2000))))
    [change] = client.post("/check", headers={"X-API-Key": "test-key"}).json()["changed"]
    assert len(change["diff"]) < 4100
    assert change["diff"].endswith("(diff truncated)")


def test_ipv6_forms_embedding_private_ipv4_are_blocked():
    from app.net import _is_public
    for addr in ("64:ff9b::7f00:1", "2002:7f00:1::", "::127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a9fe:a9fe"):
        assert not _is_public(addr), addr
    assert _is_public("64:ff9b::808:808")      # NAT64 of a public address (8.8.8.8) is fine


def test_check_requires_key():
    assert client.post("/check").status_code == 401
