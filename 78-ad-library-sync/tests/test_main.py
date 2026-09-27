"""Tests for ad-library-sync. No real Meta, 09, 58, 11 or 06 call is made: respx mocks them.

Graph API shapes follow the ads_archive reference: {"data": [ArchivedAd...], "paging":
{"cursors": {...}, "next": "https://graph.facebook.com/...&after=..."}}; the end of the result
set is an empty `data`. Errors: {"error": {"message", "type", "code", "error_subcode", "fbtrace_id"}}.
"""
import json
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.main import app

KEY = "internal-secret-key"
AUTH = {"X-API-Key": KEY}
TOKEN = "EAAtest-meta-token-value-123"
GRAPH = f"https://graph.facebook.com/{main.DEFAULT_GRAPH_VERSION}/ads_archive"
MON = "http://monitor.test"

client = TestClient(app)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for k in ("MONITOR_URL", "REVIEWS_URL", "LISTENING_URL", "LISTENING_QUERY", "KB_URL", "OWN_DOMAINS",
              "AD_COUNTRIES", "META_GRAPH_VERSION", "SUGGEST_MIN_MENTIONS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("META_AD_LIBRARY_TOKEN", TOKEN)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ads.sqlite"))
    monkeypatch.setattr(main.time, "sleep", lambda s: None)


def add(**kw):
    body = {"name": "Rival Beans", "website": "https://rival.example.org/", "meta_page_id": "123456",
            "markets": ["DE", "FR"]} | kw
    return client.post("/competitors", json=body, headers=AUTH)


def ad(i, body="Fresh beans every month", stop=None, page="Rival Beans"):
    return {"id": str(i), "page_id": "123456", "page_name": page, "ad_creative_bodies": [body],
            "ad_creative_link_titles": ["Try Rival"], "ad_delivery_start_time": "2026-09-01",
            "ad_delivery_stop_time": stop, "publisher_platforms": ["facebook", "instagram"], "languages": ["de"]}


# ---------- health, auth, registry


def test_health_without_token(monkeypatch):
    monkeypatch.delenv("META_AD_LIBRARY_TOKEN")
    h = client.get("/health").json()
    assert h["configured"] is False
    assert "META_AD_LIBRARY_TOKEN" in h["config_errors"][0]
    assert h["countries"] == ["DE", "FR", "NL", "IE"] and h["non_eu_countries"] == []


def test_health_configured_never_shows_token():
    r = client.get("/health")
    assert r.json()["configured"] is True
    assert TOKEN not in r.text and KEY not in r.text


def test_sync_without_token_is_503(monkeypatch):
    monkeypatch.delenv("META_AD_LIBRARY_TOKEN")
    r = client.post("/sync", headers=AUTH)
    assert r.status_code == 503 and "META_AD_LIBRARY_TOKEN" in r.json()["detail"]


def test_writes_need_key(monkeypatch):
    assert client.post("/competitors", json={"name": "X"}).status_code == 401
    assert client.post("/sync").status_code == 401
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/sync", headers=AUTH).status_code == 503


def test_competitor_crud_and_validation():
    r = add(domains=["https://www.rival-shop.example.org/x"], markets=["de", "US"])
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["domains"] == ["rival-shop.example.org"] and c["markets"] == ["DE", "US"]
    assert c["status"] == "active" and c["watch_state"]["error"].startswith("MONITOR_URL is not set")
    assert add().status_code == 409
    assert client.get("/competitors/rival beans").json()["id"] == c["id"]
    assert client.get(f"/competitors/{c['id']}").json()["name"] == "Rival Beans"
    p = client.patch(f"/competitors/{c['id']}", json={"notes": "main rival"}, headers=AUTH).json()
    assert p["notes"] == "main rival" and p["meta_page_id"] == "123456"
    for bad in ({"meta_page_id": "rival"}, {"markets": ["Germany"]}, {"status": "maybe"},
                {"website": "ftp://x.org"}, {"key_pages": [{"url": "https://x.org", "type": "blog"}]},
                {"google_advertiser_id": "123"}, {"name": "42"}):
        assert add(**({"name": "Other"} | bad)).status_code == 422, bad
    d = client.delete("/competitors/Rival Beans", headers=AUTH)
    assert d.status_code == 200 and d.json()["deleted"] == "Rival Beans"
    assert client.get("/competitors").json() == []
    assert client.get("/competitors/1").status_code == 404


