"""Tests for umami-sync. Umami response shapes are copied from the Umami source at v3.4.0:

- login:     src/app/api/auth/login/route.ts -> json({ token, user: {...} }) or
             json({ requiresTwoFactor: true, partialToken }) when 2FA is on
- breakdown: src/queries/sql/breakdown/getBreakdown.ts -> rows of
             {views, visitors, visits, bounces, totaltime, <field>...}; fields are aliased by
             name, so fields=["utmSource","utmCampaign"] gives "utmSource"/"utmCampaign" (null
             when the pageview URL had no such parameter)
- events:    src/queries/sql/events/getWebsiteEvents.ts -> {data: [{id, websiteId, sessionId,
             createdAt, urlPath, urlQuery, eventType, eventName, ...}], count, page, pageSize}
https://github.com/umami-software/umami/tree/v3.4.0
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
UMAMI = "http://umami.test"
SITE = "4f2c8a51-5b8c-4f55-9a6e-2c1b7d0e9a11"
ANALYTICS = "http://analytics.test"
PASSWORD = "hunter2-very-secret"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.session-token-value"
CLOUD_KEY = "api_cloudkey_1234567890abcdef"

client = TestClient(app)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for n in ("UMAMI_API_KEY", "UMAMI_TIMEZONE"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("UMAMI_URL", UMAMI)
    monkeypatch.setenv("UMAMI_WEBSITE_ID", SITE)
    monkeypatch.setenv("UMAMI_USERNAME", "admin")
    monkeypatch.setenv("UMAMI_PASSWORD", PASSWORD)
    monkeypatch.setenv("CONVERSION_EVENT", "signup")
    monkeypatch.setenv("ANALYTICS_URL", ANALYTICS)
    monkeypatch.setenv("UMAMI_PAUSE_SECONDS", "0")
    monkeypatch.setattr(main.time, "sleep", lambda s: None)


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


def login_ok(mock):
    return mock.post(f"{UMAMI}/api/auth/login").mock(return_value=httpx.Response(200, json={
        "token": TOKEN,
        "user": {"id": "u1", "username": "admin", "role": "admin",
                 "createdAt": "2026-01-01T00:00:00.000Z", "isAdmin": True, "teams": []},
    }))


def brow(source, campaign, visits):
    return {"views": visits * 2, "visitors": visits, "visits": visits, "bounces": 0,
            "totaltime": 60 * visits, "utmSource": source, "utmCampaign": campaign}


def event(query, name="signup"):
    return {"id": "e1", "websiteId": SITE, "sessionId": "s1",
            "createdAt": "2026-09-01T10:00:00.000Z", "hostname": "example.com",
            "urlPath": "/thanks", "urlQuery": query, "referrerDomain": None,
            "pageTitle": "Thanks", "eventType": 2, "eventName": name, "hasData": False}


def page(data, count=None, page=1, size=500):
    return {"data": data, "count": len(data) if count is None else count, "page": page, "pageSize": size}


def day_of(request, tz_offset_ms=0):
    from datetime import datetime, timezone
    start = int(parse_qs(urlsplit(str(request.url)).query)["startAt"][0])
    return datetime.fromtimestamp((start + tz_offset_ms) / 1000, timezone.utc).date().isoformat()


def routes(mock, breakdown_by_day, events_by_day, upload_status=200):
    """Umami + analytics mocks keyed by day (YYYY-MM-DD)."""
    bd = mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/breakdown.*").mock(
        side_effect=lambda req: httpx.Response(200, json=breakdown_by_day.get(day_of(req), [])))
    ev = mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/events.*").mock(
        side_effect=lambda req: httpx.Response(200, json=page(events_by_day.get(day_of(req), []))))
    up = mock.post(url__startswith=f"{ANALYTICS}/upload").mock(
        side_effect=lambda req: httpx.Response(upload_status, json={"rows_imported": req.content.decode().count("\n") - 1}
                                               if upload_status == 200 else {"detail": "bad"}))
    return bd, ev, up


def sync(frm="2026-09-01", to="2026-09-01", headers=AUTH):
    return client.post("/sync", json={"from": frm, "to": to}, headers=headers)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


# ---------- auth of this service


def test_sync_needs_key(mock):
    assert sync(headers={}).status_code == 401
    assert sync(headers={"X-API-Key": "nope"}).status_code == 401


def test_sync_503_without_internal_key(monkeypatch, mock):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert sync().status_code == 503


@pytest.mark.parametrize("body", [
    {"from": "2026-09-01", "to": "2026-10-02"},   # 32 days
    {"from": "2026-09-05", "to": "2026-09-01"},   # reversed
    {"from": "2026/09/01", "to": "2026-09-02"},
    {"to": "2026-09-02"},
])
def test_bad_ranges_422(body, mock):
    assert client.post("/sync", json=body, headers=AUTH).status_code == 422


def test_31_days_is_allowed(mock):
    login_ok(mock)
    bd, ev, up = routes(mock, {}, {})
    r = sync("2026-08-01", "2026-08-31")
    assert r.status_code == 200 and r.json() == {"rows": 0, "days": 31, "errors": []}
    assert bd.call_count == 31 and ev.call_count == 31
    assert not up.called  # nothing to upload


def test_missing_umami_config_503(monkeypatch, mock):
    monkeypatch.delenv("UMAMI_WEBSITE_ID")
    monkeypatch.delenv("UMAMI_PASSWORD")
    r = sync()
    assert r.status_code == 503
    assert "UMAMI_WEBSITE_ID" in r.json()["detail"] and PASSWORD not in r.text


# ---------- the happy path


def test_login_flow_csv_and_upload_with_label(mock):
    login = login_ok(mock)
    bd, ev, up = routes(
        mock,
        {"2026-09-01": [brow("LinkedIn", "spring-launch", 40), brow(None, None, 25),
                        brow("linkedin", "spring-launch", 2),       # same after lowercasing
                        brow("newsletter", None, 7), brow("", "spring-launch", 3)],
         "2026-09-02": [brow("google", "brand-search", 11)]},
        {"2026-09-01": [event("utm_source=LinkedIn&utm_campaign=spring-launch"),
                        event("utm_source=linkedin&utm_campaign=spring-launch&x=1"),
                        event(""), event(None),
                        event("utm_source=twitter&utm_campaign=spring-launch")],  # no visits row
         "2026-09-02": []},
    )
    r = sync("2026-09-01", "2026-09-02")
    assert r.status_code == 200, r.text
    assert r.json() == {"rows": 6, "days": 2, "errors": []}

    # login: POST /api/auth/login {username, password}; then Bearer token on every call
    assert json.loads(login.calls[0].request.content) == {"username": "admin", "password": PASSWORD}
    for call in [*bd.calls, *ev.calls]:
        assert call.request.headers["authorization"] == f"Bearer {TOKEN}"

    # breakdown: startAt/endAt in ms covering the whole UTC day, fields as a JSON array
    q = parse_qs(urlsplit(str(bd.calls[0].request.url)).query)
    assert q["startAt"] == ["1788220800000"] and q["endAt"] == ["1788307199999"]
    assert json.loads(q["fields"][0]) == ["utmSource", "utmCampaign"]
    # events: filtered on the conversion event name, paged
    q = parse_qs(urlsplit(str(ev.calls[0].request.url)).query)
    assert q["event"] == ["signup"] and q["pageSize"] == ["500"] and q["page"] == ["1"]

    # upload to 20 with label=umami and the internal key
    req = up.calls[0].request
    assert parse_qs(urlsplit(str(req.url)).query) == {"source": ["generic"], "label": ["umami"]}
    assert req.headers["x-api-key"] == KEY and req.headers["content-type"] == "text/csv"
    assert req.content.decode() == (
        "date,channel,campaign,sessions,conversions\n"
        "2026-09-01,direct,,25,2\n"
        "2026-09-01,direct,spring-launch,3,0\n"
        "2026-09-01,linkedin,spring-launch,42,2\n"
        "2026-09-01,newsletter,,7,0\n"
        "2026-09-01,twitter,spring-launch,0,1\n"
        "2026-09-02,google,brand-search,11,0\n"
    )


def test_csv_quotes_campaigns_with_commas_and_counts_as_strings(mock):
    login_ok(mock)
    b = brow("x", 'sale, "big"', 0)
    b["visits"] = "12"  # ClickHouse can return counts as strings
    _, _, up = routes(mock, {"2026-09-01": [b]}, {})
    assert sync().json()["rows"] == 1
    assert up.calls[0].request.content.decode().splitlines()[1] == '2026-09-01,x,"sale, ""big""",12,0'


