"""Endpoints on top of synced fixture rows: health, auth, summary, budgets, pacing, alerts, mappings."""
import httpx
import respx

from . import fixtures as F
from .conftest import AUTH, SECRETS

BUDGET = {"campaign": "autumn-launch", "month": "2026-09", "amount": 3000, "currency": "eur"}


def synced(client):
    r = client.post("/sync", json={"days": 27}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def test_health_without_credentials(client, monkeypatch):
    for k in list(SECRETS) + ["META_AD_ACCOUNT_IDS", "GOOGLE_ADS_CUSTOMER_IDS", "GOOGLE_ADS_CLIENT_ID"]:
        monkeypatch.delenv(k)
    h = client.get("/health").json()
    assert h["configured"] is False and "no platform configured" in h["config_errors"][0]
    assert h["linkedin"].startswith("not implemented")
    r = client.post("/sync", headers=AUTH)
    assert r.status_code == 503 and "META_ACCESS_TOKEN" in r.json()["detail"]


def test_health_half_configured_names_the_missing_setting(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_REFRESH_TOKEN")
    h = client.get("/health").json()
    assert h["configured"] is True and h["platforms"]["google"]["missing"] == ["GOOGLE_ADS_REFRESH_TOKEN is empty"]
    assert "GOOGLE_ADS_REFRESH_TOKEN is empty" in h["config_errors"]
    for v in SECRETS.values():
        assert v not in client.get("/health").text


def test_writes_need_the_key(client, monkeypatch):
    assert client.post("/sync").status_code == 401
    assert client.post("/budgets", json=BUDGET).status_code == 401
    assert client.post("/mappings", json={"platform": "meta", "campaign_id": "1", "slug": "x"}).status_code == 401
    assert client.post("/alerts/check").status_code == 401
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/sync", headers=AUTH).status_code == 503


def test_sync_validation(client):
    assert client.post("/sync", json={"days": 0}, headers=AUTH).status_code == 422
    assert client.post("/sync", json={"days": 91}, headers=AUTH).status_code == 422
    assert client.post("/sync", json={"end_date": "2026-10-01"}, headers=AUTH).status_code == 422
    assert client.post("/sync", json={"platforms": ["linkedin"]}, headers=AUTH).status_code == 422


def test_summary_week_over_week(client, apis):
    synced(client)
    s = client.get("/summary", params={"days": 7}).json()
    assert s["window"]["current"] == {"start": "2026-09-21", "end": "2026-09-27"}
    assert s["window"]["previous"] == {"start": "2026-09-14", "end": "2026-09-20"}
    meta = next(p for p in s["platforms"] if p["platform"] == "meta")
    # Meta last week: 7 x (90 + 20) = 770; before: 7 x (80 + 20) = 700.
    assert meta["currency"] == "EUR" and meta["current"]["spend"] == 770.0 and meta["previous"]["spend"] == 700.0
    assert meta["delta_pct"]["spend"] == 10.0
    # Leads: autumn 3 x 7 + retargeting 1 on the 21st..24th = 25; the week before 4 x 7 + 7 = 35.
    assert meta["current"]["conversions"] == 25 and meta["previous"]["conversions"] == 35
    assert meta["current"]["cpl"] == 30.8 and meta["previous"]["cpl"] == 20.0 and meta["delta_pct"]["cpl"] == 54.0
    total = s["total"][0]
    assert total["currency"] == "EUR" and total["current"]["spend"] == 1050.0  # + Google 7 x 40
    camp = s["campaigns"][0]
    assert camp["key"] == "autumn-launch" and camp["platforms"] == ["google", "meta"] and camp["current"]["spend"] == 910.0
    assert s["unmapped_spend"] == {"EUR": 140.0}
    assert "meta ads: spend 770.00 EUR in the last 7 days, 700.00 EUR the 7 days before (+10.0%)" in s["facts"]
    assert any(f.startswith("all ads (EUR): spend 1,050.00 EUR") for f in s["facts"])
    assert any(f.startswith("campaign autumn-launch: spend 910.00 EUR") for f in s["facts"])


def test_summary_empty(client):
    s = client.get("/summary").json()
    assert s["has_data"] is False and s["platforms"] == [] and s["facts"] == [] and s["alerts"] == []
    assert client.get("/summary", params={"end": "27-09-2026"}).status_code == 422


def test_currencies_are_never_mixed(client, apis):
    usd = F.google_stream()
    for b in usd:
        for res in b["results"]:
            res["customer"]["currencyCode"] = "USD"
    apis.post("https://googleads.googleapis.com/v25/customers/1234567890/googleAds:searchStream").respond(json=usd)
    synced(client)
    s = client.get("/summary").json()
    assert sorted(t["currency"] for t in s["total"]) == ["EUR", "USD"]
    client.post("/budgets", json=BUDGET, headers=AUTH)
    p = client.get("/pacing").json()["budgets"][0]
    assert p["spend_to_date"] == 2230.0 and "USD is not counted" in p["warning"]


def test_budget_upsert_list_delete(client):
    r = client.post("/budgets", json=BUDGET | {"weighting": "weekday", "cpl_target": 20}, headers=AUTH)
    assert r.status_code == 200 and r.json()["currency"] == "EUR" and r.json()["weighting"] == "weekday"
    r2 = client.post("/budgets", json=BUDGET | {"amount": 3500}, headers=AUTH).json()
    assert r2["id"] == r.json()["id"] and r2["amount"] == 3500 and r2["cpl_target"] is None
    assert len(client.get("/budgets", params={"month": "2026-09"}).json()) == 1
    assert client.post("/budgets", json=BUDGET | {"month": "2026-13"}, headers=AUTH).status_code == 422
    assert client.post("/budgets", json=BUDGET | {"weekday_weights": [1, 1]}, headers=AUTH).status_code == 422
    assert client.post("/budgets", json=BUDGET | {"weighting": "fast"}, headers=AUTH).status_code == 422
    assert client.post("/budgets", json=BUDGET | {"campaign": "google:9001"}, headers=AUTH).json()["campaign"] == "google:9001"
    assert client.delete(f"/budgets/{r2['id']}", headers=AUTH).json() == {"deleted": True}
    assert client.delete(f"/budgets/{r2['id']}", headers=AUTH).status_code == 404


def test_pacing_combines_platforms_under_one_slug(client, apis):
    synced(client)
    client.post("/budgets", json=BUDGET, headers=AUTH)
    p = client.get("/pacing").json()
    assert p["as_of"] == "2026-09-27"
    b = p["budgets"][0]
    # Meta 2230 + Google 27 x 40 = 3310 vs 3000 x 27/30 = 2700 planned.
    assert b["spend_to_date"] == 3310.0 and b["expected_to_date"] == 2700.0 and b["pace"] == 1.226
    assert b["platforms"] == ["google", "meta"] and b["projected_month_spend"] == 3677.78
    # The next month's budget has nothing to pace against yet.
    client.post("/budgets", json=BUDGET | {"month": "2026-10"}, headers=AUTH)
    assert client.get("/pacing", params={"as_of": "2026-10-01"}).json()["budgets"][0]["spend_to_date"] == 0


def test_fixture_smoke_summary_and_two_alerts(client, apis):
    """The acceptance story: a summary plus exactly two alerts (overspend, spend without conversions)."""
    synced(client)
    client.post("/budgets", json=BUDGET, headers=AUTH)
    a = client.get("/alerts").json()
    rules = [(x["rule"], x["campaign"]) for x in a["alerts"]]
    assert sorted(rules) == [("overspend", "autumn-launch"), ("spend_no_conversions", "meta:23850002")]
    zero = next(x for x in a["alerts"] if x["rule"] == "spend_no_conversions")
    assert zero["value"] == 60.0 and "(amounts in EUR)" in zero["message"]
    s = client.get("/summary").json()
    assert len(s["alerts"]) == 2 and sum(f.startswith("alert ") for f in s["facts"]) == 2


def test_cpl_and_roas_targets(client, apis):
    synced(client)
    client.post("/budgets", json=BUDGET | {"amount": 3400, "cpl_target": 20, "roas_target": 5}, headers=AUTH)
    rules = {x["rule"]: x for x in client.get("/alerts").json()["alerts"]}
    assert "overspend" not in rules  # 3310 vs 3060 planned: +8%, under the 15% threshold
    # Last 7 days of autumn-launch: 910 spend, 21 + 14 = 35 conversions -> CPL 26; revenue 1400 + 1050 -> ROAS 2.69.
    assert rules["cpl_above_target"]["value"] == 26.0
    assert rules["roas_below_target"]["value"] == 2.69


def test_alert_thresholds_from_env(client, apis, monkeypatch):
    synced(client)
    client.post("/budgets", json=BUDGET, headers=AUTH)
    monkeypatch.setenv("ALERT_OVERSPEND_PCT", "30")
    monkeypatch.setenv("ALERT_ZERO_CONV_DAYS", "4")
    assert client.get("/alerts").json()["alerts"] == []


def test_alerts_check_sends_each_alert_once_per_repeat_window(client, apis):
    synced(client)
    client.post("/budgets", json=BUDGET, headers=AUTH)
    first = client.post("/alerts/check", headers=AUTH).json()
    assert len(first["new"]) == 2
    again = client.post("/alerts/check", headers=AUTH).json()
    assert len(again["alerts"]) == 2 and again["new"] == []
    later = client.post("/alerts/check", params={"as_of": "2026-10-05"}, headers=AUTH).json()
    assert later["alerts"] == []  # October: no budget, no spend rows


def test_explicit_mapping_wins(client, apis):
    synced(client)
    r = client.post("/mappings", json={"platform": "meta", "campaign_id": "23850002", "slug": "Autumn Launch"}, headers=AUTH)
    assert r.json()["slug"] == "autumn-launch"
    s = client.get("/summary").json()
    assert s["unmapped_spend"] == {} and s["campaigns"][0]["current"]["spend"] == 1050.0
    c = next(x for x in client.get("/campaigns").json()["campaigns"] if x["campaign_id"] == "23850002")
    assert c["mapped_by"] == "mapping"
    assert client.delete("/mappings/meta/23850002", headers=AUTH).json() == {"deleted": True}
    assert client.post("/mappings", json={"platform": "tiktok", "campaign_id": "1", "slug": "x"}, headers=AUTH).status_code == 422


def test_name_rule_needs_a_known_slug(client, apis):
    """Without the campaign service (45) nothing maps by name; the UTM rule still works."""
    apis.get("http://campaigns.test/campaigns").mock(side_effect=httpx.ConnectError("down"))
    b = synced(client)
    assert any("campaign-service (45) unreachable" in w for w in b["warnings"])
    camps = client.get("/campaigns").json()["campaigns"]
    assert {c["campaign_id"]: c["slug"] for c in camps} == {"23850001": None, "23850002": None, "9001": "autumn-launch"}


def test_optional_push_to_analytics_ingest(client, apis, monkeypatch):
    monkeypatch.setenv("ANALYTICS_URL", "http://analytics.test")
    up = apis.post("http://analytics.test/upload").respond(json={"rows_imported": 81})
    b = synced(client)
    assert b["analytics_rows"] == 81
    req = up.calls[0].request
    assert req.url.params["label"] == "ads" and req.headers["x-api-key"] == "internal-secret-key-123"
    lines = req.content.decode().splitlines()
    assert lines[0] == "date,channel,campaign,impressions,clicks,conversions,spend"
    assert "2026-09-27,meta_ads,autumn-launch,4000,60,3,90.0" in lines
    assert "2026-09-27,meta_ads,,1000,10,0,20.0" in lines and "2026-09-27,google_ads,autumn-launch,500,30,2,40.0" in lines


def test_no_secret_in_any_response(client, apis):
    bodies = [synced(client)]
    client.post("/budgets", json=BUDGET, headers=AUTH)
    texts = [str(bodies[0])] + [client.get(p).text for p in ("/health", "/summary", "/alerts", "/pacing", "/campaigns",
                                                             "/budgets", "/mappings")]
    for t in texts:
        for v in list(SECRETS.values()) + [F.GOOGLE_TOKEN["access_token"]]:
            assert v not in t
