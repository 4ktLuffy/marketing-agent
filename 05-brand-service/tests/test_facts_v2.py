"""Scoped fact store (v2), the legacy /facts compatibility guarantee, questions, starter kits."""
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE.parent / "config" / "brand.yaml"
GOLDEN = HERE / "golden" / "legacy_facts.json"
OWNER = "owner-test-key"
client = TestClient(app)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    brand = tmp_path / "brand.yaml"
    shutil.copy(EXAMPLE, brand)
    monkeypatch.setenv("BRAND_FILE", str(brand))
    monkeypatch.setenv("BRAND_OVERRIDES_FILE", str(tmp_path / "brand.overrides.json"))
    monkeypatch.setenv("VOICE_FILE", str(tmp_path / "voice.json"))
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    return tmp_path


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setenv("FACT_OWNER_KEY", OWNER)
    return {"X-Owner-Key": OWNER}


def fact(key, **kw):
    body = {"key": key, "subject": {"kind": "product", "ref": "Widget"}, "fact_type": "spec",
            "text": f"Fact {key} is true.", "sensitivity": "public"}
    body.update(kw)
    return body


def add(owner_h, *facts, confirm=True):
    r = client.post("/facts/v2/import", json={"facts": list(facts), "confirm": confirm}, headers=owner_h)
    assert r.status_code == 200, r.text
    return r.json()


def legacy(**params):
    r = client.get("/facts", params=params)
    assert r.status_code == 200, r.text
    return r.json()["facts"]


