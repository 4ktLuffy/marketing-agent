import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import net
from app.main import app

client = TestClient(app)

FAKE_DNS = {"example.com": ["93.184.215.14"], "intranet.example.com": ["10.0.0.5"]}

PAGE = """<!doctype html>
<html lang="en"><head>
<title>Spring Sale | Example</title>
<meta name="description" content="Our biggest sale of the year.">
<meta property="og:title" content="Spring Sale">
<meta property="og:image" content="https://example.com/og.png">
<script>var tracking = 1;</script>
</head><body>
<header><nav><a href="/">Home</a><a href="/pricing">Pricing</a></nav></header>
<main>
  <h1>Spring   Sale</h1>
  <p>Save 30% on   everything.</p>
  <h2>How it works</h2><p>Pick a plan. <a href="https://other.org/x">Read more</a></p>
  <h4>Ignored level</h4>
  <aside>Sidebar ad</aside>
</main>
<footer>Copyright <a href="https://www.example.com/legal">Legal</a> <a href="mailto:a@b.c">mail</a></footer>
</body></html>"""


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(net, "resolve", lambda host: FAKE_DNS.get(host, ["93.184.215.14"]))


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_extract_from_html():
    r = client.post("/extract", json={"html": PAGE})
    assert r.status_code == 200
    body = r.json()
    assert body["url"] is None
    assert body["title"] == "Spring Sale | Example"
    assert body["description"] == "Our biggest sale of the year."
    assert body["lang"] == "en"
    assert body["headings"] == [{"level": 1, "text": "Spring Sale"}, {"level": 2, "text": "How it works"}]
    assert body["text"] == "Spring Sale Save 30% on everything. How it works Pick a plan. Read more Ignored level"
    assert body["word_count"] == 16
    assert body["og"] == {"title": "Spring Sale", "image": "https://example.com/og.png"}
    # no base URL: relative links internal, absolute external, mailto skipped
    assert body["links"] == {"internal": 2, "external": 2}


@respx.mock
def test_extract_from_url_follows_redirect_and_counts_links():
    respx.get("https://example.com/sale").mock(
        return_value=httpx.Response(301, headers={"location": "/spring"})
    )
    route = respx.get("https://example.com/spring").mock(
        return_value=httpx.Response(200, html=PAGE)
    )
    r = client.post("/extract", json={"url": "https://example.com/sale"})
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "https://example.com/spring"
    assert body["links"] == {"internal": 3, "external": 1}  # www.example.com counts as internal
    assert route.calls.last.request.headers["user-agent"] == "marketing-agent/1.0 (+page-extractor)"


def test_text_is_capped():
    html = "<article>" + "word " * 10_000 + "</article>"
    body = client.post("/extract", json={"html": html}).json()
    assert len(body["text"]) == 20_000
    assert body["word_count"] == 10_000


@pytest.mark.parametrize("payload", [{}, {"url": "https://example.com", "html": "<p>x</p>"}])
def test_exactly_one_input_required(payload):
    assert client.post("/extract", json=payload).status_code == 422


@respx.mock
def test_upstream_error_is_502():
    respx.get("https://example.com/gone").mock(return_value=httpx.Response(404))
    r = client.post("/extract", json={"url": "https://example.com/gone"})
    assert r.status_code == 502
    assert "404" in r.json()["detail"]


@respx.mock
def test_upstream_timeout_is_502():
    respx.get("https://example.com/slow").mock(side_effect=httpx.ConnectTimeout("timed out"))
    assert client.post("/extract", json={"url": "https://example.com/slow"}).status_code == 502


@respx.mock
def test_non_html_is_502():
    respx.get("https://example.com/f.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    )
    assert client.post("/extract", json={"url": "https://example.com/f.pdf"}).status_code == 502


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest/meta-data",
    "http://intranet.example.com/", "ftp://example.com/x", "file:///etc/passwd",
])
def test_ssrf_guard_blocks(url):
    assert client.post("/extract", json={"url": url}).status_code == 422


@respx.mock
def test_ssrf_guard_rechecks_redirects():
    respx.get("https://example.com/r").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1:8000/admin"})
    )
    admin = respx.get("http://127.0.0.1:8000/admin")
    assert client.post("/extract", json={"url": "https://example.com/r"}).status_code == 422
    assert not admin.called


@respx.mock
def test_allow_private_urls(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_URLS", "true")
    respx.get("http://127.0.0.1/").mock(return_value=httpx.Response(200, html="<title>Local</title>"))
    assert client.post("/extract", json={"url": "http://127.0.0.1/"}).json()["title"] == "Local"


def test_ipv4_mapped_ipv6_is_blocked():
    with pytest.raises(net.BlockedURL):
        net.check_url("http://[::ffff:127.0.0.1]/")
