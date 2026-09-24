import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import net
from app.main import app, score

client = TestClient(app)

BODY = " ".join(["Coffee subscription boxes ship fresh beans every month."] + ["filler"] * 300)
GOOD = f"""<!doctype html><html lang="en"><head>
<title>Coffee Subscription: Fresh Beans Every Month</title>
<meta name="description" content="Our coffee subscription sends freshly roasted beans to your door every month. Pause or cancel anytime.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="https://example.com/coffee">
<meta property="og:title" content="Coffee Subscription">
<meta property="og:image" content="https://example.com/og.png">
</head><body><main>
<h1>Coffee subscription</h1><p>{BODY}</p>
<h2>Plans</h2><h3>Monthly</h3>
<img src="a.png" alt="beans"><img src="b.png" alt="">
</main></body></html>"""

BAD = """<html><head><title>Hi</title><meta name="robots" content="noindex, nofollow"></head>
<body><h1>One</h1><h1>Two</h1><h4>Deep</h4><img src="a.png"><img src="b.png" alt="b"><img src="c.png" alt="c"></body></html>"""


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(net, "resolve", lambda host: ["10.1.2.3"] if host == "internal.test" else ["93.184.215.14"])


def statuses(body):
    return {c["id"]: c["status"] for c in body["checks"]}


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_good_page_passes_everything():
    r = client.post("/audit", json={"html": GOOD, "keyword": "coffee subscription"})
    assert r.status_code == 200
    body = r.json()
    assert body["url"] is None
    assert set(statuses(body).values()) == {"pass"}, body["checks"]
    assert body["score"] == 100
    assert len(body["checks"]) == 15


def test_bad_page():
    body = client.post("/audit", json={"html": BAD}).json()
    s = statuses(body)
    assert s == {
        "title": "warn", "meta_description": "fail", "h1": "fail", "heading_order": "warn",
        "img_alt": "warn", "canonical": "warn", "open_graph": "warn", "lang": "warn",
        "viewport": "fail", "indexable": "fail", "word_count": "warn",
    }
    # 0 pass, 7 warn, 4 fail over 11 checks
    assert body["score"] == round(100 * 3.5 / 11)


def test_img_alt_fail_below_half():
    s = statuses(client.post("/audit", json={"html": '<img src="a"><img src="b"><img alt="c">'}).json())
    assert s["img_alt"] == "fail"


def test_keyword_missing_warns():
    s = statuses(client.post("/audit", json={"html": GOOD, "keyword": "espresso machine"}).json())
    assert [s[k] for k in ("keyword_in_title", "keyword_in_h1", "keyword_in_intro", "keyword_in_description")] == ["warn"] * 4


def test_score_formula():
    checks = [{"status": "pass"}, {"status": "warn"}, {"status": "fail"}]
    assert score(checks) == 50


@respx.mock
def test_audit_url_uses_x_robots_tag_header():
    respx.get("https://example.com/coffee").mock(
        return_value=httpx.Response(200, html=GOOD, headers={"x-robots-tag": "noindex"})
    )
    body = client.post("/audit", json={"url": "https://example.com/coffee"}).json()
    assert body["url"] == "https://example.com/coffee"
    assert statuses(body)["indexable"] == "fail"


@pytest.mark.parametrize("payload", [{}, {"url": "https://example.com", "html": "<p/>"}, {"keyword": "x"}])
def test_exactly_one_input_required(payload):
    assert client.post("/audit", json=payload).status_code == 422


@respx.mock
def test_upstream_error_is_502():
    respx.get("https://example.com/down").mock(return_value=httpx.Response(503))
    assert client.post("/audit", json={"url": "https://example.com/down"}).status_code == 502


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://internal.test/", "gopher://example.com/"])
def test_ssrf_guard_blocks(url):
    assert client.post("/audit", json={"url": url}).status_code == 422


@respx.mock
def test_ssrf_guard_rechecks_redirects():
    respx.get("https://example.com/r").mock(
        return_value=httpx.Response(302, headers={"location": "http://192.168.1.1/"})
    )
    assert client.post("/audit", json={"url": "https://example.com/r"}).status_code == 422


def test_ipv6_forms_embedding_private_ipv4_are_blocked():
    from app.net import _is_public
    for addr in ("64:ff9b::7f00:1", "2002:7f00:1::", "::127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a9fe:a9fe"):
        assert not _is_public(addr), addr
    assert _is_public("64:ff9b::808:808")      # NAT64 of a public address (8.8.8.8) is fine
