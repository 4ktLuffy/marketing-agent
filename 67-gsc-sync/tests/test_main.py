"""Tests for gsc-sync. No real Google call is made: respx mocks the token endpoint and
searchanalytics.query. The service-account key is an RSA key generated per test session.

Response shapes follow the Search Console API reference (searchanalytics.query):
{"rows": [{"keys": [...], "clicks", "impressions", "ctr", "position"}],
 "responseAggregationType": "byPage"}; no "rows" key when there is no data.
Errors follow Google's JSON error format: {"error": {"code", "message", "status", "errors"}}.
"""
import json
import logging
from datetime import date
from types import SimpleNamespace
from urllib.parse import parse_qs, quote

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import main
from app.main import app

KEY = "internal-secret-key"
AUTH = {"X-API-Key": KEY}
SITE = "sc-domain:example.com"
EMAIL = "gsc-reader@test-project.iam.gserviceaccount.com"
TOKEN = "ya29.test-access-token-value"
QUERY_URL = f"{main.API}/sites/{quote(SITE, safe='')}/searchAnalytics/query"
ANALYTICS = "http://analytics.test"

client = TestClient(app)


@pytest.fixture(scope="session")
def rsa_key():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    return pem, k.public_key()


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path, rsa_key):
    creds = {"type": "service_account", "project_id": "test-project",
             "private_key_id": "kid123", "private_key": rsa_key[0], "client_email": EMAIL,
             "client_id": "1", "token_uri": "https://evil.test/token"}
    path = tmp_path / "gsc.json"
    path.write_text(json.dumps(creds))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("GSC_SITE_URL", SITE)
    monkeypatch.setenv("GSC_CREDENTIALS_FILE", str(path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "gsc.sqlite"))
    monkeypatch.delenv("ANALYTICS_URL", raising=False)
    main._token_cache.clear()
    yield
    main._token_cache.clear()


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


def token_ok(mock, token=TOKEN, ttl=3599):
    return mock.post(main.TOKEN_URL).mock(return_value=httpx.Response(
        200, json={"access_token": token, "expires_in": ttl, "token_type": "Bearer"}))


def row(keys, clicks, impressions, position):
    return {"keys": keys, "clicks": clicks, "impressions": impressions,
            "ctr": clicks / impressions if impressions else 0, "position": position}


# Two windows of fixture data, keyed by (window, dimensions)
P = "https://example.com/"
DATA = {
    ("current", "page"): [row([P + "a"], 30, 1000, 6.0), row([P + "b"], 50, 900, 3.0),
                          row([P + "new"], 40, 500, 8.0), row([P + "d"], 19, 300, 9.0)],
    ("previous", "page"): [row([P + "a"], 100, 1200, 4.0), row([P + "b"], 55, 950, 3.0),
                           row([P + "gone"], 25, 400, 7.0), row([P + "d"], 30, 350, 8.0),
                           row([P + "small"], 10, 100, 5.0)],
    ("current", "query"): [row(["coffee beans"], 20, 2000, 8.4), row(["espresso"], 5, 150, 12.0),
                           row(["best grinder"], 1, 90, 7.0), row(["decaf"], 40, 800, 2.1),
                           row(["pour over"], 0, 500, 25.0)],
    ("previous", "query"): [row(["coffee beans"], 30, 1800, 6.0)],
    ("current", "page,query"): [row([P + "a", "coffee beans"], 15, 1500, 9.0),
                                row([P + "b", "coffee beans"], 5, 500, 7.0),
                                row([P + "a", "espresso"], 5, 150, 12.0),
                                row([P + "a", "decaf"], 10, 300, 3.0)],
    ("previous", "page,query"): [row([P + "a", "coffee beans"], 60, 1400, 4.0),
                                 row([P + "a", "decaf"], 30, 400, 3.0),
                                 row([P + "a", "latte"], 5, 100, 6.0),
                                 row([P + "a", "mocha"], 3, 50, 6.0),
                                 row([P + "a", "cortado"], 2, 40, 6.0),
                                 row([P + "a", "flat white"], 1, 30, 6.0),
                                 row([P + "gone", "old query"], 25, 400, 7.0)],
}


def gsc_routes(mock, data=DATA, end="2026-09-20", days=28):
    wins = main.windows(days, date.fromisoformat(end))
    by_start = {s.isoformat(): w for w, (s, _) in wins.items()}
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        win = by_start.get(body["startDate"])
        rows = data.get((win, ",".join(body["dimensions"])), [])
        rows = rows[body["startRow"]:body["startRow"] + body["rowLimit"]]
        out = {"responseAggregationType": "byPage"}
        if rows:
            out["rows"] = rows
        return httpx.Response(200, json=out)

    route = mock.post(QUERY_URL).mock(side_effect=handler)
    return route, bodies