def test_upsert_merges_key_pages_by_website():
    add(key_pages=[{"url": "https://rival.example.org/pricing", "type": "pricing"}])
    r = client.post("/competitors?upsert=true", headers=AUTH, json={
        "name": "Rival", "website": "https://www.rival.example.org",
        "key_pages": [{"url": "https://rival.example.org/about", "type": "about"}]})
    assert r.status_code == 200
    c = r.json()
    assert c["name"] == "Rival Beans"
    assert [p["type"] for p in c["key_pages"]] == ["pricing", "about"]
    assert len(client.get("/competitors").json()) == 1


# ---------- 09 watch sync


class FakeMonitor:
    """Just enough of 09: /watches GET (tag filter), POST, DELETE."""

    def __init__(self, router):
        self.watches, self.next = [], 1
        router.get(f"{MON}/watches").mock(side_effect=self.list)
        router.post(f"{MON}/watches").mock(side_effect=self.add)
        router.delete(url__regex=rf"{MON}/watches/\d+").mock(side_effect=self.delete)

    def list(self, request):
        tag = request.url.params.get("tag")
        return httpx.Response(200, json=[w for w in self.watches if tag is None or w["tag"] == tag])

    def add(self, request):
        assert request.headers["x-api-key"] == KEY
        b = json.loads(request.content)
        w = {"id": self.next, "css": None, "xpath": None, "include_filters": [], "ignore_patterns": [],
             "trigger_text": None, "tag": None, "label": None} | b
        self.next += 1
        self.watches.append(w)
        return httpx.Response(201, json=w)

    def delete(self, request):
        wid = int(request.url.path.rsplit("/", 1)[1])
        self.watches = [w for w in self.watches if w["id"] != wid]
        return httpx.Response(204)


@respx.mock
def test_active_competitor_pages_become_09_watches_idempotently(monkeypatch):
    monkeypatch.setenv("MONITOR_URL", MON)
    mon = FakeMonitor(respx)
    mon.watches.append({"id": 99, "url": "https://mine.example/", "tag": None, "label": "my own watch"})
    c = add(key_pages=[{"url": "https://rival.example.org/pricing", "type": "pricing"}]).json()
    s = c["watch_state"]
    assert (s["added"], s["removed"], s["kept"]) == (2, 0, 0) and "error" not in s
    ours = [w for w in mon.watches if w["tag"] == f"competitor:{c['id']}"]
    assert {w["label"] for w in ours} == {"Rival Beans · home", "Rival Beans · pricing"}
    pricing = next(w for w in ours if w["url"].endswith("/pricing"))
    assert any("only" in p for p in pricing["ignore_patterns"])  # urgency lines ignored by default
    # Same data again: nothing added or removed (snapshots kept).
    s2 = client.post("/watches/sync", headers=AUTH).json()["competitors"][0]
    assert (s2["added"], s2["removed"], s2["kept"]) == (0, 0, 2)
    # A css override on pricing: that watch is replaced, home is kept.
    p = client.patch(f"/competitors/{c['id']}", headers=AUTH, json={"key_pages": [
        {"url": "https://rival.example.org/pricing", "type": "pricing", "css": ".plans"}]}).json()["watch_state"]
    assert (p["added"], p["removed"], p["kept"]) == (1, 1, 1)
    # Ignored -> its watches go; the unrelated watch stays.
    client.post(f"/competitors/{c['id']}/status", json={"status": "ignored"}, headers=AUTH)
    assert [w["id"] for w in mon.watches] == [99]
    client.post(f"/competitors/{c['id']}/status", json={"status": "active"}, headers=AUTH)
    assert len(mon.watches) == 3
    d = client.delete(f"/competitors/{c['id']}", headers=AUTH).json()
    assert d["watches_removed"] == 2 and [w["id"] for w in mon.watches] == [99]


@respx.mock
def test_monitor_down_does_not_fail_the_registry(monkeypatch):
    monkeypatch.setenv("MONITOR_URL", MON)
    respx.get(f"{MON}/watches").mock(side_effect=httpx.ConnectError("refused"))
    r = add()
    assert r.status_code == 201
    assert "change-monitor (09) failed" in r.json()["watch_state"]["error"]


