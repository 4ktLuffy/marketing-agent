from datetime import date
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.main import TRANSITIONS, app

KEY = "test-key"
AUTH = {"X-API-Key": KEY}
SHORT = "http://shortener.test"
ANALYTICS = "http://analytics.test"
CAL = "http://calendar.test"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "campaigns.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("SHORTENER_URL", SHORT)
    monkeypatch.setenv("ANALYTICS_URL", ANALYTICS)
    monkeypatch.setenv("CALENDAR_URL", CAL)
    set_today(monkeypatch, date(2026, 10, 11))


@pytest.fixture
def mock():
    # assert_all_mocked: any unmocked request fails instead of reaching the network
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


def set_today(monkeypatch, d):
    monkeypatch.setattr(main, "today", lambda: d)


client = TestClient(app)


def payload(**kw):
    return {
        "name": "Autumn Launch 2026",
        "goal_type": "traffic",
        "goal_text": "Drive visits to the new pricing page",
        "audience": "Small agency owners",
        "landing_url": "https://example.com/pricing?ref=nav",
        "channels": ["LinkedIn", "email", " X "],
        "start_date": "2026-10-01",
        "end_date": "2026-10-20",
        **kw,
    }


def create(**kw):
    r = client.post("/campaigns", json=payload(**kw), headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def move(cid, status):
    return client.post(f"/campaigns/{cid}/status", json={"status": status}, headers=AUTH)


def add_kpi(cid, **kw):
    return client.post(f"/campaigns/{cid}/kpis", json={"metric": "clicks", "target_value": 100, **kw},
                       headers=AUTH)


def campaign_in(status, **kw):
    kw.setdefault("kpis", [{"metric": "clicks", "target_value": 100}])
    paths = {
        "planned": [], "active": ["active"], "paused": ["active", "paused"],
        "completed": ["active", "completed"], "cancelled": ["cancelled"],
    }
    c = create(**kw)
    for s in paths[status]:
        assert move(c["id"], s).status_code == 200
    return client.get(f"/campaigns/{c['id']}").json()


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


# ---------- create / read


def test_create_defaults():
    c = create(kpis=[{"metric": "clicks", "target_value": 500},
                     {"metric": "ctr", "target_value": 0.02},
                     {"metric": "signups", "target_value": 40, "baseline_value": 10}])
    assert c["slug"] == "autumn-launch-2026"
    assert c["status"] == "planned"
    assert c["channels"] == ["linkedin", "email", "x"]
    assert c["offer"] is None and c["budget"] is None
    assert set(c) >= {"id", "slug", "name", "goal_type", "goal_text", "audience", "offer",
                      "landing_url", "channels", "start_date", "end_date", "status", "budget",
                      "notes", "created_at", "updated_at", "kpis"}
    sources = {k["metric"]: k["source"] for k in c["kpis"]}
    assert sources == {"clicks": "shortener", "ctr": "analytics", "signups": "manual"}
    k = c["kpis"][2]
    assert k["baseline_value"] == 10 and k["actual_value"] is None and k["measured_at"] is None
    assert k["campaign_id"] == c["id"]


def test_slug_is_slugified_and_limited():
    c = create(name="Black Friday!!  Mega_Sale — 2026 edition, now with a very long name")
    assert c["slug"] == "black-friday-mega-sale-2026-edition-now"
    assert len(c["slug"]) <= 40
    c2 = create(slug="My Custom Slug", name="Other")
    assert c2["slug"] == "my-custom-slug"


def test_slug_clash_is_409():
    create()
    r = client.post("/campaigns", json=payload(), headers=AUTH)
    assert r.status_code == 409


def test_validation_errors():
    assert client.post("/campaigns", json=payload(end_date="2026-09-30"), headers=AUTH).status_code == 422
    assert client.post("/campaigns", json=payload(start_date="10/01/2026"), headers=AUTH).status_code == 422
    assert client.post("/campaigns", json=payload(goal_type="fame"), headers=AUTH).status_code == 422
    bad_kpi = payload(kpis=[{"metric": "likes", "target_value": 1}])
    assert client.post("/campaigns", json=bad_kpi, headers=AUTH).status_code == 422
    bad_src = payload(kpis=[{"metric": "signups", "target_value": 1, "source": "shortener"}])
    assert client.post("/campaigns", json=bad_src, headers=AUTH).status_code == 422
    assert client.post("/campaigns", json=payload(name="!!!"), headers=AUTH).status_code == 422


def test_same_day_campaign_is_allowed():
    c = create(start_date="2026-10-01", end_date="2026-10-01")
    assert c["end_date"] == "2026-10-01"


def test_get_list_and_by_slug():
    a = create()
    b = campaign_in("active", name="Second")
    assert client.get(f"/campaigns/{a['id']}").json()["slug"] == a["slug"]
    assert client.get("/campaigns/by-slug/second").json()["id"] == b["id"]
    assert client.get("/campaigns/by-slug/nope").status_code == 404
    assert client.get("/campaigns/999").status_code == 404
    assert [c["id"] for c in client.get("/campaigns").json()] == [a["id"], b["id"]]
    assert [c["id"] for c in client.get("/campaigns?status=active").json()] == [b["id"]]
    assert client.get("/campaigns?status=bogus").status_code == 422


# ---------- auth


@pytest.mark.parametrize("method,path,body", [
    ("post", "/campaigns", {}),
    ("patch", "/campaigns/1", {}),
    ("post", "/campaigns/1/status", {"status": "active"}),
    ("post", "/campaigns/1/kpis", {"metric": "clicks", "target_value": 1}),
    ("patch", "/campaigns/1/kpis/1", {}),
    ("delete", "/campaigns/1/kpis/1", None),
    ("post", "/campaigns/1/measure", None),
])
def test_writes_need_key(method, path, body, monkeypatch):
    kwargs = {"json": body} if body is not None else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401
    assert getattr(client, method)(path, headers={"X-API-Key": "wrong"}, **kwargs).status_code == 401
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert getattr(client, method)(path, headers=AUTH, **kwargs).status_code == 503


def test_reads_and_link_need_no_key():
    c = create()
    assert client.get(f"/campaigns/{c['id']}").status_code == 200
    assert client.post(f"/campaigns/{c['id']}/link", json={"channel": "x"}).status_code == 200


# ---------- status transitions


ALL = ("planned", "active", "paused", "completed", "cancelled")


@pytest.mark.parametrize("current,target", [
    (cur, t) for cur in ALL for t in ALL
])
def test_every_transition(current, target):
    c = campaign_in(current)
    r = move(c["id"], target)
    if target in TRANSITIONS[current]:
        assert r.status_code == 200, r.text
        assert r.json()["status"] == target
    else:
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["current"] == current and detail["allowed"] == TRANSITIONS[current]


def test_activation_requires_a_kpi():
    c = create()
    r = move(c["id"], "active")
    assert r.status_code == 409
    assert "KPI" in r.json()["detail"]["message"]
    assert add_kpi(c["id"]).status_code == 201
    assert move(c["id"], "active").status_code == 200


def test_unknown_status_is_422():
    c = create()
    assert move(c["id"], "launched").status_code == 422


# ---------- patch


def test_patch_fields_and_forbidden_ones():
    c = create()
    r = client.patch(f"/campaigns/{c['id']}", json={"budget": 500, "channels": ["Blog"], "offer": "20% off"},
                     headers=AUTH)
    assert r.status_code == 200
    assert r.json()["budget"] == 500 and r.json()["channels"] == ["blog"]
    assert client.patch(f"/campaigns/{c['id']}", json={"status": "active"}, headers=AUTH).status_code == 422
    assert client.patch(f"/campaigns/{c['id']}", json={"kpis": []}, headers=AUTH).status_code == 422
    assert client.patch(f"/campaigns/{c['id']}", json={"end_date": "2026-09-01"}, headers=AUTH).status_code == 422
    assert client.patch(f"/campaigns/{c['id']}", json={"name": " "}, headers=AUTH).status_code == 422


def test_slug_editable_while_planned_then_locked():
    c = create()
    create(name="Taken")
    assert client.patch(f"/campaigns/{c['id']}", json={"slug": "taken"}, headers=AUTH).status_code == 409
    r = client.patch(f"/campaigns/{c['id']}", json={"slug": "Fall Push"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["slug"] == "fall-push"
    add_kpi(c["id"])
    move(c["id"], "active")
    r = client.patch(f"/campaigns/{c['id']}", json={"slug": "renamed"}, headers=AUTH)
    assert r.status_code == 409
    # sending the same slug is harmless, other fields still editable
    r = client.patch(f"/campaigns/{c['id']}", json={"slug": "fall-push", "notes": "going well"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["notes"] == "going well"


# ---------- kpis


def test_kpi_add_patch_delete():
    c = create()
    r = add_kpi(c["id"], metric="revenue", target_value=10000)
    assert r.status_code == 201
    k = r.json()
    assert k["source"] == "manual"
    assert add_kpi(c["id"], metric="revenue", target_value=1).status_code == 409
    r = client.patch(f"/campaigns/{c['id']}/kpis/{k['id']}", json={"actual_value": 2500, "target_value": 9000},
                     headers=AUTH)
    assert r.status_code == 200
    assert r.json()["actual_value"] == 2500 and r.json()["target_value"] == 9000
    assert r.json()["measured_at"] is not None
    assert client.patch(f"/campaigns/{c['id']}/kpis/{k['id']}", json={"metric": "clicks"},
                        headers=AUTH).status_code == 422
    assert client.patch(f"/campaigns/{c['id']}/kpis/999", json={}, headers=AUTH).status_code == 404
    r = client.delete(f"/campaigns/{c['id']}/kpis/{k['id']}", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"deleted": k["id"]}
    assert client.get(f"/campaigns/{c['id']}").json()["kpis"] == []
    assert add_kpi(999).status_code == 404


def test_active_campaign_keeps_last_kpi():
    c = campaign_in("active")
    kid = c["kpis"][0]["id"]
    assert client.delete(f"/campaigns/{c['id']}/kpis/{kid}", headers=AUTH).status_code == 409


# ---------- link


def test_link_uses_slug_channel_medium_and_landing_url():
    c = create()
    r = client.post(f"/campaigns/{c['id']}/link", json={"channel": "LinkedIn", "content": "42"})
    assert r.status_code == 200
    body = r.json()
    assert body["params"] == {"utm_source": "linkedin", "utm_medium": "social",
                              "utm_campaign": "autumn-launch-2026", "utm_content": "42"}
    parts = urlsplit(body["url"])
    assert parts.netloc == "example.com" and parts.path == "/pricing"
    q = parse_qs(parts.query)
    assert q["ref"] == ["nav"] and q["utm_campaign"] == ["autumn-launch-2026"]


@pytest.mark.parametrize("channel,medium", [
    ("email", "email"), ("google_ads", "cpc"), ("blog", "referral"), ("instagram", "social"),
])
def test_link_medium_by_channel(channel, medium):
    c = create()
    r = client.post(f"/campaigns/{c['id']}/link", json={"channel": channel})
    assert r.json()["params"]["utm_medium"] == medium
    assert "utm_content" not in r.json()["params"]


def test_link_url_override_and_missing_landing_url():
    c = create(landing_url=None)
    assert client.post(f"/campaigns/{c['id']}/link", json={"channel": "x"}).status_code == 422
    r = client.post(f"/campaigns/{c['id']}/link",
                    json={"channel": "x", "url": "https://example.com/blog/post?utm_source=old"})
    assert r.status_code == 200
    q = parse_qs(urlsplit(r.json()["url"]).query)
    assert q["utm_source"] == ["x"]
    assert client.post(f"/campaigns/{c['id']}/link", json={"channel": "x", "url": "ftp://a"}).status_code == 422
    assert client.post("/campaigns/999/link", json={"channel": "x"}).status_code == 404


# ---------- measure


KPIS = [
    {"metric": "clicks", "target_value": 200},
    {"metric": "sessions", "target_value": 1000},
    {"metric": "ctr", "target_value": 0.02},
    {"metric": "signups", "target_value": 50},
]


def analytics_body(**totals):
    base = {"impressions": 10000, "clicks": 150, "sessions": 400, "conversions": 12,
            "spend": 0, "ctr": 0.015, "cvr": 0.03, "cpa": None}
    return {"period": {"from": "2026-10-01", "to": "2026-10-11"}, "totals": {**base, **totals},
            "by_channel": [], "previous": None, "delta_pct": None}


def test_measure_pulls_actuals(mock):
    c = campaign_in("active", kpis=KPIS)
    links = mock.get(f"{SHORT}/links").mock(return_value=httpx.Response(200, json=[
        {"slug": "a", "short_url": "s/a", "url": "https://example.com", "clicks": 120, "created_at": "2026-10-02T00:00:00Z"},
        {"slug": "b", "short_url": "s/b", "url": "https://example.com", "clicks": 90, "created_at": "2026-10-03T00:00:00Z"},
    ]))
    kpis = mock.get(f"{ANALYTICS}/kpis").mock(return_value=httpx.Response(200, json=analytics_body()))
    mock.get(f"{CAL}/items").mock(return_value=httpx.Response(200, json=[
        {"id": 1, "title": "a", "channel": "linkedin", "status": "published"},
        {"id": 2, "title": "b", "channel": "x", "status": "published"},
        {"id": 3, "title": "c", "channel": "x", "status": "draft"},
    ]))
    r = client.post(f"/campaigns/{c['id']}/measure", headers=AUTH)
    assert r.status_code == 200, r.text
    sc = r.json()
    assert sc["errors"] == []
    by = {k["metric"]: k for k in sc["kpis"]}
    assert by["clicks"]["actual_value"] == 210 and by["clicks"]["state"] == "met"
    assert by["sessions"]["actual_value"] == 400
    assert by["ctr"]["actual_value"] == 0.015
    assert by["signups"]["actual_value"] is None and by["signups"]["state"] == "not_measured"
    assert by["clicks"]["measured_at"] is not None
    assert sc["assets"] == {"published": 2, "draft": 1}
    # the right upstream queries were sent, with the key
    q = links.calls.last.request.url.params
    assert q["utm_campaign"] == c["slug"]
    q = kpis.calls.last.request.url.params
    assert (q["from"], q["to"], q["campaign"]) == ("2026-10-01", "2026-10-11", c["slug"])
    assert kpis.calls.last.request.headers["x-api-key"] == KEY
    # persisted
    stored = {k["metric"]: k["actual_value"] for k in client.get(f"/campaigns/{c['id']}").json()["kpis"]}
    assert stored["sessions"] == 400


def test_measure_caps_analytics_range_at_end_date(mock, monkeypatch):
    set_today(monkeypatch, date(2026, 12, 1))
    c = campaign_in("active", kpis=[{"metric": "sessions", "target_value": 10}])
    route = mock.get(f"{ANALYTICS}/kpis").mock(return_value=httpx.Response(200, json=analytics_body()))
    mock.get(f"{CAL}/items").mock(return_value=httpx.Response(200, json=[]))
    client.post(f"/campaigns/{c['id']}/measure", headers=AUTH)
    assert route.calls.last.request.url.params["to"] == "2026-10-20"


def test_measure_with_upstreams_down(mock):
    c = campaign_in("active", kpis=KPIS)
    mock.get(f"{SHORT}/links").mock(side_effect=httpx.ConnectError("refused"))
    mock.get(f"{ANALYTICS}/kpis").mock(return_value=httpx.Response(500, text="boom"))
    mock.get(f"{CAL}/items").mock(side_effect=httpx.ConnectTimeout("slow"))
    r = client.post(f"/campaigns/{c['id']}/measure", headers=AUTH)
    assert r.status_code == 200
    sc = r.json()
    assert all(k["state"] == "not_measured" for k in sc["kpis"])
    sources = {e["source"] for e in sc["errors"]}
    assert sources == {"shortener", "analytics", "calendar"}
    analytics_err = next(e for e in sc["errors"] if e["source"] == "analytics")
    assert set(analytics_err["metrics"]) == {"sessions", "ctr"} and "500" in analytics_err["error"]
    assert sc["assets"] == {}


def test_measure_partial_failure_keeps_other_source(mock):
    c = campaign_in("active", kpis=KPIS)
    mock.get(f"{SHORT}/links").mock(return_value=httpx.Response(200, json=[{"clicks": 5}]))
    mock.get(f"{ANALYTICS}/kpis").mock(return_value=httpx.Response(200, json=analytics_body(ctr=None)))
    mock.get(f"{CAL}/items").mock(return_value=httpx.Response(200, json=[]))
    sc = client.post(f"/campaigns/{c['id']}/measure", headers=AUTH).json()
    by = {k["metric"]: k for k in sc["kpis"]}
    assert by["clicks"]["actual_value"] == 5
    assert by["sessions"]["actual_value"] == 400
    assert by["ctr"]["actual_value"] is None
    assert sc["errors"] == [{"source": "analytics", "metrics": ["ctr"], "error": "no value in totals"}]


def test_measure_before_start_skips_analytics(mock, monkeypatch):
    set_today(monkeypatch, date(2026, 9, 1))
    c = campaign_in("planned", kpis=[{"metric": "sessions", "target_value": 10}])
    route = mock.get(f"{ANALYTICS}/kpis")
    mock.get(f"{CAL}/items").mock(return_value=httpx.Response(200, json=[]))
    sc = client.post(f"/campaigns/{c['id']}/measure", headers=AUTH).json()
    assert not route.called
    assert sc["errors"][0]["source"] == "analytics"


def test_measure_missing_campaign_is_404():
    assert client.post("/campaigns/999/measure", headers=AUTH).status_code == 404


# ---------- scorecard


def set_actual(cid, kid, value):
    r = client.patch(f"/campaigns/{cid}/kpis/{kid}", json={"actual_value": value}, headers=AUTH)
    assert r.status_code == 200


def test_scorecard_states(mock, monkeypatch):
    # 2026-10-01..2026-10-20 = 20 days; today 2026-10-11 → 10 days done = 50 % elapsed
    mock.get(f"{CAL}/items").mock(return_value=httpx.Response(200, json=[]))
    c = campaign_in("active", kpis=[
        {"metric": "clicks", "target_value": 100},      # met
        {"metric": "sessions", "target_value": 1000},   # on_track (60 %)
        {"metric": "conversions", "target_value": 50},  # behind (20 %)
        {"metric": "signups", "target_value": 40},      # not_measured
    ])
    ids = {k["metric"]: k["id"] for k in c["kpis"]}
    set_actual(c["id"], ids["clicks"], 120)
    set_actual(c["id"], ids["sessions"], 600)
    set_actual(c["id"], ids["conversions"], 10)
    sc = client.get(f"/campaigns/{c['id']}/scorecard").json()
    assert sc["elapsed_pct"] == 50.0
    by = {k["metric"]: k for k in sc["kpis"]}
    assert (by["clicks"]["state"], by["clicks"]["progress_pct"]) == ("met", 120.0)
    assert (by["sessions"]["state"], by["sessions"]["progress_pct"]) == ("on_track", 60.0)
    assert (by["conversions"]["state"], by["conversions"]["progress_pct"]) == ("behind", 20.0)
    assert (by["signups"]["state"], by["signups"]["progress_pct"]) == ("not_measured", None)
    assert sc["campaign"]["id"] == c["id"] and sc["errors"] == []


def test_scorecard_progress_rounding_and_time(mock, monkeypatch):
    mock.get(f"{CAL}/items").mock(return_value=httpx.Response(200, json=[]))
    c = campaign_in("active", kpis=[{"metric": "clicks", "target_value": 3}])
    set_actual(c["id"], c["kpis"][0]["id"], 1)
    set_today(monkeypatch, date(2026, 9, 1))  # before start: anything measured is on track
    k = client.get(f"/campaigns/{c['id']}/scorecard").json()["kpis"][0]
    assert k["progress_pct"] == 33.3 and k["state"] == "on_track"
    set_today(monkeypatch, date(2026, 11, 1))  # after end: 100 % elapsed, not met → behind
    sc = client.get(f"/campaigns/{c['id']}/scorecard").json()
    assert sc["elapsed_pct"] == 100.0 and sc["kpis"][0]["state"] == "behind"


def test_scorecard_calendar_down_is_not_500(mock):
    mock.get(f"{CAL}/items").mock(return_value=httpx.Response(503))
    c = create()
    r = client.get(f"/campaigns/{c['id']}/scorecard")
    assert r.status_code == 200
    assert r.json()["errors"][0]["source"] == "calendar"
    assert client.get("/campaigns/999/scorecard").status_code == 404


# ---------- report


def test_unmeasured_report(monkeypatch):
    set_today(monkeypatch, date(2026, 11, 1))
    ended = create(name="Ended", kpis=[{"metric": "clicks", "target_value": 1},
                                       {"metric": "signups", "target_value": 1}])
    set_actual(ended["id"], ended["kpis"][0]["id"], 5)
    done_measured = create(name="Done", kpis=[{"metric": "clicks", "target_value": 1}])
    set_actual(done_measured["id"], done_measured["kpis"][0]["id"], 5)
    running = create(name="Running", end_date="2026-12-01", kpis=[{"metric": "clicks", "target_value": 1}])
    completed_early = campaign_in("completed", name="Early", end_date="2026-12-01")
    cancelled = campaign_in("cancelled", name="Dropped")
    rows = client.get("/report/unmeasured").json()
    ids = [r["id"] for r in rows]
    assert ended["id"] in ids and completed_early["id"] in ids
    assert done_measured["id"] not in ids and running["id"] not in ids and cancelled["id"] not in ids
    assert next(r for r in rows if r["id"] == ended["id"])["unmeasured"] == ["signups"]


# ---------- insights


def test_insights_aggregates_by_channel_and_post(mock):
    recent = "2026-09-20T10:00:00Z"
    mock.get(f"{SHORT}/links").mock(return_value=httpx.Response(200, json=[
        {"slug": "a", "url": "https://ex.com/?utm_source=linkedin&utm_content=1", "clicks": 30, "created_at": recent},
        {"slug": "b", "url": "https://ex.com/?utm_source=linkedin&utm_content=1", "clicks": 10, "created_at": recent},
        {"slug": "c", "url": "https://ex.com/?utm_source=linkedin&utm_content=2", "clicks": 20, "created_at": recent},
        {"slug": "d", "url": "https://ex.com/?utm_source=x&utm_content=3", "clicks": 5, "created_at": recent},
        {"slug": "e", "url": "https://ex.com/?utm_content=hero-banner", "clicks": 999, "created_at": recent},
        {"slug": "f", "url": "https://ex.com/plain", "clicks": 999, "created_at": recent},
        {"slug": "g", "url": "https://ex.com/?utm_content=4", "clicks": 999, "created_at": "2020-01-01T00:00:00Z"},
    ]))
    item1 = mock.get(f"{CAL}/items/1").mock(return_value=httpx.Response(200, json={"id": 1, "title": "Launch", "channel": "linkedin"}))
    mock.get(f"{CAL}/items/2").mock(return_value=httpx.Response(200, json={"id": 2, "title": "Tips", "channel": "linkedin"}))
    mock.get(f"{CAL}/items/3").mock(return_value=httpx.Response(404))
    r = client.get("/insights?days=90")
    assert r.status_code == 200
    body = r.json()
    assert body["by_channel"] == [
        {"channel": "linkedin", "posts": 2, "clicks": 60, "avg_clicks": 30.0},
        {"channel": "x", "posts": 1, "clicks": 5, "avg_clicks": 5.0},
    ]
    assert body["top_posts"] == [
        {"item_id": 1, "title": "Launch", "channel": "linkedin", "clicks": 40},
        {"item_id": 2, "title": "Tips", "channel": "linkedin", "clicks": 20},
        {"item_id": 3, "title": None, "channel": "x", "clicks": 5},
    ]
    assert item1.call_count == 1  # fetched once per request
    assert body["errors"] == [{"source": "calendar", "item_id": 3, "error": "HTTP 404"}]


def test_insights_shortener_down(mock):
    mock.get(f"{SHORT}/links").mock(side_effect=httpx.ConnectError("refused"))
    body = client.get("/insights").json()
    assert body["by_channel"] == [] and body["top_posts"] == []
    assert body["errors"][0]["source"] == "shortener"


# ---------- helpers


@pytest.mark.parametrize("raw,slug", [
    ("Spring Sale", "spring-sale"),
    ("  Q4 -- Push__Now ", "q4-push-now"),
    ("Café Día", "caf-da"),
    ("a" * 60, "a" * 40),
])
def test_slugify(raw, slug):
    assert main.slugify(raw) == slug