def do_sync(body=None, headers=AUTH):
    return client.post("/sync", json=body if body is not None else {"days": 28, "end_date": "2026-09-20"},
                       headers=headers)


def synced(mock):
    token_ok(mock)
    gsc_routes(mock)
    r = do_sync()
    assert r.status_code == 200, r.text
    return r.json()


# ---------- window math


def test_windows_are_consecutive_and_equal_length():
    w = main.windows(28, date(2026, 9, 20))
    assert w["current"] == (date(2026, 8, 24), date(2026, 9, 20))
    assert w["previous"] == (date(2026, 7, 27), date(2026, 8, 23))
    assert (w["current"][1] - w["current"][0]).days + 1 == 28
    one = main.windows(1, date(2026, 3, 1))
    assert one == {"current": (date(2026, 3, 1), date(2026, 3, 1)),
                   "previous": (date(2026, 2, 28), date(2026, 2, 28))}


def test_default_end_date_is_three_days_ago(mock, monkeypatch):
    monkeypatch.setattr(main, "today", lambda: date(2026, 9, 24))
    token_ok(mock)
    route, bodies = gsc_routes(mock, end="2026-09-21", days=7)
    r = do_sync({"days": 7})
    assert r.status_code == 200
    assert r.json()["windows"] == {"current": {"start": "2026-09-15", "end": "2026-09-21"},
                                   "previous": {"start": "2026-09-08", "end": "2026-09-14"}}
    assert {(b["startDate"], b["endDate"]) for b in bodies} == {
        ("2026-09-15", "2026-09-21"), ("2026-09-08", "2026-09-14")}


@pytest.mark.parametrize("body", [{"days": 0}, {"days": 91}, {"days": 7, "end_date": "2026/09/01"},
                                  {"days": 7, "end_date": "2999-01-01"}])
def test_bad_sync_body_422(body, mock):
    assert do_sync(body).status_code == 422


# ---------- token flow


def test_token_flow_and_request_shape(mock, rsa_key):
    tok = token_ok(mock)
    route, bodies = gsc_routes(mock)
    r = do_sync()
    assert r.status_code == 200
    assert tok.call_count == 1  # cached across all six queries
    assert route.call_count == 6
    form = parse_qs(tok.calls[0].request.content.decode())
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    claims = jwt.decode(form["assertion"][0], rsa_key[1], algorithms=["RS256"], audience=main.TOKEN_URL)
    assert claims["iss"] == EMAIL and claims["scope"] == main.SCOPE
    assert claims["exp"] - claims["iat"] == 3600
    assert jwt.get_unverified_header(form["assertion"][0])["kid"] == "kid123"
    # token_uri from the file is ignored: only Google's endpoint gets the assertion
    assert str(tok.calls[0].request.url) == main.TOKEN_URL
    assert {tuple(b["dimensions"]) for b in bodies} == {("page",), ("query",), ("page", "query")}
    for b in bodies:
        assert b["rowLimit"] == 5000 and b["dataState"] == "final" and b["type"] == "web"
        assert b["startRow"] == 0