@respx.mock
def test_monitor_refusing_a_url_is_reported(monkeypatch):
    monkeypatch.setenv("MONITOR_URL", MON)
    respx.get(f"{MON}/watches").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{MON}/watches").mock(return_value=httpx.Response(422, json={"detail": "non-public address"}))
    s = add().json()["watch_state"]
    assert s["added"] == 0 and s["errors"][0]["error"].startswith("09 returned 422 non-public")


def test_suggested_competitor_gets_no_watches(monkeypatch):
    monkeypatch.setenv("MONITOR_URL", MON)
    with respx.mock:
        respx.get(f"{MON}/watches").mock(return_value=httpx.Response(200, json=[]))
        s = add(status="suggested").json()["watch_state"]
    assert s["added"] == 0 and s["watches"] == []


# ---------- links


def test_links_without_scraping():
    add(markets=["DE", "US"], google_advertiser_id="AR01234567890123456789", linkedin_company_id="777")
    r = client.get("/links/Rival Beans").json()
    by = {}
    for link in r["links"]:
        by.setdefault(link["platform"], []).append(link)
    meta = {m["market"]: m for m in by["meta"]}
    assert set(meta) == {"ALL", "DE", "US"}
    assert "view_all_page_id=123456" in meta["DE"]["url"] and meta["DE"]["api"] is True
    assert meta["US"]["api"] is False
    assert by["google"][0]["url"] == "https://adstransparency.google.com/advertiser/AR01234567890123456789?region=anywhere"
    assert "domain=rival.example.org" in by["google"][1]["url"]
    assert by["linkedin"][0]["url"] == "https://www.linkedin.com/ad-library/search?companyIds=777"
    assert "adv_name=Rival+Beans" in by["tiktok"][0]["url"]
    assert all(link["note"] for link in r["links"])
    assert client.get("/links").json()[0]["competitor"] == "Rival Beans"
    assert client.get("/links/nobody").status_code == 404


def test_links_by_name_without_page_id():
    add(meta_page_id=None)
    meta = client.get("/links/Rival Beans").json()["links"][0]
    assert "q=Rival+Beans" in meta["url"] and "keyword_unordered" in meta["url"]


# ---------- Meta sync


@respx.mock
def test_sync_pages_dedupes_and_tracks_changes(caplog):
    caplog.set_level(logging.DEBUG)
    add()
    nxt = GRAPH + "?after=abc&access_token=" + TOKEN
    route = respx.get(GRAPH).mock(side_effect=[
        httpx.Response(200, json={"data": [ad(1), ad(2)], "paging": {"next": nxt}}),
        httpx.Response(200, json={"data": [ad(2), ad(3, stop="2026-09-05")], "paging": {"next": nxt + "2"}}),
        httpx.Response(200, json={"data": []}),
    ])
    r = client.post("/sync", headers=AUTH)
    assert r.status_code == 200, r.text
    s = r.json()
    assert (s["seen"], s["new"]) == (4, 3)
    q = parse_qs(urlsplit(str(route.calls[0].request.url)).query)
    assert q["search_page_ids"] == ["[123456]"] and q["ad_reached_countries"] == ['["DE", "FR"]']
    assert q["ad_active_status"] == ["ALL"] and "ad_snapshot_url" not in q["fields"][0]
    assert "after=abc" in str(route.calls[1].request.url)
    ads = client.get("/ads").json()
    assert ads["count"] == 3
    a3 = next(a for a in ads["ads"] if a["ad_id"] == "3")
    assert a3["status"] == "stopped" and a3["change"] == "new" and a3["texts"] == ["Fresh beans every month"]
    assert a3["library_url"] == "https://www.facebook.com/ads/library/?id=3"
    first_seen = a3["first_seen"]

    # Second sync: ad 1 text changed, ad 2 stopped, ad 3 unchanged.
    route.side_effect = [httpx.Response(200, json={"data": [ad(1, body="Now 20% off"), ad(2, stop="2026-09-20"),
                                                            ad(3, stop="2026-09-05")]})]
    s2 = client.post("/sync", headers=AUTH).json()
    assert (s2["new"], s2["changed"], s2["stopped"]) == (0, 1, 1)
    all_ads = {a["ad_id"]: a for a in client.get("/ads?since=2000-01-01").json()["ads"]}
    assert all_ads["1"]["texts"] == ["Now 20% off"] and all_ads["1"]["changed_at"]
    assert all_ads["2"]["status"] == "stopped" and all_ads["2"]["stopped_seen_at"]
    assert all_ads["3"]["first_seen"] == first_seen
    future = client.get("/ads?since=2999-01-01").json()
    assert future["count"] == 0
    assert client.get("/ads?since=yesterday").status_code == 422
    assert client.get("/ads?competitor=Rival Beans").json()["count"] == 3
    assert TOKEN not in caplog.text and TOKEN not in json.dumps(client.get("/ads").json())


