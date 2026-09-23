import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app

KEY = {"X-API-Key": "test-key"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "links.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")
    monkeypatch.setenv("BASE_URL", "https://go.example.com/")
    return TestClient(app, follow_redirects=False)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_random_slug(client):
    r = client.post("/links", json={"url": "https://example.com/sale?utm_source=x"}, headers=KEY)
    assert r.status_code == 201
    body = r.json()
    assert re.fullmatch(r"[A-Za-z0-9]{6}", body["slug"])
    assert body["short_url"] == f"https://go.example.com/{body['slug']}"
    assert body["url"] == "https://example.com/sale?utm_source=x"


def test_create_requires_key(client, monkeypatch):
    assert client.post("/links", json={"url": "https://example.com"}).status_code == 401
    assert client.post("/links", json={"url": "https://example.com"},
                       headers={"X-API-Key": "wrong"}).status_code == 401
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/links", json={"url": "https://example.com"}, headers=KEY).status_code == 503


@pytest.mark.parametrize("url", ["ftp://example.com", "/relative", "javascript:alert(1)", "example.com"])
def test_rejects_non_http_urls(client, url):
    assert client.post("/links", json={"url": url}, headers=KEY).status_code == 422


@pytest.mark.parametrize("slug", ["ab", "has space", "x" * 41, "health", "Links", "docs", "redoc"])
def test_rejects_bad_or_reserved_slugs(client, slug):
    r = client.post("/links", json={"url": "https://example.com", "slug": slug}, headers=KEY)
    assert r.status_code == 422


def test_custom_slug_conflict(client):
    body = {"url": "https://example.com", "slug": "spring-sale"}
    assert client.post("/links", json=body, headers=KEY).status_code == 201
    assert client.post("/links", json=body, headers=KEY).status_code == 409


def test_redirect_records_clicks_and_stats(client):
    client.post("/links", json={"url": "https://example.com/p", "slug": "promo"}, headers=KEY)
    r = client.get("/promo", headers={"Referer": "https://www.linkedin.com/feed/?x=1"})
    assert r.status_code == 302 and r.headers["location"] == "https://example.com/p"
    client.get("/promo", headers={"Referer": "https://www.linkedin.com/in/someone"})
    client.get("/promo")

    stats = client.get("/links/promo/stats").json()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert stats == {
        "slug": "promo", "url": "https://example.com/p", "clicks": 3,
        "by_day": {today: 3}, "referrers": {"www.linkedin.com": 2, "direct": 1},
    }


def test_unknown_slug_404(client):
    assert client.get("/nope").status_code == 404
    assert client.get("/links/nope/stats").status_code == 404


def test_health_and_docs_not_shadowed(client):
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "link-shortener"


# ---------- GET /links


def add(client, url, slug=None):
    body = {"url": url} | ({"slug": slug} if slug else {})
    r = client.post("/links", json=body, headers=KEY)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def seeded(client):
    add(client, "https://ex.com/a?utm_campaign=spring&utm_content=12&utm_source=linkedin", "a-spring-12")
    add(client, "https://ex.com/b?utm_source=x&utm_content=13&utm_campaign=spring", "b-spring-13")
    add(client, "https://ex.com/c?utm_campaign=autumn&utm_content=12", "c-autumn-12")
    add(client, "https://ex.com/d", "d-plain")
    add(client, "https://ex.com/e?utm_campaign=spring-sale&utm_content=12", "e-springsale")
    client.get("/a-spring-12")
    client.get("/a-spring-12")
    client.get("/b-spring-13")
    return client


def slugs(r):
    assert r.status_code == 200, r.text
    return [x["slug"] for x in r.json()]


def test_list_links_all_newest_first(seeded):
    r = seeded.get("/links")
    assert slugs(r) == ["e-springsale", "d-plain", "c-autumn-12", "b-spring-13", "a-spring-12"]
    first = r.json()[-1]
    assert set(first) == {"slug", "short_url", "url", "clicks", "created_at"}
    assert first["short_url"] == "https://go.example.com/a-spring-12"
    assert first["clicks"] == 2
    assert first["url"].startswith("https://ex.com/a?")
    assert r.json()[0]["clicks"] == 0


def test_list_links_filter_by_campaign_exact(seeded):
    r = seeded.get("/links?utm_campaign=spring")
    assert slugs(r) == ["b-spring-13", "a-spring-12"]  # not spring-sale
    assert sum(x["clicks"] for x in r.json()) == 3


def test_list_links_filter_by_content(seeded):
    assert slugs(seeded.get("/links?utm_content=12")) == ["e-springsale", "c-autumn-12", "a-spring-12"]


def test_list_links_filters_combine(seeded):
    assert slugs(seeded.get("/links?utm_campaign=spring&utm_content=12")) == ["a-spring-12"]
    assert slugs(seeded.get("/links?utm_campaign=autumn&utm_content=13")) == []


def test_list_links_empty_filter_means_no_filter(seeded):
    assert len(slugs(seeded.get("/links?utm_campaign=&utm_content="))) == 5


def test_list_links_filter_not_fooled_by_path_or_other_params(client):
    add(client, "https://ex.com/utm_campaign=spring", "in-path")
    add(client, "https://ex.com/p?ref=utm_campaign%3Dspring", "in-value")
    add(client, "https://ex.com/p?utm_campaign=Spring", "other-case")
    add(client, "https://ex.com/p?utm_campaign=spring%20sale", "encoded")
    assert slugs(client.get("/links?utm_campaign=spring")) == []
    assert slugs(client.get("/links", params={"utm_campaign": "spring sale"})) == ["encoded"]


def test_list_links_limit(seeded):
    assert slugs(seeded.get("/links?limit=2")) == ["e-springsale", "d-plain"]
    assert slugs(seeded.get("/links?utm_content=12&limit=1")) == ["e-springsale"]
    assert len(slugs(seeded.get("/links?limit=1000"))) == 5


@pytest.mark.parametrize("limit", ["0", "-1", "1001", "abc"])
def test_list_links_bad_limit_422(seeded, limit):
    assert seeded.get(f"/links?limit={limit}").status_code == 422


def test_list_links_empty_db(client):
    assert client.get("/links").json() == []


def test_list_links_needs_no_key(seeded, monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert seeded.get("/links").status_code == 200


def test_list_links_does_not_shadow_other_routes(seeded):
    assert seeded.get("/links/a-spring-12/stats").json()["clicks"] == 2
    r = seeded.get("/a-spring-12")
    assert r.status_code == 302
    assert seeded.get("/links/a-spring-12/stats").json()["clicks"] == 3
    # listing is not counted as a click and "links" can never be a slug
    seeded.get("/links")
    assert seeded.get("/links/a-spring-12/stats").json()["clicks"] == 3
    assert seeded.post("/links", json={"url": "https://ex.com", "slug": "links"}, headers=KEY).status_code == 422
