import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import net
from app.main import app

client = TestClient(app)

FEED_A = "https://blog.example.com/feed"
FEED_B = "https://news.example.org/rss"


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>https://blog.example.com/{g}</link><guid>{g}</guid>"
        f"<pubDate>Mon, 21 Sep 2026 0{i}:00:00 GMT</pubDate>"
        f"<description>&lt;p&gt;About &lt;b&gt;{t}&lt;/b&gt;&lt;/p&gt;</description></item>"
        for i, (g, t) in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title>{body}</channel></rss>'


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    feeds = tmp_path / "feeds.txt"
    feeds.write_text(f"# marketing feeds\n{FEED_A}\n\n")
    monkeypatch.setenv("FEEDS_FILE", str(feeds))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "rss.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(net, "resolve", lambda host: ["93.184.215.14"])


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


@respx.mock
def test_poll_default_feeds_returns_only_new_items():
    route = respx.get(FEED_A).mock(return_value=httpx.Response(200, text=rss(("a1", "First"), ("a2", "Second"))))
    first = client.post("/poll", headers={"X-API-Key": "test-key"}, json={}).json()
    assert first["checked"] == 1 and first["errors"] == []
    assert [i["title"] for i in first["new_items"]] == ["First", "Second"]
    assert first["new_items"][0] == {
        "feed": FEED_A, "title": "First", "link": "https://blog.example.com/a1",
        "published": "2026-09-21T00:00:00+00:00", "summary": "About First",
    }

    route.mock(return_value=httpx.Response(200, text=rss(("a3", "Third"), ("a1", "First"), ("a2", "Second"))))
    second = client.post("/poll", headers={"X-API-Key": "test-key"}, json={}).json()
    assert [i["title"] for i in second["new_items"]] == ["Third"]


@respx.mock
def test_max_items_per_feed():
    respx.get(FEED_A).mock(return_value=httpx.Response(200, text=rss(("a1", "One"), ("a2", "Two"), ("a3", "Three"))))
    r = client.post("/poll", headers={"X-API-Key": "test-key"}, json={"feeds": [FEED_A], "max_items_per_feed": 2}).json()
    assert len(r["new_items"]) == 2


@respx.mock
def test_bad_feed_does_not_fail_poll():
    respx.get(FEED_A).mock(return_value=httpx.Response(200, text=rss(("a1", "First"))))
    respx.get(FEED_B).mock(return_value=httpx.Response(500))
    r = client.post("/poll", headers={"X-API-Key": "test-key"}, json={"feeds": [FEED_A, FEED_B, "http://127.0.0.1/rss"]}).json()
    assert r["checked"] == 3
    assert len(r["new_items"]) == 1
    assert [e["feed"] for e in r["errors"]] == [FEED_B, "http://127.0.0.1/rss"]
    assert "HTTP 500" in r["errors"][0]["error"]
    assert "non-public" in r["errors"][1]["error"]


@respx.mock
def test_garbage_feed_is_an_error():
    respx.get(FEED_A).mock(return_value=httpx.Response(200, text="<<< not xml"))
    r = client.post("/poll", headers={"X-API-Key": "test-key"}, json={}).json()
    assert r["new_items"] == [] and r["errors"][0]["feed"] == FEED_A


def test_summary_is_plain_text_and_capped():
    from app.main import plain_text
    out = plain_text("<p>" + "word " * 300 + "</p>")
    assert len(out) <= 500 and out.endswith("...") and "<" not in out


def test_no_feeds_is_422(monkeypatch, tmp_path):
    monkeypatch.setenv("FEEDS_FILE", str(tmp_path / "missing.txt"))
    assert client.post("/poll", headers={"X-API-Key": "test-key"}, json={}).status_code == 422


@respx.mock
def test_items_most_recent_first():
    respx.get(FEED_A).mock(return_value=httpx.Response(200, text=rss(("a1", "Older"), ("a2", "Newer"))))
    client.post("/poll", headers={"X-API-Key": "test-key"}, json={})
    items = client.get("/items", params={"limit": 1}).json()
    assert [i["title"] for i in items] == ["Newer"]
    assert set(items[0]) == {"feed", "title", "link", "published", "summary"}


def test_items_limit_validated():
    assert client.get("/items", params={"limit": 0}).status_code == 422


def test_ipv6_forms_embedding_private_ipv4_are_blocked():
    from app.net import _is_public
    for addr in ("64:ff9b::7f00:1", "2002:7f00:1::", "::127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a9fe:a9fe"):
        assert not _is_public(addr), addr
    assert _is_public("64:ff9b::808:808")      # NAT64 of a public address (8.8.8.8) is fine


def test_poll_requires_key():
    assert client.post("/poll", json={}).status_code == 401