def query(**params):
    r = client.get("/facts/query", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def reasons(q):
    return {e["key"]: e["reason"] for e in q["excluded"]}


def served(q):
    return {f["key"] for f in q["facts"]}


# --- legacy /facts ------------------------------------------------------------------------

def test_legacy_facts_byte_identical_with_empty_store(env):
    r = client.get("/facts")
    assert r.status_code == 200
    assert r.content == GOLDEN.read_bytes()
    assert not (env / "facts.sqlite").exists()   # a read never creates the store


def test_legacy_facts_byte_identical_after_store_exists_but_empty(owner):
    client.get("/questions")
    add(owner, confirm=True)  # empty import still opens (creates) the store
    assert client.get("/facts").content == GOLDEN.read_bytes()


def test_legacy_appends_v2_facts_with_contiguous_ids(owner):
    before = legacy()
    add(owner, fact("a-one", text="Widget weighs 2 kg."), fact("b-two", text="Widget ships in 3 days."))
    after = legacy()
    assert after[: len(before)] == before                           # derived facts first, unchanged
    assert [f["id"] for f in after] == [f"f{i + 1}" for i in range(len(after))]
    tail = after[len(before):]
    assert [f["text"] for f in tail] == ["Widget weighs 2 kg.", "Widget ships in 3 days."]
    assert tail[0]["key"] == "a-one" and tail[0]["version"] == 1 and tail[0]["scope_note"] is None


def test_legacy_leaves_out_expired_draft_restricted_retired(owner):
    add(owner, fact("gone", text="Old offer.", valid_to="2020-01-01"),
        fact("later", text="Future offer.", valid_from="2099-01-01"),
        fact("secret", text="Margin is 40%.", sensitivity="restricted"),
        fact("inside", text="Staff rota is weekly.", sensitivity="internal"),
        fact("bye", text="Retired fact."),
        fact("ok", text="Current fact."))
    add(owner, fact("wip", text="Draft fact."), confirm=False)
    assert client.post("/facts/v2/bye/retire", headers=owner).status_code == 200
    texts = [f["text"] for f in legacy()]
    for t in ("Old offer.", "Future offer.", "Margin is 40%.", "Draft fact.", "Retired fact."):
        assert t not in texts
    assert "Current fact." in texts and "Staff rota is weekly." in texts   # default max internal
    pub = [f["text"] for f in legacy(max_sensitivity="public")]
    assert "Staff rota is weekly." not in pub and "Current fact." in pub
    assert "Margin is 40%." not in [f["text"] for f in legacy(max_sensitivity="restricted")]
    assert client.get("/facts", params={"max_sensitivity": "secret"}).status_code == 422


def test_legacy_renders_scope_note(owner):
    add(owner, fact("parking", text="Parking is free.", scope={"sites": ["Quayside"]}),
        fact("multi", text="Pro plan has SSO", scope={"plan_tiers": ["Pro", "Enterprise"], "regions": ["UK"]}))
    tail = {f["key"]: f for f in legacy() if "key" in f}
    assert tail["parking"]["text"] == "Parking is free (only: sites Quayside)."
    assert tail["parking"]["scope_note"] == "only: sites Quayside"
    assert tail["multi"]["text"] == "Pro plan has SSO (only: regions UK; plan tiers Pro, Enterprise)"


# --- owner key ----------------------------------------------------------------------------

def test_confirm_refused_when_key_unset_missing_or_wrong(monkeypatch):
    assert client.post("/facts/v2", json=fact("k1")).status_code == 200
    assert client.post("/facts/v2/k1/confirm").status_code == 503          # FACT_OWNER_KEY unset
    assert client.post("/facts/v2/k1/confirm", headers={"X-Owner-Key": "x"}).status_code == 503
    monkeypatch.setenv("FACT_OWNER_KEY", OWNER)
    assert client.post("/facts/v2/k1/confirm").status_code == 403
    assert client.post("/facts/v2/k1/confirm", headers={"X-Owner-Key": "wrong"}).status_code == 403
    assert client.post("/facts/v2/k1/retire", headers={"X-Owner-Key": "wrong"}).status_code == 403
    assert client.post("/facts/v2/import", json={"facts": []}, headers={"X-Owner-Key": "wrong"}).status_code == 403
    assert client.post("/starter-kits/saas/apply", headers={"X-Owner-Key": "wrong"}).status_code == 403
    assert client.get("/facts/v2/k1").json()["status"] == "draft"
    r = client.post("/facts/v2/k1/confirm", headers={"X-Owner-Key": OWNER})
    assert r.status_code == 200 and r.json()["status"] == "active"


def test_owner_routes_also_need_the_api_key(monkeypatch, owner):
    monkeypatch.setenv("INTERNAL_API_KEY", "k-1")
    assert client.post("/facts/v2/import", json={"facts": []}, headers=owner).status_code == 401
    assert client.get("/facts/v2").status_code == 401
    assert client.get("/facts/query").status_code == 401
    assert client.get("/starter-kits").status_code == 401
    assert client.get("/questions").status_code == 401
    ok = client.post("/facts/v2/import", json={"facts": []}, headers=owner | {"X-API-Key": "k-1"})
    assert ok.status_code == 200


# --- versions -----------------------------------------------------------------------------

def test_versions_append_only_and_confirm_switches_served_version(env, owner):
    add(owner, fact("rate", text="Rate is £180."))
    r = client.put("/facts/v2/rate", json=fact("rate", text="Rate is £190."))
    assert r.status_code == 200 and r.json()["latest_version"] == 2 and r.json()["version"] == 1
    one = client.get("/facts/v2/rate").json()
    assert one["status"] == "active" and one["version"] == 1 and one["latest_version"] == 2
    assert [v["version"] for v in one["versions"]] == [1, 2]
    assert [v["status"] for v in one["versions"]] == ["active", "draft"]
    assert "Rate is £180." in [f["text"] for f in legacy()]         # pending draft not served
    client.post("/facts/v2/rate/confirm", headers=owner)
    one = client.get("/facts/v2/rate").json()
    assert one["version"] == 2 and [v["status"] for v in one["versions"]] == ["superseded", "active"]
    texts = [f["text"] for f in legacy()]
    assert "Rate is £190." in texts and "Rate is £180." not in texts

    conn = sqlite3.connect(env / "facts.sqlite")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE fact_versions SET data = '{}' WHERE key = 'rate'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM fact_versions WHERE key = 'rate'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE changes SET kind = 'x'")
    conn.close()
    assert "Rate is £180." in client.get("/facts/v2/rate").json()["versions"][0]["data"]["text"]


def test_post_existing_key_adds_draft_version_or_conflicts(owner):
    assert client.post("/facts/v2", json=fact("kk", text="One.")).json()["version"] == 1
    assert client.post("/facts/v2", json=fact("kk", text="Two.")).json()["version"] == 2
    client.post("/facts/v2/kk/confirm", headers=owner)
    assert client.post("/facts/v2", json=fact("kk", text="Three.")).status_code == 409
    assert client.put("/facts/v2/missing", json=fact("missing")).status_code == 404
    assert client.put("/facts/v2/kk", json=fact("other")).status_code == 422   # key mismatch


@pytest.mark.parametrize("patch,field", [
    ({"key": "Bad Key"}, "key"),
    ({"key": "x"}, "key"),
    ({"fact_type": "rumour"}, "fact_type"),
    ({"subject": {"kind": "planet", "ref": "x"}}, "subject"),
    ({"sensitivity": "secret"}, "sensitivity"),
    ({"basis": "per_galaxy"}, "basis"),
    ({"claim_class": "magic"}, "claim_class"),
    ({"text": "x" * 501}, "text"),
    ({"text": ""}, "text"),
    ({"valid_from": "2026-13-01"}, "valid_from"),
    ({"valid_from": "2026-10-02", "valid_to": "2026-10-01"}, "valid_to"),
    ({"scope": {"planets": ["Mars"]}}, "scope"),
    ({"currency": "pounds"}, "currency"),
    ({"surprise": 1}, "surprise"),
])
def test_fact_validation(patch, field):
    r = client.post("/facts/v2", json=fact("valid-key") | patch)
    assert r.status_code == 422, r.text
    assert any(field in [str(p) for p in e["loc"]] or field in e["msg"] for e in r.json()["detail"]), r.text


# --- fact_set_version ---------------------------------------------------------------------

def test_fact_set_version_changes_on_confirm_and_on_overrides_change(owner):
    fs0 = query()["fact_set_version"]
    assert fs0.startswith("fs-0-") and len(fs0.split("-")[2]) == 8
    client.post("/facts/v2", json=fact("new"))
    assert query()["fact_set_version"].split("-")[2] == fs0.split("-")[2]   # a draft doesn't change the set
    client.post("/facts/v2/new/confirm", headers=owner)
    fs1 = query()["fact_set_version"]
    assert fs1 != fs0
    assert query()["fact_set_version"] == fs1                            # stable when nothing changes
    r = client.put("/brand/editable", json={"facts": ["We only sell tea now."]})
    assert r.status_code == 200
    fs2 = query()["fact_set_version"]
    assert fs2 != fs1 and fs2.split("-")[1] == fs1.split("-")[1]          # same seq, other hash
    assert client.get("/facts/v2").json()["fact_set_version"] == fs2


# --- query --------------------------------------------------------------------------------

def test_query_validity_boundaries(owner):
    add(owner, fact("summer", valid_from="2026-06-01", valid_to="2026-10-01"),
        fact("winter", valid_from="2026-10-05"))
    q = query(at="2026-10-01")
    assert "summer" in served(q) and reasons(q)["winter"] == "not_yet_valid"
    q = query(at="2026-10-02")
    assert reasons(q)["summer"] == "expired" and "summer" not in served(q)
    q = query(at="2026-10-05")
    assert "winter" in served(q)
    assert reasons(query(at="2026-05-31"))["summer"] == "not_yet_valid"
    assert query(at="2026-10-02")["at"] == "2026-10-02"
    assert client.get("/facts/query", params={"at": "tomorrow"}).status_code == 422


def test_query_scope_unspecified_vs_out_of_scope(owner):
    add(owner, fact("quay", scope={"sites": ["Quayside"]}),
        fact("pro", scope={"plan_tiers": ["Pro", "Enterprise"], "channels": ["email"]}),
        fact("everywhere"))
    q = query()
    assert reasons(q)["quay"] == "scope_unspecified" and "everywhere" in served(q)
    assert reasons(query(site="Leeds"))["quay"] == "out_of_scope"
    assert "quay" in served(query(site="quayside"))                       # case-insensitive
    assert reasons(query(site=["Quayside", "Leeds"]))["quay"] == "out_of_scope"   # all named must fit
    assert reasons(query(plan_tier="Pro"))["pro"] == "scope_unspecified"  # channel still unnamed
    assert "pro" in served(query(plan_tier="Pro", channel="email"))
    assert reasons(query(plan_tier="Basic"))["pro"] == "out_of_scope"     # out beats unspecified
    assert "everywhere" in served(query(site="Leeds", plan_tier="Basic"))


def test_query_sensitivity_and_status_reasons(owner):
    add(owner, fact("pub"), fact("int", sensitivity="internal"), fact("res", sensitivity="restricted"),
        fact("old", valid_to="2020-01-01"), fact("gone"))
    add(owner, fact("wip"), confirm=False)
    client.post("/facts/v2/gone/retire", headers=owner)
    q = query()
    assert {"pub", "int"} <= served(q)
    want = {"res": "restricted", "old": "expired", "wip": "draft", "gone": "retired"}
    assert {k: reasons(q)[k] for k in want} == want
    q = query(max_sensitivity="public")
    assert reasons(q)["int"] == "internal" and "pub" in served(q)
    assert "res" in served(query(max_sensitivity="restricted"))
    listed = {f["key"]: f for f in client.get("/facts/v2").json()["facts"]}
    assert listed["old"]["status"] == "expired"                            # reported, not stored
    assert [f["key"] for f in client.get("/facts/v2", params={"status": "draft"}).json()["facts"]] == ["wip"]


def test_expired_is_recorded_once_in_changes(owner):
    add(owner, fact("old", valid_to="2020-01-01"))
    query(), query(), legacy()
    kinds = [c["kind"] for c in client.get("/facts/changes").json()["changes"] if c["key"] == "old"]
    assert kinds == ["created", "confirmed", "expired"]


def test_changes_feed_since(owner):
    add(owner, fact("aa"))
    feed = client.get("/facts/changes").json()
    seq = feed["seq"]
    assert [(c["key"], c["kind"]) for c in feed["changes"]] == [("aa", "created"), ("aa", "confirmed")]
    client.put("/facts/v2/aa", json=fact("aa", text="Changed."))
    later = client.get("/facts/changes", params={"since": seq}).json()
    assert [(c["key"], c["version"], c["kind"]) for c in later["changes"]] == [("aa", 2, "updated")]
    assert later["seq"] == seq + 1


# --- derived facts ------------------------------------------------------------------------

def test_derived_facts_listed_read_only_and_convertible(owner):
    listed = client.get("/facts/v2").json()["facts"]
    derived = [f for f in listed if f.get("derived")]
    assert len(derived) == len(legacy())
    price = next(f for f in derived if f["key"] == "price-desk-blend")
    assert price["text"] == "Desk Blend costs $18 / 340 g." and price["sensitivity"] == "public"
    assert price["scope"]["sites"] == [] and price["status"] == "active"
    assert all(f["key"].startswith(("legacy-", "price-")) for f in derived)
    assert client.post("/facts/v2/price-desk-blend/confirm", headers=owner).status_code == 409
    assert client.put("/facts/v2/price-desk-blend", json=fact("price-desk-blend")).status_code == 409
    assert client.get("/facts/v2/price-desk-blend").json()["derived"] is True

    new = fact("desk-blend-price", fact_type="price", subject={"kind": "product", "ref": "Desk Blend"},
               text="Desk Blend costs $19 / 340 g.", value=19, currency="USD", supersedes_key="price-desk-blend")
    assert client.post("/facts/v2", json=new).status_code == 200
    assert "Desk Blend costs $18 / 340 g." in [f["text"] for f in legacy()]   # draft: old still served
    client.post("/facts/v2/desk-blend-price/confirm", headers=owner)
    texts = [f["text"] for f in legacy()]
    assert "Desk Blend costs $18 / 340 g." not in texts and "Desk Blend costs $19 / 340 g." in texts
    ids = [f["id"] for f in legacy()]
    assert ids == [f"f{i + 1}" for i in range(len(ids))]
    assert reasons(query())["price-desk-blend"] == "superseded"
    assert client.post("/facts/v2", json=fact("x", supersedes_key="no-such-key")).status_code == 422


def test_query_includes_derived_facts():
    q = query(site="Leeds")
    assert "price-desk-blend" in served(q)
    assert all(f["derived"] for f in q["facts"])


def test_supersede_stored_fact(owner):
    add(owner, fact("old-rate", text="Old rate."))
    add(owner, fact("new-rate", text="New rate.", supersedes_key="old-rate"))
    assert client.get("/facts/v2/old-rate").json()["status"] == "superseded"
    assert reasons(query())["old-rate"] == "superseded"
    assert "Old rate." not in [f["text"] for f in legacy()]


def test_supersede_takes_effect_on_the_successor_start_date(owner, monkeypatch):
    """A successor confirmed today but starting later leaves the old fact valid until then."""
    monkeypatch.setenv("FACTS_TODAY", "2031-03-10")
    add(owner, fact("summer-hours", text="The kiosk opens 7am to 9pm.", valid_from="2031-01-01"),
        fact("winter-hours", text="The kiosk opens 8am to 5pm.", valid_from="2031-04-01",
             supersedes_key="summer-hours"))
    # today (legacy /facts, /facts/v2) the old fact is still the one in force
    assert client.get("/facts/v2/summer-hours").json()["status"] == "active"
    texts = [f["text"] for f in legacy()]
    assert "The kiosk opens 7am to 9pm." in texts and "The kiosk opens 8am to 5pm." not in texts
    # per date: before the successor starts, the old one; from its start date, the new one
    before, first, later = query(at="2031-03-31"), query(at="2031-04-01"), query(at="2031-06-01")
    assert "summer-hours" in served(before) and reasons(before)["winter-hours"] == "not_yet_valid"
    assert reasons(first)["summer-hours"] == "superseded" and "winter-hours" in served(first)
    assert reasons(later)["summer-hours"] == "superseded"
    kinds = [c["kind"] for c in client.get("/facts/changes").json()["changes"] if c["key"] == "summer-hours"]
    assert "superseded" not in kinds                     # nothing has happened yet
    # the start date arrives: now it is superseded for legacy readers too, and pollers hear once
    monkeypatch.setenv("FACTS_TODAY", "2031-04-01")
    assert client.get("/facts/v2/summer-hours").json()["status"] == "superseded"
    texts = [f["text"] for f in legacy()]
    assert "The kiosk opens 7am to 9pm." not in texts and "The kiosk opens 8am to 5pm." in texts
    legacy(), query()
    kinds = [c["kind"] for c in client.get("/facts/changes").json()["changes"] if c["key"] == "summer-hours"]
    assert kinds.count("superseded") == 1
    # a query for a day before the switch still answers with the old fact
    assert "summer-hours" in served(query(at="2031-03-20"))


def test_supersede_without_start_date_or_already_started_is_immediate(owner, monkeypatch):
    """Negative control: nothing is deferred when the successor is already in force."""
    monkeypatch.setenv("FACTS_TODAY", "2031-03-10")
    add(owner, fact("fee-a", text="Fee A."), fact("fee-b", text="Fee B.", supersedes_key="fee-a"),
        fact("fee-c", text="Fee C."), fact("fee-d", text="Fee D.", valid_from="2031-03-10", supersedes_key="fee-c"))
    for old in ("fee-a", "fee-c"):
        assert client.get(f"/facts/v2/{old}").json()["status"] == "superseded"
        assert reasons(query())[old] == "superseded"


def test_future_successor_of_a_derived_fact_hides_it_only_from_its_start(owner, monkeypatch):
    monkeypatch.setenv("FACTS_TODAY", "2031-03-10")
    old_text = "Desk Blend costs $18 / 340 g."
    assert old_text in [f["text"] for f in legacy()]
    add(owner, fact("desk-blend-2031", fact_type="price", subject={"kind": "product", "ref": "Desk Blend"},
                    text="Desk Blend costs $21 / 340 g.", value=21, currency="USD", valid_from="2031-05-01",
                    supersedes_key="price-desk-blend"))
    assert old_text in [f["text"] for f in legacy()]
    assert "price-desk-blend" in served(query(at="2031-04-30"))
    assert reasons(query(at="2031-05-01"))["price-desk-blend"] == "superseded"


# --- import -------------------------------------------------------------------------------

def test_import_bulk_confirm_idempotent_and_all_or_nothing(owner):
    body = {"facts": [fact(f"k-{i}") for i in range(20)], "confirm": True}
    r = client.post("/facts/v2/import", json=body, headers=owner).json()
    assert r["created"] == 20 and r["confirmed"] == 20
    again = client.post("/facts/v2/import", json=body, headers=owner).json()
    assert again["unchanged"] == 20 and again["created"] == 0
    bad = {"facts": [fact("fine-one"), fact("BAD KEY")], "confirm": True}
    r = client.post("/facts/v2/import", json=bad, headers=owner)
    assert r.status_code == 422
    assert client.get("/facts/v2/fine-one").status_code == 404
    drafts = client.post("/facts/v2/import", json={"facts": [fact("d-1")], "confirm": False}, headers=owner).json()
    assert drafts["confirmed"] == 0 and client.get("/facts/v2/d-1").json()["status"] == "draft"


def test_import_keeps_explicit_expired_status(owner):
    add(owner, fact("was", text="Was true.", status="expired"))
    assert reasons(query())["was"] == "expired"
    assert "Was true." not in [f["text"] for f in legacy()]


def test_import_accepts_round_trip_of_listing(owner):
    add(owner, fact("rt", scope={"sites": ["A"]}))
    got = client.get("/facts/v2/rt").json()
    r = client.post("/facts/v2/import", json={"facts": [got], "confirm": True}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["unchanged"] == 1


# --- questions ----------------------------------------------------------------------------

def test_questions_crud():
    assert client.get("/questions").json() == {"questions": []}
    r = client.post("/questions", json={"kind": "missing_fact", "text": "What is the weekend rate?",
                                        "task_id": "T-ABC123"})
    assert r.status_code == 200
    q = r.json()
    assert q["status"] == "open" and q["kind"] == "missing_fact" and q["fact_key"] is None and q["created_at"]
    q2 = client.post("/questions", json={"kind": "scope", "fact_key": "rate", "text": "Which site?"}).json()
    assert client.post(f"/questions/{q['id']}/answer", json={"answer": "£210."}).json()["status"] == "answered"
    assert client.post(f"/questions/{q['id']}/answer", json={"answer": "again"}).status_code == 409
    assert client.post(f"/questions/{q2['id']}/dismiss").json()["status"] == "dismissed"
    assert [x["id"] for x in client.get("/questions", params={"status": "open"}).json()["questions"]] == []
    assert len(client.get("/questions").json()["questions"]) == 2
    assert client.post("/questions/999/dismiss").status_code == 404
    assert client.post("/questions", json={"kind": "gossip", "text": "x"}).status_code == 422
    assert client.post("/questions", json={"kind": "confirm", "text": "x" * 1001}).status_code == 422


# --- starter kits -------------------------------------------------------------------------

KITS = {"hospitality", "manufacturing", "saas", "retail", "clinic", "restaurant", "consultancy", "ethiopia-alcohol", "ethiopia-hospitality"}


def test_starter_kits_load_and_validate():
    kits = client.get("/starter-kits").json()
    assert {k["id"] for k in kits} == KITS
    for k in kits:
        assert k["label"] and k["fact_types"] and k["claim_classes"] and k["required_disclosures"]
        assert len(k["forbidden_phrasing"]) >= 3
        assert all(p["phrase"] and p["why"] for p in k["forbidden_phrasing"])
    by = {k["id"]: k for k in kits}
    phrases = {kid: " | ".join(p["phrase"].lower() for p in k["forbidden_phrasing"]) for kid, k in by.items()}
    assert "ul listed" in phrases["manufacturing"] and "fda approved" in phrases["manufacturing"]
    assert "soc 2 type ii" in phrases["saas"] and "unlimited" in phrases["saas"]
    assert "specialist" in phrases["clinic"] and "gluten-free" in phrases["restaurant"]
    assert "google partner" in phrases["consultancy"] and "breakfast included" in phrases["hospitality"]
    assert "was £" in phrases["retail"]


def test_starter_kit_files_validate_directly():
    from app.facts_store import load_starter_kits
    kits = load_starter_kits()
    assert set(kits) == KITS


def test_apply_starter_kit_creates_draft_rules_idempotently(owner):
    assert client.post("/starter-kits/nope/apply", headers=owner).status_code == 404
    r = client.post("/starter-kits/saas/apply", headers=owner)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] > 0 and all(x["status"] == "draft" for x in body["rules"])
    assert {x["kind"] for x in body["rules"]} == {"forbidden_phrase", "required_disclosure"}
    again = client.post("/starter-kits/saas/apply", headers=owner).json()
    assert again["created"] == 0 and again["existing"] == body["created"]
    rules = client.get("/rules").json()["rules"]
    assert len(rules) == body["created"]
    # rules are not facts: legacy /facts and the query are untouched
    assert client.get("/facts").content == GOLDEN.read_bytes()
    rid = rules[0]["id"]
    assert client.post(f"/rules/{rid}/confirm").status_code == 403
    assert client.post(f"/rules/{rid}/confirm", headers=owner).json()["status"] == "active"
    assert client.get("/rules", params={"status": "active"}).json()["rules"][0]["id"] == rid


def test_store_path_unwritable_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTS_DB", str(tmp_path / "no" / "such" / "dir" / "facts.sqlite"))
    r = client.post("/facts/v2", json=fact("k1"))
    assert r.status_code == 500 and "FACTS_DB" in r.json()["detail"]
    assert client.get("/facts").content == GOLDEN.read_bytes()
    assert os.path.exists(tmp_path) and not (tmp_path / "no").exists()


# --- storage robustness -------------------------------------------------------------------

def test_migrates_a_first_schema_database(env, owner):
    """A store created before latest_version / supersedes_key / changes.actor existed."""
    import json
    conn = sqlite3.connect(env / "facts.sqlite")
    conn.executescript("""
        CREATE TABLE facts (key TEXT PRIMARY KEY, current_version INTEGER NOT NULL, status TEXT NOT NULL,
            subject_kind TEXT, subject_ref TEXT, fact_type TEXT, sensitivity TEXT, valid_from TEXT,
            valid_to TEXT, review_by TEXT, updated_at TEXT NOT NULL);
        CREATE TABLE fact_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, version INTEGER NOT NULL,
            data TEXT NOT NULL, created_by TEXT, confirmed_by TEXT, created_at TEXT NOT NULL, UNIQUE (key, version));
        CREATE TABLE changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, version INTEGER,
            kind TEXT NOT NULL, at TEXT NOT NULL);
    """)
    from app.facts_store import FactIn
    data = FactIn(**fact("legacy-db", text="Stored before the upgrade.", sensitivity="public")).stored()
    conn.execute("INSERT INTO facts VALUES ('legacy-db', 1, 'active', 'product', 'Widget', 'spec', 'public',"
                 " NULL, NULL, NULL, '2026-01-01T00:00:00Z')")
    conn.execute("INSERT INTO fact_versions (key, version, data, created_at) VALUES (?, 1, ?, ?)",
                 ("legacy-db", json.dumps(data), "2026-01-01T00:00:00Z"))
    conn.commit()
    conn.close()
    assert "Stored before the upgrade." in [f["text"] for f in legacy()]
    assert client.get("/facts/v2/legacy-db").json()["latest_version"] == 1
    client.put("/facts/v2/legacy-db", json=fact("legacy-db", text="After the upgrade.", sensitivity="public"))
    client.post("/facts/v2/legacy-db/confirm", headers=owner)
    assert "After the upgrade." in [f["text"] for f in legacy()]
    conn = sqlite3.connect(env / "facts.sqlite")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM fact_versions")   # triggers added to the old file too
    conn.close()


def test_parallel_writes_keep_versions_and_seq_consistent(owner):
    import threading
    errors = []

    def worker(n):
        for i in range(8):
            r = client.post("/facts/v2", json=fact(f"t{n}-{i}"))
            if r.status_code != 200:
                errors.append(r.text)
            r = client.post("/facts/v2", json=fact("shared-key", text=f"Draft from {n}-{i}."))
            if r.status_code != 200:
                errors.append(r.text)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    one = client.get("/facts/v2/shared-key").json()
    assert [v["version"] for v in one["versions"]] == list(range(1, 49))
    seqs = [c["seq"] for c in client.get("/facts/changes").json()["changes"]]
    assert seqs == sorted(set(seqs)) and len(seqs) == 6 * 8 * 2
