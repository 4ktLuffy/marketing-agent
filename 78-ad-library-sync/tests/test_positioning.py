"""Positioning map: the verbatim-quote filter, the matrix, white space only from approved facts,
monthly snapshots and their diff. 09, 05 and the gateway are respx mocks; no LLM runs."""
import json
from collections import Counter
from contextlib import closing

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main, positioning
from app.main import app

KEY = "internal-secret-key"
AUTH = {"X-API-Key": KEY}
MON, BRAND, GW = "http://monitor.test", "http://brand.test", "http://gateway.test"
client = TestClient(app)

RIVAL_PRICING = ("Pricing\nPlans from $10 a month.\nFirst box free, cancel any time.\n"
                 "Every bag is roasted the day we ship it.")
BEANCO_HOME = "BeanCo\nCoffee for your whole office.\nOrder for the team in one click.\nFirst box free for new offices."
OUR_HOME = "Northwind Roasters\nRoasted to order and delivered on your workweek schedule."
FACTS = [{"id": "f1", "text": "Unopened bags can be returned within 30 days for a full refund."},
         {"id": "f2", "text": "Team Box contains 1.5 kg of coffee across two blends and ships to each teammate."},
         {"id": "f3", "text": "Subscribers can skip or pause a delivery from their account page at any time, with no fee."}]


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for k in ("OWN_DOMAINS", "POSITIONING_THEMES", "POSITIONING_MAX_CHARS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ads.sqlite"))
    monkeypatch.delenv("MONITOR_URL", raising=False)
    client.post("/competitors", headers=AUTH, json={"name": "Rival Beans", "website": "https://rival.example.org/"})
    client.post("/competitors", headers=AUTH, json={"name": "BeanCo", "website": "https://beanco.example.net/"})
    monkeypatch.setenv("MONITOR_URL", MON)
    monkeypatch.setenv("BRAND_URL", BRAND)
    monkeypatch.setenv("GATEWAY_URL", GW)


def ad_row(ad_id, text, cid=1):
    with closing(main.db()) as conn, conn:
        main.store_ads(conn, cid, [{"id": ad_id, "page_id": "1", "page_name": "Rival Beans", "ad_creative_bodies": [text],
                                    "ad_creative_link_titles": ["Try Rival"]}], main.now())


def snapshots(extra=()):
    return [{"id": 1, "url": "https://rival.example.org/pricing", "label": "Rival Beans · pricing", "tag": "competitor:1",
             "last_checked_at": "2026-09-27T00:00:00+00:00", "text": RIVAL_PRICING},
            {"id": 2, "url": "https://beanco.example.net/", "label": "BeanCo · home", "tag": "competitor:2",
             "last_checked_at": "2026-09-27T00:00:00+00:00", "text": BEANCO_HOME},
            {"id": 3, "url": "https://northwind-roasters.example.com/", "label": "our home", "tag": None,
             "last_checked_at": "2026-09-27T00:00:00+00:00", "text": OUR_HOME},
            {"id": 4, "url": "https://unrelated.example.com/", "label": "x", "tag": None, "last_checked_at": None,
             "text": "Cheap coffee for everyone at the lowest price."}, *extra]


def claims_for(company, sources):
    """A fake model: finds the source ids by their text, returns good, invented and bad claims."""
    sid = lambda needle: next(l.split("]")[0][1:] for l in sources.splitlines() if needle in l)  # noqa: E731
    if company == "Rival Beans":
        out = [{"theme": "price", "source_id": sid("Plans from"), "quote": "Plans from $10 a month."},
               {"theme": "price", "source_id": sid("Plans from"), "quote": "first box FREE,  cancel any time"},
               {"theme": "freshness", "source_id": sid("Plans from"), "quote": "roasted the day we ship it"},
               {"theme": "freshness", "source_id": sid("Plans from"), "quote": "roasted fresh every single day"},  # invented
               {"theme": "vibes", "source_id": sid("Plans from"), "quote": "Plans from $10 a month."},  # unknown theme
               {"theme": "price", "source_id": "s99", "quote": "Plans from $10 a month."}]  # unknown source
        if "Christmas" in sources:
            out.append({"theme": "gifting", "source_id": sid("Christmas"), "quote": "Give the gift of fresh coffee"})
        return out
    if company == "BeanCo":
        return [{"theme": "team_office", "source_id": sid("office"), "quote": "Coffee for your whole office."},
                {"theme": "price", "source_id": sid("First box"), "quote": "First box free for new offices."},
                {"theme": "price", "source_id": sid("office"), "quote": "cheap"}]  # too short
    return [{"theme": "guarantee", "source_id": sid("returned"), "quote": "returned within 30 days for a full refund"},
            {"theme": "team_office", "source_id": sid("Team Box"), "quote": "ships to each teammate"},
            {"theme": "convenience", "source_id": sid("workweek"), "quote": "delivered on your workweek schedule"},
            {"theme": "convenience", "source_id": sid("skip or pause"), "quote": "skip or pause a delivery"},
            {"theme": "freshness", "source_id": sid("workweek"), "quote": "Roasted to order"},
            {"theme": "speed", "source_id": sid("returned"), "quote": "we deliver faster than anyone"}]  # invented