def test_timezone_moves_day_window(monkeypatch, mock):
    monkeypatch.setenv("UMAMI_TIMEZONE", "Europe/Berlin")
    login_ok(mock)
    bd, _, _ = routes(mock, {}, {})
    sync()
    q = parse_qs(urlsplit(str(bd.calls[0].request.url)).query)
    assert int(q["startAt"][0]) == 1788220800000 - 2 * 3600 * 1000  # 00:00 CEST = 22:00 UTC


def test_without_conversion_event_no_events_calls(monkeypatch, mock):
    monkeypatch.delenv("CONVERSION_EVENT")
    login_ok(mock)
    _, ev, up = routes(mock, {"2026-09-01": [brow("a", "b", 1)]}, {})
    assert sync().json() == {"rows": 1, "days": 1, "errors": []}
    assert not ev.called
    assert up.calls[0].request.content.decode() == "date,channel,campaign,sessions\n2026-09-01,a,b,1\n"


def test_events_paging_and_other_event_names_ignored(mock, monkeypatch):
    monkeypatch.setattr(main, "EVENT_PAGE_SIZE", 2)
    login_ok(mock)
    routes(mock, {}, {})
    pages = {1: [event("utm_source=a"), event("utm_source=a", name="other")],
             2: [event("utm_source=a")]}
    ev = mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/events.*").mock(side_effect=lambda req: httpx.Response(
        200, json=page(pages[int(parse_qs(urlsplit(str(req.url)).query)["page"][0])], count=3, size=2)))
    up = mock.post(url__startswith=f"{ANALYTICS}/upload").mock(return_value=httpx.Response(200, json={"rows_imported": 1}))
    assert sync().json()["errors"] == []
    assert ev.call_count == 2
    assert up.calls[0].request.content.decode().splitlines()[1] == "2026-09-01,a,,0,2"


