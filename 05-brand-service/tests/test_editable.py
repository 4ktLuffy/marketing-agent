"""Editable brand: overrides over the read-only brand.yaml, validation, reset, completeness."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app, merge_brand

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "brand.yaml"
client = TestClient(app)


@pytest.fixture(autouse=True)
def files(tmp_path, monkeypatch):
    brand = tmp_path / "brand.yaml"
    shutil.copy(EXAMPLE, brand)
    over = tmp_path / "brand.overrides.json"
    monkeypatch.setenv("BRAND_FILE", str(brand))
    monkeypatch.setenv("BRAND_OVERRIDES_FILE", str(over))
    monkeypatch.setenv("VOICE_FILE", str(tmp_path / "voice.json"))
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    return brand, over


def put(body, **kw):
    return client.put("/brand/editable", json=body, **kw)


def errors(r):
    assert r.status_code == 422, r.text
    return {".".join(str(p) for p in e["loc"][1:]): e["msg"] for e in r.json()["detail"]}


def test_editable_without_overrides_is_the_base():
    doc = client.get("/brand/editable").json()
    assert doc["brand"]["name"] == "Northwind Roasters"
    assert doc["brand"]["audience"]["primary"].startswith("Remote workers")
    assert doc["overridden"] == [] and doc["products"] == {"changed": [], "added": [], "removed": []}
    decaf = doc["brand"]["products"][3]
    assert decaf == {"name": "Swiss Water decaf", "one_line": "Roasted in small batches every Tuesday.",
                     "price": None, "aliases": ["decaf"]}


def test_price_edit_changes_facts_summary_and_profile(files):
    _, over = files
    prods = client.get("/brand/editable").json()["brand"]["products"]
    prods[0]["price"] = "$19 / 340 g"
    r = put({"products": prods})
    assert r.status_code == 200, r.text
    assert r.json()["overridden"] == ["products"] and r.json()["products"]["changed"] == ["Desk Blend"]
    facts = [f["text"] for f in client.get("/facts").json()["facts"]]
    assert "Desk Blend costs $19 / 340 g." in facts and "Desk Blend costs $18 / 340 g." not in facts
    assert "Desk Blend ($19 / 340 g)" in client.get("/profile/summary").json()["summary"]
    assert client.get("/profile").json()["products"][0]["price"] == "$19 / 340 g"
    # only the changed product is stored; the base yaml is untouched
    stored = json.loads(over.read_text())
    assert [p["name"] for p in stored["products"]] == ["Desk Blend"]
    assert "$18 / 340 g" in EXAMPLE.read_text()


def test_reset_restores_base(files):
    _, over = files
    put({"name": "Acme Tea", "facts": ["We sell tea."]})
    assert client.get("/profile").json()["name"] == "Acme Tea"
    assert client.delete("/brand/editable").json() == {"deleted": True}
    assert not over.exists()
    assert client.get("/profile").json()["name"] == "Northwind Roasters"
    assert len(client.get("/profile").json()["facts"]) == 7
    assert client.delete("/brand/editable").json() == {"deleted": False}


def test_partial_put_keeps_other_fields_and_reports_overridden():
    put({"name": "Acme Tea"})
    doc = put({"facts": ["Our tea is packed in Kenya.", "Bags hold 100 g."]}).json()
    assert doc["brand"]["name"] == "Acme Tea" and len(doc["brand"]["facts"]) == 2
    assert doc["overridden"] == ["facts", "name"]
    # setting a field back to the base value drops it from the overrides
    doc = put({"name": "Northwind Roasters"}).json()
    assert doc["overridden"] == ["facts"]


def test_nested_mappings_merge_key_by_key():
    doc = put({"audience": {"primary": "Tea lovers in offices."},
               "emoji_policy": {"max_per_post": 1, "allowed": ["🍵"]}}).json()
    assert doc["brand"]["audience"] == {"primary": "Tea lovers in offices.",
                                        "secondary": "Small distributed teams that want a shared coffee perk."}
    prof = client.get("/profile").json()
    assert prof["audience"]["pains"]                      # not editable here, kept from the base
    assert prof["emoji_policy"]["note"].startswith("One or two")
    assert prof["emoji_policy"]["not_allowed_severity"] == "error"
    body = client.post("/check", json={"text": "Fresh tea ☕"}).json()
    assert any(v["rule"] == "emoji_not_allowed" for v in body["violations"])


def test_products_merge_by_name_add_and_remove():
    prods = client.get("/brand/editable").json()["brand"]["products"]
    prods = [p for p in prods if p["name"] != "Team Box"]
    prods.append({"name": "Cold Brew Pack", "one_line": "Four bottles of cold brew.", "price": "$24", "aliases": []})
    doc = put({"products": prods}).json()
    assert [p["name"] for p in doc["brand"]["products"]] == \
        ["Desk Blend", "Single-Origin Rotation", "Swiss Water decaf", "Cold Brew Pack"]
    assert doc["products"] == {"changed": [], "added": ["Cold Brew Pack"], "removed": ["Team Box"]}
    facts = " ".join(f["text"] for f in client.get("/facts").json()["facts"])
    assert "Cold Brew Pack costs $24." in facts and "Team Box costs" not in facts


def test_removing_a_price_is_kept():
    prods = client.get("/brand/editable").json()["brand"]["products"]
    prods[0]["price"] = ""
    put({"products": prods})
    assert "price" not in client.get("/profile").json()["products"][0]


def test_merge_rules_unit():
    base = {"name": "A", "tags": [1, 2], "ep": {"max": 2, "note": "n"},
            "products": [{"name": "X", "price": 1, "url": "u"}, {"name": "Y"}]}
    over = {"tags": [3], "ep": {"max": 1}, "products": [{"name": "x", "price": 2}, {"name": "Z"}],
            "_removed_products": ["Y"]}
    m = merge_brand(base, over)
    assert m["tags"] == [3] and m["ep"] == {"max": 1, "note": "n"}
    assert m["products"] == [{"name": "x", "price": 2}, {"name": "Z"}]
    assert base["products"][0]["price"] == 1   # base not mutated


@pytest.mark.parametrize("body,field,words", [
    ({"facts": ["x" * 201]}, "facts.0", "200"),
    ({"facts": ["ok"] * 101}, "facts", "100"),
    ({"products": [{"name": "A"}]}, "products.0.one_line", "Field required"),
    ({"products": [{"one_line": "a"}]}, "products.0.name", "Field required"),
    ({"products": [{"name": "A", "one_line": "a"}, {"name": "a", "one_line": "b"}]}, "products", "twice"),
    ({"emoji_policy": {"allowed": ["☕", "ok"], "max_per_post": 2}}, "emoji_policy.allowed", "not a single emoji"),
    ({"emoji_policy": {"max_per_post": 11}}, "emoji_policy.max_per_post", "10"),
    ({"website": "northwind"}, "website", "https://"),
    ({"allowed_domains": ["not a domain"]}, "allowed_domains", "not a domain"),
    ({"banned_phrases": [""]}, "banned_phrases.0", "at least 1"),
    ({"name": ""}, "name", "at least 1"),
    ({"required_disclaimers": {"Paid Social!": ["#ad"]}}, "required_disclaimers", "channel"),
    ({"colour": "red"}, "colour", "Extra inputs"),
    ({"name": None}, "name", "null"),
])
def test_validation_errors_are_field_specific(body, field, words, files):
    _, over = files
    errs = errors(put(body))
    assert field in errs, errs
    assert words in errs[field]
    assert not over.exists()   # nothing half-saved


def test_domains_are_normalised_and_used_by_check():
    doc = put({"allowed_domains": ["https://www.Shop.Example.org/x", "shop.example.org"]}).json()
    assert doc["brand"]["allowed_domains"] == ["shop.example.org"]
    assert client.post("/check", json={"text": "Buy at shop.example.org"}).json()["ok"] is True


def test_editable_endpoints_need_key(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "k-123")
    assert client.get("/brand/editable").status_code == 401
    assert put({"name": "X"}).status_code == 401
    assert client.delete("/brand/editable").status_code == 401
    assert put({"name": "X"}, headers={"X-API-Key": "k-123"}).status_code == 200
    assert client.get("/brand/completeness").status_code == 401


@pytest.mark.parametrize("method,path", [
    ("GET", "/profile"), ("GET", "/facts"), ("GET", "/profile/summary"), ("GET", "/voice/questions"),
    ("GET", "/voice"), ("POST", "/check"), ("GET", "/brand/completeness")])
def test_reads_need_the_key_too(monkeypatch, method, path):
    """Several clients on one machine each have their own key: a URL pointing at the wrong
    client's brand must fail (401), not answer with that brand's facts."""
    monkeypatch.setenv("INTERNAL_API_KEY", "k-123")
    body = {"json": {"text": "hello"}} if method == "POST" else {}
    assert client.request(method, path, **body).status_code == 401
    assert client.request(method, path, headers={"X-API-Key": "other-client"}, **body).status_code == 401
    ok = client.request(method, path, headers={"X-API-Key": "k-123"}, **body)
    assert ok.status_code in (200, 404), ok.text   # /voice is 404 until a profile is saved
    assert client.get("/health").status_code == 200


def test_corrupt_overrides_file_is_ignored(files):
    _, over = files
    over.write_text("{not json")
    assert client.get("/profile").json()["name"] == "Northwind Roasters"
    over.write_text("[1, 2]")
    assert client.get("/profile").json()["name"] == "Northwind Roasters"


def test_write_is_atomic_leaves_no_temp_files(files, tmp_path):
    put({"name": "Acme Tea"})
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".brand-overrides-")] == []


def test_completeness_lists_what_is_missing():
    c = client.get("/brand/completeness").json()
    by = {x["id"]: x for x in c["checks"]}
    assert by["facts"]["ok"] and by["products"]["ok"] and by["website"]["ok"]
    assert by["voice"]["ok"] is False and "voice" in by["voice"]["missing"]
    assert c["done"] == c["total"] - 1 and c["score"] == round(100 * c["done"] / c["total"])
    put({"facts": ["One."], "website": "", "banned_phrases": []})
    c = client.get("/brand/completeness").json()
    by = {x["id"]: x for x in c["checks"]}
    assert not by["facts"]["ok"] and "4 more" in by["facts"]["missing"]
    assert not by["website"]["ok"] and not by["banned_phrases"]["ok"]
    assert len(c["missing"]) == 4
    client.put("/voice", json={"summary": "Write like a friendly barista."})
    assert {x["id"]: x for x in client.get("/brand/completeness").json()["checks"]}["voice"]["ok"]