@respx.mock
def test_paging_link_to_another_host_is_not_followed():
    add()
    respx.get(GRAPH).mock(return_value=httpx.Response(200, json={
        "data": [ad(1)], "paging": {"next": "https://evil.test/steal?access_token=" + TOKEN}}))
    evil = respx.get("https://evil.test/steal").mock(return_value=httpx.Response(200, json={"data": []}))
    s = client.post("/sync", headers=AUTH).json()
    assert s["seen"] == 1 and not evil.called
    assert any("paging link not on graph.facebook.com" in w for w in s["warnings"])


@respx.mock
def test_expired_token_is_clear_502_and_writes_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    add()
    respx.get(GRAPH).mock(return_value=httpx.Response(400, json={"error": {
        "message": f"Error validating access token: Session has expired. token {TOKEN}",
        "type": "OAuthException", "code": 190, "error_subcode": 463}}))
    r = client.post("/sync", headers=AUTH)
    assert r.status_code == 502
    d = r.json()["detail"]
    assert "expired or invalid" in d and "190/463" in d and "long-lived token" in d
    assert TOKEN not in r.text and TOKEN not in caplog.text
    assert client.get("/ads?since=2000-01-01").json()["count"] == 0
    assert client.get("/health").json()["last_sync"] is None


@respx.mock
def test_rate_limit_returns_429_with_retry_after():
    add()
    respx.get(GRAPH).mock(return_value=httpx.Response(400, headers={"retry-after": "600"}, json={
        "error": {"message": "Application request limit reached", "type": "OAuthException", "code": 4}}))
    r = client.post("/sync", headers=AUTH)
    assert r.status_code == 429 and r.headers["retry-after"] == "600"
    assert "rate limit" in r.json()["detail"] and "600 s" in r.json()["detail"]


@respx.mock
def test_short_rate_limit_is_retried_once():
    add()
    respx.get(GRAPH).mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "2"}, json={"error": {"message": "slow down", "code": 17}}),
        httpx.Response(200, json={"data": [ad(1)]}),
    ])
    assert client.post("/sync", headers=AUTH).json()["new"] == 1


@respx.mock
def test_identity_not_confirmed_is_explained():
    add()
    respx.get(GRAPH).mock(return_value=httpx.Response(400, json={"error": {
        "message": "Application does not have permission for this action", "code": 10, "error_subcode": 2332002}}))
    r = client.post("/sync", headers=AUTH)
    assert r.status_code == 502 and "facebook.com/ID" in r.json()["detail"]


@respx.mock
def test_non_eu_markets_are_skipped_with_a_warning():
    add(markets=["US", "DE"])
    route = respx.get(GRAPH).mock(return_value=httpx.Response(200, json={"data": []}))
    s = client.post("/sync", headers=AUTH).json()
    assert parse_qs(urlsplit(str(route.calls[0].request.url)).query)["ad_reached_countries"] == ['["DE"]']
    assert any("US outside the EU" in w for w in s["warnings"])
    add(name="US only", markets=["US"], meta_page_id="9")
    assert route.call_count == 1  # adding does not sync
    client.post("/sync", headers=AUTH)
    assert route.call_count == 2  # US only was not queried


@respx.mock
def test_search_by_name_keeps_only_that_page():
    add(meta_page_id=None)
    route = respx.get(GRAPH).mock(return_value=httpx.Response(200, json={"data": [
        ad(1), ad(2, page="Someone Else Coffee")]}))
    s = client.post("/sync", headers=AUTH).json()
    q = parse_qs(urlsplit(str(route.calls[0].request.url)).query)
    assert q["search_terms"] == ["Rival Beans"] and q["search_type"] == ["KEYWORD_EXACT_PHRASE"]
    assert s["seen"] == 1 and any("left out" in w for w in s["warnings"])