def test_events_page_cap_is_reported(mock, monkeypatch):
    monkeypatch.setattr(main, "EVENT_PAGE_SIZE", 1)
    monkeypatch.setattr(main, "MAX_EVENT_PAGES", 2)
    login_ok(mock)
    routes(mock, {}, {})
    ev = mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/events.*").mock(
        return_value=httpx.Response(200, json=page([event("utm_source=a")], count=9, size=1)))
    r = sync().json()
    assert ev.call_count == 2 and r["days"] == 1 and r["rows"] == 1
    assert "only those were counted" in r["errors"][0]["error"]


def test_breakdown_row_cap_is_reported(mock, monkeypatch):
    monkeypatch.setattr(main, "BREAKDOWN_ROW_CAP", 2)
    login_ok(mock)
    routes(mock, {"2026-09-01": [brow("a", "x", 1), brow("b", "x", 1)]}, {})
    r = sync().json()
    assert r["rows"] == 2 and "cap" in r["errors"][0]["error"]


# ---------- cloud / API key flow


def test_cloud_api_key_flow_uses_cloud_base_without_login(monkeypatch, mock):
    monkeypatch.delenv("UMAMI_URL")
    monkeypatch.delenv("UMAMI_USERNAME")
    monkeypatch.delenv("UMAMI_PASSWORD")
    monkeypatch.setenv("UMAMI_API_KEY", CLOUD_KEY)
    cloud = "https://api.umami.is/v1"
    bd = mock.get(url__startswith=f"{cloud}/websites/{SITE}/breakdown").mock(
        return_value=httpx.Response(200, json=[brow("x", "c", 5)]))
    ev = mock.get(url__startswith=f"{cloud}/websites/{SITE}/events").mock(
        return_value=httpx.Response(200, json=page([event("utm_source=x&utm_campaign=c")])))
    up = mock.post(url__startswith=f"{ANALYTICS}/upload").mock(return_value=httpx.Response(200, json={"rows_imported": 1}))
    assert sync().json() == {"rows": 1, "days": 1, "errors": []}
    for call in (bd.calls[0], ev.calls[0]):
        h = call.request.headers
        assert h["authorization"] == f"Bearer {CLOUD_KEY}" and h["x-umami-api-key"] == CLOUD_KEY
    assert up.calls[0].request.content.decode().splitlines()[1] == "2026-09-01,x,c,5,1"