def mock_services(router, snaps=None, gateway=None):
    calls = []

    def gw(request):
        assert request.headers["x-api-key"] == KEY
        body = json.loads(request.content)
        assert body["prompt"] == "positioning_themes"
        calls.append(body["vars"])
        if gateway:
            return gateway(body)
        return httpx.Response(200, json={"output": {"claims": claims_for(body["vars"]["company"], body["vars"]["sources"])},
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    router.get(f"{MON}/snapshots").mock(side_effect=lambda r: (
        httpx.Response(200, json=snaps if snaps is not None else snapshots()) if r.headers.get("x-api-key") == KEY
        else httpx.Response(401)))
    router.get(f"{BRAND}/profile").respond(json={"name": "Northwind Roasters", "website": "https://northwind-roasters.example.com"})
    router.get(f"{BRAND}/facts").respond(json={"facts": FACTS})
    router.post(f"{GW}/v1/run").mock(side_effect=gw)
    return calls


# ---------- the verbatim filter


def test_find_verbatim_allows_case_space_and_apostrophes_only():
    text = "We roast   every Tuesday.\nIt’s the best part of the week."
    assert positioning.find_verbatim("we ROAST every tuesday", text) == "We roast   every Tuesday"
    assert positioning.find_verbatim("\"It's the best part\"", text) == "It’s the best part"
    assert positioning.find_verbatim("we roast every Monday", text) is None
    assert positioning.find_verbatim("We roast on Tuesdays", text) is None   # paraphrase
    assert positioning.find_verbatim("", text) is None


def test_check_claims_keeps_only_exact_quotes_of_the_cited_source():
    srcs = {"s1": {"kind": "ad", "ref": "ad 1", "url": "u1", "text": "Fresh beans every month. First box free."},
            "s2": {"kind": "page: home", "ref": "home", "url": "u2", "text": "Coffee for teams."}}
    st = Counter()
    kept = positioning.check_claims([
        {"theme": "price", "source_id": "s1", "quote": "First box free."},
        {"theme": "price", "source_id": "s2", "quote": "First box free."},        # right words, wrong source
        {"theme": "price", "source_id": "[s1]", "quote": "first box free"},       # duplicate span
        {"theme": "freshness", "source_id": "s1", "quote": "Fresh beans, every week"},
        {"theme": "Price", "source_id": "s1", "quote": "Fresh beans every month."},
        {"theme": "price", "source_id": "s1", "quote": "Fresh beans"},            # 2 words
        "not a dict"], srcs, {"price", "freshness"}, st)
    assert [(k["theme"], k["quote"], k["source"]["ref"]) for k in kept] == [
        ("price", "First box free.", "ad 1"), ("price", "Fresh beans every month.", "ad 1")]
    assert st == Counter(proposed=7, kept=2, not_verbatim=2, duplicate=1, length=1, malformed=1)


def test_negative_control_without_the_check_the_invented_quote_would_count():
    """The fake model's invented claims reach the matrix only if the filter is bypassed."""
    srcs = {"s1": {"kind": "fact", "ref": "fact f1", "url": None, "text": "Unopened bags can be returned."}}
    bad = [{"theme": "speed", "source_id": "s1", "quote": "we deliver faster than anyone"}]
    assert positioning.check_claims(bad, srcs, {"speed"}, Counter()) == []
    unchecked = [{"theme": c["theme"], "quote": c["quote"], "source": srcs["s1"]} for c in bad]
    brands = [{"name": "Us", "kind": "ours", "claims": unchecked}]
    assert positioning.build_matrix(brands, ["speed"])["speed"]["Us"]["from_facts"] == 1


# ---------- the build


@respx.mock
def test_build_matrix_white_space_crowded_and_sources():
    ad_row("11", "Give the gift of fresh coffee this Christmas.")
    ad_row("12", "Old summer promo", cid=2)
    with closing(main.db()) as conn, conn:
        conn.execute("UPDATE ads SET status = 'stopped' WHERE ad_id = '12'")
    calls = mock_services(respx)
    r = client.post("/positioning/build?month=2026-09", headers=AUTH)
    assert r.status_code == 200, r.text
    s = r.json()
    assert [b["name"] for b in s["brands"]] == ["Rival Beans", "BeanCo", "Northwind Roasters"]
    rival, beanco, ours = s["brands"]
    assert (rival["pages"], rival["ads"], beanco["ads"]) == (1, 1, 0)   # stopped ad not read
    assert (ours["facts"], ours["pages"]) == (3, 1)                     # unrelated page not ours
    # Competitors are never shown our brand: no {{ brand }} and our facts are not in their sources.
    assert "returned within 30 days" not in calls[0]["sources"] and "Northwind" not in calls[0]["sources"]
    cells = s["cells"]
    assert cells["price"]["Rival Beans"]["count"] == 2
    assert cells["price"]["Rival Beans"]["quotes"][1]["quote"] == "First box free, cancel any time"  # the source's span
    assert cells["freshness"]["Rival Beans"]["count"] == 1              # invented one dropped
    assert cells["gifting"]["Rival Beans"]["quotes"][0]["source"]["url"] == "https://www.facebook.com/ads/library/?id=11"
    assert cells["price"]["BeanCo"]["count"] == 1 and cells["team_office"]["BeanCo"]["count"] == 1
    assert cells["speed"]["Northwind Roasters"]["count"] == 0
    assert s["stats"]["not_verbatim"] == 3 and s["stats"]["unknown_theme"] == 1 and s["stats"]["unknown_source"] == 1
    assert s["usage"] == {"prompt_tokens": 300, "completion_tokens": 150}
    # White space: no competitor claims it AND an approved fact backs it.
    assert [w["theme"] for w in s["white_space"]] == ["convenience", "guarantee"]
    assert s["white_space"][0]["facts"][0]["source"]["kind"] == "fact"
    # team_office: we have a fact but BeanCo claims it -> not white space. Crowded = both competitors.
    assert [c["theme"] for c in s["crowded"]] == ["price"]
    assert {"theme": "team_office", "competitors": ["BeanCo"]} not in s["they_claim_we_dont"]
    assert {m["theme"] for m in s["they_claim_we_dont"]} == {"price", "gifting"}
    assert "freshness" not in s["open_no_fact"]  # Rival claims it
    assert set(s["open_no_fact"]) >= {"quality", "ethics", "speed"}
    assert s["shifts"] == [] and s["previous_month"] is None
    md = s["markdown"]
    assert md.startswith("# Positioning map September 2026")
    assert "| price | 2 | 1 | 0 |" in md and "**guarantee**" in md


@respx.mock
def test_white_space_needs_an_approved_fact_not_just_our_page():
    """Our homepage says 'Roasted to order' (freshness), no competitor claims freshness, but no fact
    says it: it is open, not white space."""
    snaps = [s for s in snapshots() if s["id"] != 1]    # Rival has no page: freshness unclaimed
    mock_services(respx, snaps=snaps)
    s = client.post("/positioning/build?month=2026-09", headers=AUTH).json()
    assert s["cells"]["freshness"]["Northwind Roasters"]["count"] == 1
    assert s["cells"]["freshness"]["Northwind Roasters"]["from_facts"] == 0
    assert "freshness" not in [w["theme"] for w in s["white_space"]]
    assert "freshness" in s["open_no_fact"]
    assert s["brands"][0]["error"].startswith("nothing to read yet")


@respx.mock
def test_monthly_snapshots_shift_and_diff():
    mock_services(respx)
    first = client.post("/positioning/build?month=2026-09", headers=AUTH).json()
    ad_row("11", "Give the gift of fresh coffee this Christmas.")
    snaps = snapshots()
    snaps[1] = snaps[1] | {"text": "BeanCo\nFirst box free for new offices."}   # BeanCo dropped the office line
    respx.get(f"{MON}/snapshots").respond(json=snaps)
    oct_ = client.post("/positioning/build?month=2026-10", headers=AUTH).json()
    assert oct_["previous_month"] == "2026-09"
    texts = [x["text"] for x in oct_["shifts"]]
    assert "Rival Beans started talking about gifting in October 2026" in texts
    assert "BeanCo stopped talking about team_office in October 2026" in texts
    started = next(x for x in oct_["shifts"] if x["kind"] == "started")
    assert started["quote"][0]["quote"] == "Give the gift of fresh coffee"
    assert "## Shifts since last month" in oct_["markdown"] and "started talking about gifting" in oct_["markdown"]
    d = client.get("/positioning/diff", params={"from": oct_["id"], "to": first["id"]}).json()
    assert (d["from"]["month"], d["to"]["month"]) == ("2026-09", "2026-10")   # ordered by month
    assert {x["text"] for x in d["shifts"]} == set(texts)
    assert client.get("/positioning/diff", params={"from": 999, "to": first["id"]}).status_code == 404
    # Rebuilding a month replaces it; latest is the newest month.
    again = client.post("/positioning/build?month=2026-09", headers=AUTH).json()
    assert again["id"] != first["id"] and client.get(f"/positioning/{first['id']}").status_code == 404
    assert [x["month"] for x in client.get("/positioning").json()] == ["2026-10", "2026-09"]
    assert client.get("/positioning/latest").json()["month"] == "2026-10"
    assert client.get(f"/positioning/{oct_['id']}").json()["month"] == "2026-10"
    assert client.get("/positioning/999").status_code == 404


@respx.mock
def test_every_llm_call_failing_stores_nothing():
    mock_services(respx, gateway=lambda body: httpx.Response(503, json={"detail": "model down"}))
    r = client.post("/positioning/build?month=2026-09", headers=AUTH)
    assert r.status_code == 502 and "model down" in r.json()["detail"]
    assert KEY not in r.text
    assert client.get("/positioning/latest").status_code == 404


@respx.mock
def test_one_failed_brand_is_reported_not_counted():
    def gw(body):
        if body["vars"]["company"] == "BeanCo":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"output": {"claims": claims_for(body["vars"]["company"], body["vars"]["sources"])}})
    mock_services(respx, gateway=gw)
    s = client.post("/positioning/build?month=2026-09", headers=AUTH).json()
    assert s["brands"][1]["error"].startswith("gateway 500")
    assert s["crowded"] == []    # only one competitor was read


