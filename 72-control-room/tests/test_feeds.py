"""Product feed page (87): batches with counts, downloads passed through with the service key;
no key or internal URL in the page or the download; 'not installed' when FEED_URL is empty."""
import httpx
import pytest

from .conftest import KEYS

FEED = "http://feed.internal:8000"
BATCH = {"id": 3, "name": "products.tsv", "format": "tsv", "created_at": "2026-09-28T06:00:00Z", "products": 30,
         "pending": 0, "proposed": 28, "unchanged": 2, "titles": {"model": 27, "rule_based": 3, "original": 0},
         "rejected": {"titles": 3, "descriptions": 1, "reasons": {"colour": 2, "number": 1}}, "errors": 0,
         "approved": 12, "name_html": "<b>x</b>"}


@pytest.fixture
def feed(monkeypatch):
    monkeypatch.setenv("FEED_URL", FEED)


def test_list_with_counts_and_links(feed, authed, mock):
    c, _ = authed
    route = mock.get(f"{FEED}/batches").respond(json=[BATCH | {"name": "<script>x</script>.tsv"}])
    h = c.get("/feeds").text
    assert "30 products" in h and "28 with a proposed change" in h and "<b>12 approved</b>" in h
    assert "rejected 3" in h and "colour 2, number 1" in h
    assert 'href="/feeds/3/export.tsv"' in h and 'href="/feeds/3/supplemental.tsv"' in h
    assert "&lt;script&gt;" in h and "<script>x" not in h
    assert route.calls[0].request.headers["x-api-key"] == KEYS["INTERNAL_API_KEY"]
    for secret in (*KEYS.values(), FEED, "feed.internal"):
        assert secret not in h


def test_download_is_passed_through_as_an_attachment(feed, authed, mock):
    c, _ = authed
    body = "id\ttitle\nA1\tKiln & Co Mug\n".encode()
    route = mock.get(f"{FEED}/batches/3/supplemental.tsv").respond(content=body)
    r = c.get("/feeds/3/supplemental.tsv")
    assert r.status_code == 200 and r.content == body
    assert r.headers["content-disposition"] == 'attachment; filename="feed-3-supplemental.tsv"'
    assert r.headers["content-type"].startswith("text/tab-separated-values")
    assert route.calls[0].request.headers["x-api-key"] == KEYS["INTERNAL_API_KEY"]
    assert KEYS["INTERNAL_API_KEY"] not in str(r.headers)


def test_download_rejects_other_paths_and_reports_errors(feed, authed, mock):
    c, _ = authed
    assert c.get("/feeds/3/raw.tsv").status_code == 404
    assert c.get("/feeds/3/export.xlsx").status_code == 404
    mock.get(f"{FEED}/batches/9/export.csv").respond(404, json={"detail": "batch 9 not found"})
    assert c.get("/feeds/9/export.csv").status_code == 404
    mock.get(f"{FEED}/batches").mock(side_effect=httpx.ConnectError("refused"))
    h = c.get("/feeds").text
    assert "feed-optimizer: unreachable (ConnectError)" in h and FEED not in h


def test_not_installed(authed, mock):
    c, _ = authed
    assert "isn't installed" in c.get("/feeds").text
    assert c.get("/feeds/3/export.csv").status_code == 404


def test_needs_login(client):
    for path in ("/feeds", "/feeds/3/export.csv"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_more_links_to_it(authed):
    c, _ = authed
    assert 'href="/feeds"' in c.get("/more").text