def test_self_hosted_api_key_keeps_api_prefix(monkeypatch, mock):
    monkeypatch.setenv("UMAMI_API_KEY", "umami_selfhostedkey")
    bd, _, _ = routes(mock, {}, {})  # routes are under {UMAMI}/api/...; no login call is mocked
    assert sync().json() == {"rows": 0, "days": 1, "errors": []}
    assert bd.calls[0].request.headers["authorization"] == "Bearer umami_selfhostedkey"


# ---------- older Umami 3.0-3.3: breakdown only as POST /api/reports/breakdown


def test_falls_back_to_legacy_breakdown_once(mock):
    login_ok(mock)
    new = mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/breakdown.*").mock(return_value=httpx.Response(404))
    legacy = mock.post(f"{UMAMI}/api/reports/breakdown").mock(
        return_value=httpx.Response(200, json=[brow("a", "c", 4)]))
    mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/events.*").mock(return_value=httpx.Response(200, json=page([])))
    mock.post(url__startswith=f"{ANALYTICS}/upload").mock(return_value=httpx.Response(200, json={"rows_imported": 2}))
    assert sync("2026-09-01", "2026-09-02").json() == {"rows": 2, "days": 2, "errors": []}
    assert new.call_count == 1 and legacy.call_count == 2
    body = json.loads(legacy.calls[0].request.content)
    assert body["websiteId"] == SITE and body["type"] == "breakdown" and body["filters"] == {}
    assert body["parameters"] == {"startDate": "2026-09-01T00:00:00.000+00:00",
                                  "endDate": "2026-09-01T23:59:59.999+00:00",
                                  "fields": ["utmSource", "utmCampaign"]}


# ---------- errors: never 500, never credentials


def test_login_failure_is_reported_not_500(mock):
    mock.post(f"{UMAMI}/api/auth/login").mock(return_value=httpx.Response(401, json={"code": "incorrect-username-password"}))
    r = sync()
    assert r.status_code == 200
    assert r.json() == {"rows": 0, "days": 0, "errors": [{"day": None, "error": "Umami login failed (HTTP 401)"}]}


def test_two_factor_login_is_reported(mock):
    mock.post(f"{UMAMI}/api/auth/login").mock(
        return_value=httpx.Response(200, json={"requiresTwoFactor": True, "partialToken": "p"}))
    r = sync().json()
    assert r["days"] == 0 and "UMAMI_API_KEY" in r["errors"][0]["error"]


def test_umami_unreachable_is_reported(mock):
    mock.post(f"{UMAMI}/api/auth/login").mock(side_effect=httpx.ConnectError("boom"))
    r = sync()
    assert r.status_code == 200 and r.json()["errors"][0]["error"] == "Umami unreachable (ConnectError)"