def test_build_needs_key_and_valid_month():
    assert client.post("/positioning/build").status_code == 401
    assert client.post("/positioning/build?month=2026-13", headers=AUTH).status_code == 422
    assert client.get("/positioning/latest").status_code == 404


def test_custom_themes(monkeypatch):
    monkeypatch.setenv("POSITIONING_THEMES", json.dumps({"price": "cost", "support": "help"}))
    assert list(positioning.themes()) == ["price", "support"]
    monkeypatch.setenv("POSITIONING_THEMES", '{"Bad Key": "x"}')
    assert positioning.themes() == positioning.DEFAULT_THEMES


@respx.mock
def test_model_jitter_on_unchanged_text_is_not_a_shift():
    """Same page text both months; the model files 'roasted the day we ship it' under freshness only
    in October (and our 'Roasted to order'), and drops BeanCo's office line in October. Neither is a shift: the words were
    (still) there. Negative control: without the stored texts both would be reported."""
    month = {"n": 0}

    def gw(body):
        v = body["vars"]
        claims = claims_for(v["company"], v["sources"])
        if month["n"] == 0:
            claims = [c for c in claims if c["theme"] != "freshness"]
        elif v["company"] == "BeanCo":
            claims = [c for c in claims if c["theme"] != "team_office"]
        return httpx.Response(200, json={"output": {"claims": claims}})
    mock_services(respx, gateway=gw)
    sep = client.post("/positioning/build?month=2026-09", headers=AUTH).json()
    month["n"] = 1
    oct_ = client.post("/positioning/build?month=2026-10", headers=AUTH).json()
    assert oct_["shifts"] == [] and oct_["shifts_suppressed"] == 3   # ours filed "Roasted to order" late too
    assert "_corpus" not in oct_ and "_corpus" not in client.get("/positioning/latest").json()
    # Negative control: the same two snapshots without their stored texts report both.
    with closing(main.db()) as conn:
        rows = {r["month"]: positioning._load(r) for r in conn.execute("SELECT * FROM positioning")}
    raw, n = positioning.shifts(rows["2026-09"], rows["2026-10"])
    assert n == 0 and {x["text"] for x in raw} == {"Rival Beans started talking about freshness in October 2026",
                                                  "Northwind Roasters started talking about freshness in October 2026",
                                                  "BeanCo stopped talking about team_office in October 2026"}
    assert sep["id"] != oct_["id"]
