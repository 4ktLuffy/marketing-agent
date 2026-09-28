"""Meta and Google through /sync with respx: paging, normalization, errors, rate limits, secrets."""
import json
import logging

import httpx
import respx

from app import main

from . import fixtures as F
from .conftest import AUTH, CAMP, GOOGLE_URL, META_URL, SECRETS, TOKEN_URL, meta_route


def only(monkeypatch, platform):
    drop = {"meta": ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_ADS_CUSTOMER_IDS",
                     "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET"),
            "google": ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_IDS")}[platform]
    for k in drop:
        monkeypatch.delenv(k)


def assert_no_secret(text):
    for v in list(SECRETS.values()) + [F.GOOGLE_TOKEN["access_token"], "internal-secret-key-123"]:
        assert v not in text, v


def test_meta_paging_normalization_and_auth_header(client, monkeypatch):
    only(monkeypatch, "meta")
    with respx.mock(assert_all_mocked=True) as mock:
        route = meta_route(mock, F.meta_pages())
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", json={"days": 27}, headers=AUTH)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["window"] == {"start": "2026-09-01", "end": "2026-09-27"}
    assert b["platforms"]["meta"]["ok"] and b["platforms"]["meta"]["rows"] == 54
    assert b["platforms"]["meta"]["calls"] == 2 and b["platforms"]["meta"]["rate_usage_pct"] == 12
    assert route.call_count == 2
    first = route.calls[0].request
    q = dict(httpx.QueryParams(first.url.query))
    assert q["level"] == "campaign" and q["time_increment"] == "1"
    assert json.loads(q["time_range"]) == {"since": "2026-09-01", "until": "2026-09-27"}
    assert {"spend", "actions", "action_values", "account_currency"} <= set(q["fields"].split(","))
    # The token goes in the Authorization header, never in the URL.
    assert first.headers["authorization"] == f"Bearer {SECRETS['META_ACCESS_TOKEN']}"
    assert "access_token" not in str(first.url) and SECRETS["META_ACCESS_TOKEN"] not in str(route.calls[1].request.url)
    c = client.get("/campaigns", params={"days": 27}).json()["campaigns"]
    auto = next(x for x in c if x["campaign_id"] == "23850001")
    # 20 days x 80 + 7 x 90; leads: only "lead" counted, not the overlapping pixel lead.
    assert auto["spend"] == 2230.0 and auto["conversions"] == 101 and auto["revenue"] == 5400.0
    assert auto["slug"] == "autumn-launch" and auto["mapped_by"] == "name" and auto["currency"] == "EUR"
    assert auto["cpl"] == round(2230 / 101, 2) and auto["roas"] == round(5400 / 2230, 2)
    retarget = next(x for x in c if x["campaign_id"] == "23850002")
    assert retarget["slug"] is None and retarget["key"] == "meta:23850002"


def test_meta_conversion_and_revenue_actions_are_configurable(client, monkeypatch):
    only(monkeypatch, "meta")
    monkeypatch.setenv("META_CONVERSION_ACTIONS", "offsite_conversion.fb_pixel_lead")
    monkeypatch.setenv("META_REVENUE_ACTIONS", "purchase")
    with respx.mock() as mock:
        meta_route(mock, F.meta_pages())
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        client.post("/sync", json={"days": 27}, headers=AUTH)
    auto = next(x for x in client.get("/campaigns", params={"days": 27}).json()["campaigns"] if x["campaign_id"] == "23850001")
    assert auto["conversions"] == 101 and auto["revenue"] == 0


def test_meta_adset_level_is_stored_separately(client, monkeypatch):
    only(monkeypatch, "meta")
    adset = {"data": [{"date_start": "2026-09-27", "date_stop": "2026-09-27", "account_currency": "EUR",
                       "campaign_id": "23850001", "campaign_name": "Autumn Launch | Prospecting",
                       "adset_id": "777", "adset_name": "Lookalike 1%", "spend": "45.00", "impressions": "2000",
                       "clicks": "30"}]}
    camp = {"data": F.meta_rows(F.AS_OF, F.AS_OF)}

    def handler(request):
        lv = request.url.params.get("level")
        return httpx.Response(200, json=adset if lv == "adset" else camp)
    with respx.mock() as mock:
        mock.get(META_URL).mock(side_effect=handler)
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", json={"days": 1, "meta_levels": ["adset"]}, headers=AUTH)
    assert r.json()["platforms"]["meta"]["rows"] == 3  # 2 campaigns + 1 adset
    s = client.get("/summary", params={"days": 1}).json()
    assert s["total"][0]["current"]["spend"] == 110.0  # campaign level only: the adset is not double-counted