def test_graph_version_is_configurable(monkeypatch):
    monkeypatch.setenv("META_GRAPH_VERSION", "v27.0")
    assert main.graph_version() == "v27.0"
    monkeypatch.setenv("META_GRAPH_VERSION", "../evil")
    assert main.graph_version() == main.DEFAULT_GRAPH_VERSION


# ---------- suggestions


@respx.mock
def test_suggestions_from_our_own_sources(monkeypatch):
    monkeypatch.setenv("REVIEWS_URL", "http://reviews.test")
    monkeypatch.setenv("LISTENING_URL", "http://listen.test")
    monkeypatch.setenv("LISTENING_QUERY", "coffee subscription")
    monkeypatch.setenv("KB_URL", "http://kb.test")
    monkeypatch.setenv("OWN_DOMAINS", "ourbeans.example.coffee")
    add()  # rival.example.org is already tracked
    respx.get("http://reviews.test/reviews").mock(return_value=httpx.Response(200, json=[
        {"id": 1, "text": "Switched from beanbox.com, yours is fresher. Also tried rival.example.org"},
        {"id": 2, "text": "Better than www.beanbox.com and cheaper than ourbeans.example.coffee? no, that's you"},
        {"id": 3, "text": "Loved it. e.g. the file.py thing, Node.js, v1.2"},
    ]))
    respx.post("http://listen.test/search").mock(return_value=httpx.Response(200, json={"mentions": [
        {"source": "reddit", "url": "https://www.reddit.com/r/coffee/abc", "title": "Beanbox vs Trade?",
         "text": "trade.coffee and https://beanbox.com/plans both ok"},
        {"source": "hackernews", "url": "https://news.ycombinator.com/item?id=1", "title": "x", "text": ""},
    ]}))
    respx.post("http://kb.test/search").mock(return_value=httpx.Response(200, json={"results": [
        {"doc_id": "digest-2026-09-20", "source": "trend-digest", "chunk": "New: trade.coffee launched a decaf box"},
        {"doc_id": "competitors-x", "source": "competitor-watch", "chunk": "beanbox.com again"},
    ]}))
    r = client.post("/suggestions/scan", headers=AUTH).json()
    assert r["scanned"] == {"reviews": 3, "social": 2, "trend_digest": 1} and r["errors"] == []
    assert {n["name"]: n["mentions"] for n in r["new"]} == {"beanbox.com": 3, "trade.coffee": 2}
    sug = {s["name"]: s for s in client.get("/suggestions").json()}
    assert set(sug) == {"beanbox.com", "trade.coffee"}
    ev = sug["beanbox.com"]["evidence"]
    assert {e["source"] for e in ev} == {"reviews", "social"}
    assert all(e["snippet"] in ("Switched from beanbox.com, yours is fresher. Also tried rival.example.org",
                                "Better than www.beanbox.com and cheaper than ourbeans.example.coffee? no, that's you",
                                "https://www.reddit.com/r/coffee/abc Beanbox vs Trade? trade.coffee and https://beanbox.com/plans both ok")
               for e in ev)  # verbatim
    # Rescan: nothing new; ignore one, accept the other.
    again = client.post("/suggestions/scan", headers=AUTH).json()
    assert again["new"] == [] and len(again["updated"]) == 2
    client.post("/competitors/beanbox.com/status", json={"status": "ignored"}, headers=AUTH)
    client.post("/competitors/trade.coffee/status", json={"status": "active"}, headers=AUTH)
    third = client.post("/suggestions/scan", headers=AUTH).json()
    assert third["new"] == [] and third["updated"] == [] and third["pending"] == 0


@respx.mock
def test_suggestion_sources_fail_soft(monkeypatch):
    monkeypatch.setenv("REVIEWS_URL", "http://reviews.test")
    respx.get("http://reviews.test/reviews").mock(side_effect=httpx.ConnectError("down"))
    r = client.post("/suggestions/scan", headers=AUTH).json()
    assert r["errors"] == ["reviews (58): ConnectError"] and r["new"] == []


def test_domains_in_ignores_code_and_abbreviations():
    got = [d for d, _ in main.domains_in("see e.g. main.py, Node.js, sub.shop.co.uk and HTTPS://WWW.Acme.IO/x")]
    assert got == ["shop.co.uk", "acme.io"]
