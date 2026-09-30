"""Disclosure wordings: a business's own words for a required disclosure, proposed from an
accepted missing_disclosure finding (88) and served on the fact once the owner confirms."""
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE.parent / "config" / "brand.yaml"
GOLDEN = HERE / "golden" / "legacy_facts.json"
OWNER = "owner-test-key"
client = TestClient(app)
DISC = "Subject to availability"


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
    body = {"key": key, "subject": {"kind": "offer", "ref": "Spring offer"}, "fact_type": "policy",
            "text": f"Fact {key} is true.", "sensitivity": "public", "required_disclosures": [DISC],
            "allowed_phrasing": ["spring offer"]}
    body.update(kw)
    return body


def add(owner_h, *facts, confirm=True):
    r = client.post("/facts/v2/import", json={"facts": list(facts), "confirm": confirm}, headers=owner_h)
    assert r.status_code == 200, r.text


def propose(**kw):
    body = {"fact_key": "spring", "disclosure": DISC, "wording": "while stocks last", "task_id": "t-1",
            "proposed_by": "Sam"}
    body.update(kw)
    return client.post("/disclosure-wordings", json=body)


def served(path, key, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    return next(f for f in r.json()["facts"] if f["key"] == key)


def test_draft_then_confirm_is_served(owner):
    add(owner, fact("spring"))
    r = propose(disclosure="  subject   TO availability ")
    assert r.status_code == 201, r.text
    row = r.json()
    assert set(row) == {"id", "fact_key", "disclosure", "wording", "status", "proposed_by", "task_id",
                        "created_at", "decided_by", "decided_at"}
    assert row["status"] == "draft" and row["disclosure"] == DISC and row["wording"] == "while stocks last"
    assert row["proposed_by"] == "Sam" and row["task_id"] == "t-1" and row["decided_by"] is None
    # a draft is not served
    assert served("/facts/query", "spring")["disclosure_wordings"] == []
    assert served("/facts/v2", "spring")["disclosure_wordings"] == []

    c = client.post(f"/disclosure-wordings/{row['id']}/confirm", headers=owner | {"X-Actor": "Owner Ann"})
    assert c.status_code == 200, c.text
    assert c.json()["status"] == "active" and c.json()["decided_by"] == "Owner Ann" and c.json()["decided_at"]
    assert served("/facts/query", "spring")["disclosure_wordings"] == ["while stocks last"]
    assert served("/facts/v2", "spring")["disclosure_wordings"] == ["while stocks last"]
    one = client.get("/facts/v2/spring").json()
    assert one["disclosure_wordings"] == ["while stocks last"]
    assert one["allowed_phrasing"] == ["spring offer"]          # never merged
    # derived facts carry the field too (always empty)
    assert served("/facts/v2", "price-desk-blend")["disclosure_wordings"] == []
    assert client.get("/facts/v2/price-desk-blend").json()["disclosure_wordings"] == []


def test_list_filters_newest_first(owner):
    add(owner, fact("spring"), fact("autumn"))
    a = propose(wording="while stocks last").json()
    b = propose(fact_key="autumn", wording="limited numbers").json()
    assert [w["id"] for w in client.get("/disclosure-wordings").json()["wordings"]] == [b["id"], a["id"]]
    assert [w["id"] for w in client.get("/disclosure-wordings", params={"fact_key": "spring"}).json()["wordings"]] == [a["id"]]
    client.post(f"/disclosure-wordings/{b['id']}/confirm", headers=owner)
    assert [w["id"] for w in client.get("/disclosure-wordings", params={"status": "active"}).json()["wordings"]] == [b["id"]]
    assert client.get("/disclosure-wordings", params={"status": "nope"}).status_code == 422


def test_list_without_store_is_empty():
    assert client.get("/disclosure-wordings").json() == {"wordings": []}


def test_dismiss_is_not_served_and_stays_dismissed(owner):
    add(owner, fact("spring"))
    row = propose().json()
    d = client.post(f"/disclosure-wordings/{row['id']}/dismiss", headers=owner | {"X-Actor": "Ann"})
    assert d.status_code == 200 and d.json()["status"] == "dismissed" and d.json()["decided_by"] == "Ann"
    assert served("/facts/query", "spring")["disclosure_wordings"] == []
    again = propose(wording="While  STOCKS last")
    assert again.status_code == 200 and again.json()["id"] == row["id"] and again.json()["status"] == "dismissed"
    assert len(client.get("/disclosure-wordings").json()["wordings"]) == 1


def test_dedupe_returns_existing_row(owner):
    add(owner, fact("spring"))
    first = propose()
    assert first.status_code == 201
    dup = propose(wording="  While stocks   LAST ", proposed_by="Someone else")
    assert dup.status_code == 200 and dup.json() == first.json()
    assert len(client.get("/disclosure-wordings").json()["wordings"]) == 1
    # a different wording is a new row
    assert propose(wording="until they are gone").status_code == 201


def test_owner_key_unset_wrong_and_unknown_id(monkeypatch, owner):
    add(owner, fact("spring"))
    row = propose().json()
    monkeypatch.delenv("FACT_OWNER_KEY")
    assert client.post(f"/disclosure-wordings/{row['id']}/confirm", headers=owner).status_code == 503
    assert client.post(f"/disclosure-wordings/{row['id']}/dismiss", headers=owner).status_code == 503
    monkeypatch.setenv("FACT_OWNER_KEY", OWNER)
    assert client.post(f"/disclosure-wordings/{row['id']}/confirm").status_code == 403
    assert client.post(f"/disclosure-wordings/{row['id']}/confirm", headers={"X-Owner-Key": "wrong"}).status_code == 403
    assert client.post(f"/disclosure-wordings/{row['id']}/dismiss", headers={"X-Owner-Key": "wrong"}).status_code == 403
    assert client.post("/disclosure-wordings/9999/confirm", headers=owner).status_code == 404
    assert client.post("/disclosure-wordings/9999/dismiss", headers=owner).status_code == 404
    assert served("/facts/query", "spring")["disclosure_wordings"] == []


def test_api_key_needed(monkeypatch, owner):
    add(owner, fact("spring"))
    monkeypatch.setenv("INTERNAL_API_KEY", "k-1")
    assert propose().status_code == 401
    assert client.get("/disclosure-wordings").status_code == 401
    assert client.post("/disclosure-wordings", headers={"X-API-Key": "k-1"},
                       json={"fact_key": "spring", "disclosure": DISC, "wording": "while stocks last"}).status_code == 201


def test_404_and_422(owner):
    assert propose().status_code == 404                        # no store yet
    add(owner, fact("spring"), fact("plain", required_disclosures=[]))
    assert propose(fact_key="nope").status_code == 404
    assert propose(disclosure="Terms apply").status_code == 422
    assert propose(fact_key="plain").status_code == 422
    assert propose(fact_key="price-desk-blend").status_code == 422   # derived: no disclosures
    assert propose(wording="").status_code == 422
    assert propose(wording="   ").status_code == 422
    assert propose(wording="x" * 201).status_code == 422
    assert propose(disclosure="x" * 201).status_code == 422
    assert propose(wording="x" * 200).status_code == 201
    assert client.post("/disclosure-wordings", json={"fact_key": "spring", "disclosure": DISC}).status_code == 422
    assert propose(task_id=None, proposed_by=None).status_code == 201   # optional fields


def test_draft_fact_disclosures_count(owner):
    add(owner, fact("wip"), confirm=False)
    assert propose(fact_key="wip").status_code == 201


def test_decisions_write_no_change_entry(owner):
    add(owner, fact("spring"))
    before = client.get("/facts/changes").json()
    fs_before = client.get("/facts/query").json()["fact_set_version"]
    a = propose().json()
    b = propose(wording="until they are gone").json()
    client.post(f"/disclosure-wordings/{a['id']}/confirm", headers=owner)
    client.post(f"/disclosure-wordings/{b['id']}/dismiss", headers=owner)
    assert client.get("/facts/changes").json() == before
    assert client.get("/facts/query").json()["fact_set_version"] == fs_before
    assert client.get("/facts/v2/spring").json()["versions"][-1]["version"] == 1


def test_legacy_facts_unchanged_by_wordings(owner, env):
    add(owner, confirm=True)                  # store exists, empty
    assert client.get("/facts").content == GOLDEN.read_bytes()
    add(owner, fact("spring", text="Spring offer: 10% off."))
    legacy_before = client.get("/facts").content
    row = propose().json()
    client.post(f"/disclosure-wordings/{row['id']}/confirm", headers=owner)
    assert client.get("/facts").content == legacy_before
    assert b"while stocks last" not in client.get("/facts").content


def test_listing_round_trips_through_import(owner):
    add(owner, fact("spring"))
    row = propose().json()
    client.post(f"/disclosure-wordings/{row['id']}/confirm", headers=owner)
    got = served("/facts/v2", "spring")
    assert got["disclosure_wordings"] == ["while stocks last"]
    r = client.post("/facts/v2/import", json={"facts": [got], "confirm": True}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["unchanged"] == 1


def test_migrates_existing_database_without_the_table(env, owner):
    import sqlite3
    db = env / "facts.sqlite"
    add(owner, fact("spring"))
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE disclosure_wordings")
    from app import facts_store
    facts_store._migrated.discard(str(db))
    assert propose().status_code == 201
    assert served("/facts/query", "spring")["disclosure_wordings"] == []