def test_meta_expired_token_is_a_clear_502(client, monkeypatch, caplog):
    only(monkeypatch, "meta")
    err = {"error": {"message": f"Error validating access token: Session has expired. token={SECRETS['META_ACCESS_TOKEN']}",
                     "type": "OAuthException", "code": 190, "error_subcode": 463, "fbtrace_id": "x"}}
    caplog.set_level(logging.DEBUG)
    with respx.mock() as mock:
        mock.get(META_URL).respond(400, json=err)
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", headers=AUTH)
    assert r.status_code == 502
    d = r.json()["detail"]
    assert "META_ACCESS_TOKEN" in d and "expired" in d and "190/463" in d and "System User" in d
    assert_no_secret(r.text)
    assert_no_secret(caplog.text)


def test_meta_rate_limit_returns_retry_after_from_buc_header(client, monkeypatch):
    only(monkeypatch, "meta")
    err = {"error": {"message": "User request limit reached", "code": 80000, "error_subcode": 2446079}}
    buc = json.dumps({"111": [{"type": "ads_insights", "call_count": 100, "total_cputime": 40, "total_time": 60,
                               "estimated_time_to_regain_access": 7}]})
    with respx.mock() as mock:
        route = mock.get(META_URL).respond(400, json=err, headers={"x-business-use-case-usage": buc})
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", headers=AUTH)
    assert r.status_code == 429 and r.headers["retry-after"] == "420"  # 7 minutes
    assert "rate limit" in r.json()["detail"] and route.call_count == 1  # 420 s is too long to wait here


def test_meta_short_rate_limit_is_retried_once(client, monkeypatch):
    only(monkeypatch, "meta")
    err = {"error": {"message": "Application request limit reached", "code": 4}}
    with respx.mock() as mock:
        route = mock.get(META_URL).mock(side_effect=[
            httpx.Response(400, json=err, headers={"retry-after": "2"}),
            httpx.Response(200, json={"data": F.meta_rows(F.AS_OF, F.AS_OF)})])
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", json={"days": 1}, headers=AUTH)
    assert r.status_code == 200 and route.call_count == 2 and r.json()["platforms"]["meta"]["rows"] == 2


def test_meta_permission_error(client, monkeypatch):
    only(monkeypatch, "meta")
    with respx.mock() as mock:
        mock.get(META_URL).respond(403, json={"error": {"message": "(#200) Requires ads_read permission", "code": 200}})
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", headers=AUTH)
    assert r.status_code == 502 and "ads_read" in r.json()["detail"]


def test_meta_paging_link_off_host_is_not_followed(client, monkeypatch):
    only(monkeypatch, "meta")
    page = {"data": F.meta_rows(F.AS_OF, F.AS_OF), "paging": {"next": "https://evil.example.com/steal"}}
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(META_URL).respond(json=page)
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", json={"days": 1}, headers=AUTH)
    assert r.status_code == 200 and any("paging link" in w for w in r.json()["warnings"])