def test_token_cached_until_expiry(mock, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr(main, "time", SimpleNamespace(time=lambda: now[0]))
    tok = token_ok(mock, ttl=3600)
    gsc_routes(mock)
    do_sync()
    do_sync()
    assert tok.call_count == 1
    now[0] += 3600 - 30  # inside the 60 s safety margin -> refresh
    do_sync()
    assert tok.call_count == 2


def test_401_from_api_refreshes_token_once(mock):
    tok = token_ok(mock)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials"}})
        return httpx.Response(200, json={})

    mock.post(QUERY_URL).mock(side_effect=handler)
    assert do_sync().status_code == 200
    assert tok.call_count == 2


def test_invalid_grant_is_502_with_hint(mock):
    mock.post(main.TOKEN_URL).mock(return_value=httpx.Response(
        400, json={"error": "invalid_grant", "error_description": "Invalid JWT Signature."}))
    r = do_sync()
    assert r.status_code == 502
    assert "invalid_grant" in r.json()["detail"] and "clock" in r.json()["detail"]


def test_pagination_reads_past_5000_rows(mock):
    big = [row([f"q{i}"], 1, 200, 10.0) for i in range(5003)]
    token_ok(mock)
    route, bodies = gsc_routes(mock, {**DATA, ("current", "query"): big})
    r = do_sync()
    assert r.json()["rows"]["current"]["query"] == 5003
    starts = sorted(b["startRow"] for b in bodies if b["dimensions"] == ["query"] and
                    b["startDate"] == "2026-08-24")
    assert starts == [0, 5000]


# ---------- Google errors


def test_403_not_added_is_502_with_helpful_message(mock):
    token_ok(mock)
    mock.post(QUERY_URL).mock(return_value=httpx.Response(403, json={"error": {
        "code": 403, "status": "PERMISSION_DENIED",
        "message": f"User does not have sufficient permission for site '{SITE}'. See also: "
                   "https://support.google.com/webmasters/answer/2451999."}}))
    r = do_sync()
    assert r.status_code == 502
    d = r.json()["detail"]
    assert EMAIL in d and "Users and permissions" in d and "sc-domain:" in d
    assert TOKEN not in d


def test_403_api_disabled_says_enable_it(mock):
    token_ok(mock)
    mock.post(QUERY_URL).mock(return_value=httpx.Response(403, json={"error": {
        "code": 403, "status": "PERMISSION_DENIED",
        "message": "Google Search Console API has not been used in project 1 before or it is disabled."}}))
    r = do_sync()
    assert r.status_code == 502 and "Enable 'Google Search Console API'" in r.json()["detail"]


def test_failed_sync_keeps_previous_data(mock):
    synced(mock)
    mock.post(QUERY_URL).mock(return_value=httpx.Response(500, json={"error": {"message": "backend"}}))
    assert do_sync().status_code == 502
    assert client.get("/pages/declining").status_code == 200


# ---------- auth and config


def test_wrong_key_401(mock):
    assert do_sync(headers={}).status_code == 401
    assert do_sync(headers={"X-API-Key": "nope"}).status_code == 401


def test_no_internal_key_503(monkeypatch, mock):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert do_sync().status_code == 503


def test_missing_credentials_file(monkeypatch, tmp_path, mock):
    monkeypatch.setenv("GSC_CREDENTIALS_FILE", str(tmp_path / "nope.json"))
    h = client.get("/health").json()
    assert h["configured"] is False and h["service_account"] is None
    assert any("not found" in e for e in h["config_errors"])
    r = do_sync()
    assert r.status_code == 503 and "GSC_CREDENTIALS_FILE" in r.json()["detail"]


def test_not_a_service_account_file(monkeypatch, tmp_path, mock):
    p = tmp_path / "user.json"
    p.write_text(json.dumps({"type": "authorized_user", "refresh_token": "1//secret-refresh"}))
    monkeypatch.setenv("GSC_CREDENTIALS_FILE", str(p))
    r = do_sync()
    assert r.status_code == 503 and "secret-refresh" not in r.text
    assert client.get("/health").json()["configured"] is False


def test_missing_site_503(monkeypatch, mock):
    monkeypatch.delenv("GSC_SITE_URL")
    assert do_sync().status_code == 503
    assert client.get("/health").json()["configured"] is False
    assert client.get("/pages/declining").status_code == 503


def test_reads_before_sync_409(mock):
    assert client.get("/pages/declining").status_code == 409
    assert client.get("/queries/opportunities").status_code == 409


def test_no_secret_in_health_or_logs(mock, caplog, rsa_key):
    caplog.set_level(logging.DEBUG)
    synced(mock)
    mock.post(QUERY_URL).mock(return_value=httpx.Response(403, json={"error": {"message": "denied"}}))
    do_sync()
    h = client.get("/health")
    assert h.json()["configured"] is True and h.json()["site"] == SITE
    assert h.json()["service_account"] == EMAIL
    assert h.json()["last_sync"]["current"] == {"start": "2026-08-24", "end": "2026-09-20"}
    body_marker = rsa_key[0].splitlines()[1]  # a line of the private key
    for text in (h.text, caplog.text):
        assert KEY not in text and TOKEN not in text and body_marker not in text
        assert "PRIVATE KEY" not in text


# ---------- declining pages


def test_declining_pages(mock):
    synced(mock)
    r = client.get("/pages/declining")  # min_clicks=20, drop=0.3
    assert r.status_code == 200
    pages = {p["page"]: p for p in r.json()["pages"]}
    # a: 100 -> 30 (0.7); gone: 25 -> 0 (1.0); d: 30 -> 19 (0.3667)
    # b: 55 -> 50 (0.09, too small); new: no previous clicks; small: below min_clicks
    assert set(pages) == {P + "a", P + "gone", P + "d"}
    assert [p["page"] for p in r.json()["pages"]] == [P + "a", P + "gone", P + "d"]  # by clicks lost
    a = pages[P + "a"]
    assert a["drop"] == 0.7 and a["clicks_lost"] == 70
    assert a["previous"] == {"clicks": 100, "impressions": 1200, "ctr": 0.0833, "position": 4.0}
    assert a["current"]["clicks"] == 30 and a["current"]["position"] == 6.0
    assert [q["query"] for q in a["top_queries"]] == ["coffee beans", "decaf", "latte", "mocha", "cortado"]
    beans = a["top_queries"][0]
    assert beans["previous"]["clicks"] == 60 and beans["current"]["clicks"] == 15
    assert a["top_queries"][2]["current"] == {"clicks": 0, "impressions": 0, "ctr": None, "position": None}
    gone = pages[P + "gone"]
    assert gone["drop"] == 1.0 and gone["current"]["clicks"] == 0 and gone["current"]["position"] is None


def test_declining_thresholds(mock):
    synced(mock)
    assert {p["page"] for p in client.get("/pages/declining?drop=0.8").json()["pages"]} == {P + "gone"}
    assert {p["page"] for p in client.get("/pages/declining?min_clicks=5&drop=0.05").json()["pages"]} == {
        P + "a", P + "b", P + "gone", P + "d", P + "small"}
    assert client.get("/pages/declining?drop=0").status_code == 422
    assert client.get("/pages/declining?min_clicks=0").status_code == 422


def test_new_pages_never_declining(mock):
    data = {("current", "page"): [row([P + "new"], 50, 500, 3.0)], ("previous", "page"): []}
    token_ok(mock)
    gsc_routes(mock, data)
    assert do_sync().status_code == 200
    assert client.get("/pages/declining?min_clicks=1&drop=0.01").json()["pages"] == []


# ---------- opportunities


def test_opportunities(mock):
    synced(mock)
    r = client.get("/queries/opportunities")  # impressions >= 100, 5 <= position <= 20
    qs = r.json()["queries"]
    # decaf ranks 2.1 (too high), best grinder has 90 impressions, pour over ranks 25
    assert [q["query"] for q in qs] == ["coffee beans", "espresso"]
    beans = qs[0]
    assert beans["impressions"] == 2000 and beans["position"] == 8.4 and beans["previous_position"] == 6.0
    assert beans["best_page"]["page"] == P + "a" and beans["best_page"]["clicks"] == 15
    assert qs[1]["previous_position"] is None
    wide = client.get("/queries/opportunities?min_impressions=50&max_position=30&min_position=1").json()
    assert {q["query"] for q in wide["queries"]} == {"coffee beans", "espresso", "best grinder",
                                                    "decaf", "pour over"}
    assert next(q for q in wide["queries"] if q["query"] == "pour over")["best_page"] is None
    assert client.get("/queries/opportunities?min_position=21&max_position=20").status_code == 422


# ---------- one page


def test_queries_for_one_page(mock):
    synced(mock)
    r = client.get(f"/pages/{quote(P + 'a', safe='')}/queries")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["page"] == P + "a" and j["current"]["clicks"] == 30 and j["previous"]["clicks"] == 100
    assert [q["query"] for q in j["queries"]] == ["coffee beans", "decaf", "espresso"]
    prev = client.get(f"/pages/{quote(P + 'a', safe='')}/queries?window=previous&limit=2").json()
    assert [q["query"] for q in prev["queries"]] == ["coffee beans", "decaf"]
    assert client.get(f"/pages/{quote(P + 'zzz', safe='')}/queries").status_code == 404


# ---------- optional push to 20


def test_push_daily_totals_to_analytics(monkeypatch, mock):
    monkeypatch.setenv("ANALYTICS_URL", ANALYTICS)
    token_ok(mock)
    daily = [row(["2026-09-20"], 12, 400, 5.0), row(["2026-07-27"], 9, 300, 6.0)]
    route, bodies = gsc_routes(mock)
    base = route.side_effect

    def handler(req):
        if json.loads(req.content)["dimensions"] == ["date"]:
            return httpx.Response(200, json={"rows": daily})
        return base(req)

    route.side_effect = handler
    up = mock.post(url__startswith=f"{ANALYTICS}/upload").mock(
        return_value=httpx.Response(200, json={"rows_imported": 2}))
    r = do_sync()
    assert r.status_code == 200 and r.json()["analytics_rows"] == 2
    req = up.calls[0].request
    assert req.url.params["label"] == "gsc" and req.url.params["source"] == "generic"
    assert req.headers["x-api-key"] == KEY
    assert req.content.decode() == ("date,channel,campaign,impressions,clicks\n"
                                    "2026-07-27,google-search,,300,9\n2026-09-20,google-search,,400,12\n")


def test_push_failure_is_a_warning(monkeypatch, mock):
    monkeypatch.setenv("ANALYTICS_URL", ANALYTICS)
    token_ok(mock)
    gsc_routes(mock, {**DATA, ("previous", "date"): [row(["2026-08-01"], 1, 2, 3.0)]})
    mock.post(url__startswith=f"{ANALYTICS}/upload").mock(return_value=httpx.Response(500, text="boom"))
    r = do_sync()
    assert r.status_code == 200 and r.json()["analytics_rows"] is None
    assert "HTTP 500" in r.json()["warnings"][0]


def test_no_push_without_analytics_url(mock):
    j = synced(mock)
    assert j["analytics_rows"] is None and j["calls"] == 6
    assert client.get("/health").json()["analytics_push"] is False
