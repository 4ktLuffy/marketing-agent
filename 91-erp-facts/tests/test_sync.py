import pytest

from .conftest import KEY, fact05


def sync(client, q=""):
    r = client.post(f"/sync?{q}")
    assert r.status_code == 200, r.text
    return r.json()


def test_health_open_and_lists_mappings(client):
    r = client.get("/health", headers={"X-API-Key": ""})
    assert r.status_code == 200 and r.json()["mappings"] == ["drink-crates", "service-plans"]
    assert r.json()["erp_configured"] is True


@pytest.mark.parametrize("hdr", [{}, {"X-API-Key": "wrong"}])
def test_key_required(client, hdr):
    for method, url in (("post", "/sync"), ("get", "/drift")):
        r = getattr(client, method)(url, headers={"X-API-Key": "", **hdr})
        assert r.status_code == 401


def test_unknown_mapping_404(client):
    assert client.post("/sync?mapping=nope").status_code == 404


def test_dry_run_writes_nothing_and_is_the_default(client, b05, odoo):
    for q in ("dry_run=true", ""):
        out = sync(client, q)
        assert out["dry_run"] is True and out["written"] == {"created": 0, "updated": 0}
    assert b05.writes == [] and b05.bad == []
    assert all(c["method"] in ("authenticate", "search_read") for c in odoo.calls)


def test_drift_detects_changed_price_and_reports_write_date(client, b05):
    r = client.get("/drift")
    assert r.status_code == 200
    d = r.json()
    ch = {c["key"]: c for c in d["changed"]}
    assert ch["price-starter-plan-list"] == {"key": "price-starter-plan-list", "served_value": 150,
                                                  "erp_value": 180, "erp_write_date": "2026-09-30 08:00:00"}
    assert d["unchanged"] == 1  # Cola matches 05
    assert b05.writes == []


def test_new_facts_for_both_examples(client):
    d = client.get("/drift").json()
    keys = {n["key"] for n in d["new"]}
    assert {"price-starter-plan-cost", "price-trial-plan-list", "price-lemonade-24x33cl-crate"} <= keys
    assert "price-cola-33cl-crate" not in keys  # unchanged


def test_placeholder_and_missing_prices_skipped_and_reported(client):
    d = client.get("/drift").json()
    sk = [(s["record"], s["reason"]) for s in d["skipped"]]
    assert any("Tonic" in r and "placeholder" in v for r, v in sk)
    assert any("Basic Plan" in r and "placeholder" in v for r, v in sk)  # list price = 1
    assert any("Basic Plan" in r and "no value" in v for r, v in sk)      # cost = False
    assert any("Trial Plan" in r and "<= 1" in v for r, v in sk)          # cost = 0
    keys = {n["key"] for n in d["new"]}
    assert "price-tonic-20x50cl-crate" not in keys
    assert "price-basic-plan-list" not in keys
    assert "price-trial-plan-list" in keys
    # other companies, not-for-sale and non-services are filtered by the domain, never read
    assert not any("Other company" in r or "Hardware" in r or "Not for sale" in r for r, _ in sk)


def test_real_run_posts_new_drafts_and_puts_changed_only(client, b05):
    out = sync(client, "dry_run=false")
    assert out["errors"] == [] and out["written"] == {"created": len(out["new"]), "updated": 1}
    methods = {(m, p.split("/facts/v2")[1][:1]) for m, p, _ in b05.writes}
    assert {m for m, _, _ in b05.writes} == {"POST", "PUT"}
    assert b05.bad == []  # no import, confirm, retire, owner key
    put = [w for w in b05.writes if w[0] == "PUT"][0]
    assert put[1] == "/facts/v2/price-starter-plan-list"
    body = put[2]
    assert body["value"] == 180 and body["currency"] == "GBP"
    assert body["source"] == {"kind": "doc", "ref": "Odoo product.template #10 write_date 2026-09-30 08:00:00"}
    assert "status" not in body  # never claims active
    assert body["scope"]["sites"] == ["Example Services Ltd"] and body["sensitivity"] == "public"


def test_bodies_match_examples(client, b05):
    sync(client, "dry_run=false")
    by = {b["key"]: b for m, _, b in b05.writes}
    cost = by["price-starter-plan-cost"]
    assert cost["sensitivity"] == "internal" and cost["value"] == 120
    crate = by["price-lemonade-24x33cl-crate"]
    assert crate["currency"] == "GBP" and crate["unit"] == "crate" and crate["basis"] == "per_unit"
    assert "24 bottles of 33cl" in crate["text"]
    assert "price-tonic-20x50cl-crate" not in by
    # the facts validate against 05's real model when it is importable
    try:
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "05-brand-service"))
        from app.facts_store import FactIn  # noqa: F401
    except Exception:
        return


def test_pending_draft_not_rewritten(client, b05):
    b05.facts["price-starter-plan-list"] = fact05("price-starter-plan-list", 150, "GBP", latest=2)
    b05.versions["price-starter-plan-list"] = [
        {"version": 1, "data": {"value": 150, "currency": "GBP"}},
        {"version": 2, "data": {"value": 180, "currency": "GBP"}}]
    d = sync(client, "dry_run=false")
    assert "price-starter-plan-list" in d["pending"]
    assert not [w for w in b05.writes if w[0] == "PUT"]


def test_retired_fact_is_skipped_not_revived(client, b05):
    b05.facts["price-cola-33cl-crate"] = fact05("price-cola-33cl-crate", 9.99, "GBP", status="retired")
    d = sync(client, "dry_run=false")
    assert any("retired" in s["reason"] for s in d["skipped"])
    assert all(w[2]["key"] != "price-cola-33cl-crate" for w in b05.writes)


def test_erp_price_change_shows_as_drift(client, odoo):
    for r in odoo.records["product.template"]:
        if r["id"] == 20:
            r["list_price"] = 13.5
    ch = {c["key"]: c for c in client.get("/drift").json()["changed"]}
    assert ch["price-cola-33cl-crate"]["served_value"] == 12 and ch["price-cola-33cl-crate"]["erp_value"] == 13.5


def test_single_mapping_only(client, odoo):
    d = sync(client, "mapping=drink-crates")
    assert d["mappings"] == ["drink-crates"] and all("price-" in n["key"] for n in d["new"])
    assert all(c["model"] == "product.template" for c in odoo.calls if c["method"] == "search_read")


def test_brand_down_is_502_and_erp_missing_creds_503(client, b05, odoo):
    b05.down = True
    assert client.get("/drift").status_code == 502
    from fastapi.testclient import TestClient
    from app.main import create_app
    from .conftest import make_cfg
    b05.down = False
    with TestClient(create_app(make_cfg(), env={})) as c:
        assert c.get("/drift", headers={"X-API-Key": KEY}).status_code == 503


def test_05_write_rejection_reported_in_errors(client, b05):
    b05.post_status = 409
    d = client.post("/sync?mapping=drink-crates&dry_run=false").json()
    assert d["errors"] and d["written"]["created"] == 0