def test_google_stream_headers_query_and_utm_mapping(client, monkeypatch):
    only(monkeypatch, "google")
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "999-888-7777")
    with respx.mock(assert_all_mocked=True) as mock:
        tok = mock.post(TOKEN_URL).respond(json=F.GOOGLE_TOKEN)
        ads = mock.post(GOOGLE_URL).respond(json=F.google_stream())
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", json={"days": 27}, headers=AUTH)
        r2 = client.post("/sync", json={"days": 27}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["platforms"]["google"]["rows"] == 27 and r2.status_code == 200
    assert tok.call_count == 1  # the access token is cached until shortly before it expires
    form = dict(httpx.QueryParams(tok.calls[0].request.content.decode()))
    assert form["grant_type"] == "refresh_token" and form["refresh_token"] == SECRETS["GOOGLE_ADS_REFRESH_TOKEN"]
    req = ads.calls[0].request
    assert req.headers["developer-token"] == SECRETS["GOOGLE_ADS_DEVELOPER_TOKEN"]
    assert req.headers["login-customer-id"] == "9998887777"
    assert req.headers["authorization"] == "Bearer " + F.GOOGLE_TOKEN["access_token"]
    q = json.loads(req.content)["query"]
    assert "metrics.cost_micros" in q and "FROM campaign" in q and "BETWEEN '2026-09-01' AND '2026-09-27'" in q
    c = client.get("/campaigns", params={"days": 27}).json()["campaigns"][0]
    assert c["spend"] == 1080.0 and c["conversions"] == 54 and c["revenue"] == 4050.0  # micros / 1e6
    assert c["slug"] == "autumn-launch" and c["mapped_by"] == "utm_campaign"


def test_google_invalid_grant_is_a_clear_502(client, monkeypatch, caplog):
    only(monkeypatch, "google")
    caplog.set_level(logging.DEBUG)
    with respx.mock() as mock:
        mock.post(TOKEN_URL).respond(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", headers=AUTH)
    assert r.status_code == 502
    d = r.json()["detail"]
    assert "GOOGLE_ADS_REFRESH_TOKEN" in d and "invalid_grant" in d and "7 days" in d
    assert_no_secret(r.text)
    assert_no_secret(caplog.text)


def test_google_401_retries_once_with_a_fresh_token(client, monkeypatch):
    only(monkeypatch, "google")
    with respx.mock() as mock:
        tok = mock.post(TOKEN_URL).respond(json=F.GOOGLE_TOKEN)
        ads = mock.post(GOOGLE_URL).mock(side_effect=[
            httpx.Response(401, json=[{"error": {"code": 401, "status": "UNAUTHENTICATED", "message": "expired"}}]),
            httpx.Response(200, json=F.google_stream(F.AS_OF, F.AS_OF))])
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", json={"days": 1}, headers=AUTH)
    assert r.status_code == 200 and ads.call_count == 2 and tok.call_count == 2


def test_google_quota_returns_retry_after(client, monkeypatch):
    only(monkeypatch, "google")
    body = [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Too many requests.",
                       "details": [{"@type": "type.googleapis.com/google.ads.googleads.v25.errors.GoogleAdsFailure",
                                    "errors": [{"errorCode": {"quotaError": "RESOURCE_EXHAUSTED"},
                                                "details": {"quotaErrorDetails": {"rateScope": "DEVELOPER",
                                                                                  "retryDelay": "60s"}}}]}]}}]
    with respx.mock() as mock:
        mock.post(TOKEN_URL).respond(json=F.GOOGLE_TOKEN)
        ads = mock.post(GOOGLE_URL).respond(429, json=body)
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", headers=AUTH)
    assert r.status_code == 429 and r.headers["retry-after"] == "60" and ads.call_count == 1


def test_google_developer_token_not_approved(client, monkeypatch):
    only(monkeypatch, "google")
    body = [{"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "The caller does not have permission",
                       "details": [{"errors": [{"errorCode": {"authorizationError": "DEVELOPER_TOKEN_NOT_APPROVED"}}]}]}}]
    with respx.mock() as mock:
        mock.post(TOKEN_URL).respond(json=F.GOOGLE_TOKEN)
        mock.post(GOOGLE_URL).respond(403, json=body)
        mock.get(f"{CAMP}/campaigns").respond(json=F.CAMPAIGNS_45)
        r = client.post("/sync", headers=AUTH)
    assert r.status_code == 502 and "Basic access" in r.json()["detail"]


def test_one_platform_failing_keeps_the_other(client, apis):
    apis.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})
    r = client.post("/sync", json={"days": 27}, headers=AUTH)
    assert r.status_code == 200
    p = r.json()["platforms"]
    assert p["meta"]["ok"] and not p["google"]["ok"] and "invalid_grant" in p["google"]["error"]


def test_failed_sync_keeps_old_rows(client, apis, monkeypatch):
    client.post("/sync", json={"days": 27}, headers=AUTH)
    before = client.get("/summary").json()["total"]
    apis.get(META_URL).respond(400, json={"error": {"code": 190, "message": "expired"}})
    apis.post(GOOGLE_URL).respond(500, json=[{"error": {"code": 500, "status": "INTERNAL"}}])
    r = client.post("/sync", json={"days": 27}, headers=AUTH)
    assert r.status_code == 502
    assert client.get("/summary").json()["total"] == before


def test_unreachable_api(client, monkeypatch):
    only(monkeypatch, "meta")
    with respx.mock() as mock:
        mock.get(META_URL).mock(side_effect=httpx.ConnectError("boom"))
        mock.get(f"{CAMP}/campaigns").mock(side_effect=httpx.ConnectError("boom"))
        r = client.post("/sync", headers=AUTH)
    assert r.status_code == 502 and "unreachable" in r.json()["detail"]