def test_one_bad_day_does_not_stop_the_others(mock):
    login_ok(mock)
    def bd(req):
        d = day_of(req)
        if d == "2026-09-02":
            return httpx.Response(500, text="oops")
        if d == "2026-09-03":
            return httpx.Response(200, json={"not": "a list"})
        return httpx.Response(200, json=[brow("a", "c", 1)])
    mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/breakdown.*").mock(side_effect=bd)
    mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/events.*").mock(return_value=httpx.Response(200, json=page([])))
    up = mock.post(url__startswith=f"{ANALYTICS}/upload").mock(return_value=httpx.Response(200, json={"rows_imported": 2}))
    r = sync("2026-09-01", "2026-09-04").json()
    assert r["days"] == 2 and r["rows"] == 2
    assert r["errors"] == [{"day": "2026-09-02", "error": "breakdown: Umami returned HTTP 500"},
                           {"day": "2026-09-03", "error": "breakdown: unexpected response shape"}]
    assert [ln.split(",")[0] for ln in up.calls[0].request.content.decode().splitlines()[1:]] == ["2026-09-01", "2026-09-04"]


def test_events_failure_skips_that_day_entirely(mock):
    login_ok(mock)
    routes(mock, {"2026-09-01": [brow("a", "c", 1)]}, {})
    mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/events.*").mock(return_value=httpx.Response(403))
    r = sync().json()
    assert r == {"rows": 0, "days": 0, "errors": [{"day": "2026-09-01", "error": "events: Umami returned HTTP 403"}]}


def test_rate_limit_429_is_retried_once(mock, monkeypatch):
    slept = []
    monkeypatch.setattr(main.time, "sleep", slept.append)
    login_ok(mock)
    routes(mock, {}, {})
    bd = mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/breakdown.*").mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "3"}), httpx.Response(200, json=[])])
    assert sync().json()["errors"] == []
    assert bd.call_count == 2 and 3.0 in slept


def test_pause_between_calls(mock, monkeypatch):
    slept = []
    monkeypatch.setattr(main.time, "sleep", slept.append)
    monkeypatch.setenv("UMAMI_PAUSE_SECONDS", "0.25")
    login_ok(mock)
    routes(mock, {}, {})
    sync("2026-09-01", "2026-09-02")
    assert slept == [0.25] * 4  # login + 2 x (breakdown, events) = 5 calls, pause before 4


@pytest.mark.parametrize("status", [401, 422, 503])
def test_upload_failure_is_reported(mock, status):
    login_ok(mock)
    routes(mock, {"2026-09-01": [brow("a", "c", 1)]}, {}, upload_status=status)
    r = sync()
    assert r.status_code == 200 and r.json()["rows"] == 0 and r.json()["days"] == 1
    assert f"HTTP {status}" in r.json()["errors"][0]["error"]


def test_analytics_unreachable_is_reported(mock):
    login_ok(mock)
    routes(mock, {"2026-09-01": [brow("a", "c", 1)]}, {})
    mock.post(url__startswith=f"{ANALYTICS}/upload").mock(side_effect=httpx.ConnectError("x"))
    assert sync().json()["errors"] == [{"day": None, "error": "analytics-ingest unreachable (ConnectError)"}]


def test_credentials_never_returned_or_logged(mock, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    login_ok(mock)
    # every failure path echoes as much as it can; none of it may carry a secret
    mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/breakdown.*").mock(side_effect=lambda req: httpx.Response(
        200 if day_of(req) == "2026-09-01" else 500, json=[brow("a", "c", 1)]))
    mock.get(url__regex=rf"{UMAMI}/api/websites/{SITE}/events.*").mock(return_value=httpx.Response(200, json=page([])))
    mock.post(url__startswith=f"{ANALYTICS}/upload").mock(
        return_value=httpx.Response(401, text=f"echo {KEY} {PASSWORD} {TOKEN}"))
    r = sync("2026-09-01", "2026-09-02")
    blob = r.text + caplog.text
    assert r.status_code == 200 and "echo *** ***" in blob
    for secret in (KEY, PASSWORD, TOKEN):
        assert secret not in blob

    caplog.clear()
    monkeypatch.setenv("UMAMI_API_KEY", CLOUD_KEY)
    mock.post(url__startswith=f"{ANALYTICS}/upload").mock(return_value=httpx.Response(500, text=CLOUD_KEY))
    r = sync()
    assert CLOUD_KEY not in r.text + caplog.text
