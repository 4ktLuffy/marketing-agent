"""Upload, proposals (gateway mocked), approval with the approver key, and the exports."""
import csv
import io

from app import feed

from .conftest import APPROVE, AUTH, FEED_TSV, GW, batch, upload

GOOD_S1 = {"title": "Harbour Lane Men's Oxford Shirt - Navy, Size M, Cotton", "description": ""}


def propose(client, bid, **body):
    r = client.post(f"/batches/{bid}/propose", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def items(client, bid, **q):
    r = client.get(f"/batches/{bid}", params=q, headers=AUTH)
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()["items"]}


def test_health_is_open_everything_else_needs_the_key(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/batches").status_code == 401
    assert client.post("/batches", content=b"id,title\n1,a\n").status_code == 401
    assert client.get("/batches/1/export.csv").status_code == 401


def test_upload_summary_and_rule_titles_without_a_model(client, mock):
    r = upload(client)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["products"] == 3 and s["pending"] == 3 and s["format"] == "tsv"
    assert s["unknown_columns"] == ["custom_label_0"]
    bid = s["id"]
    out = propose(client, bid, limit=10)          # no GATEWAY_URL: rule-based only, no call made
    assert out["model"] is False and out["done"] == 3 and out["pending"] == 0
    it = items(client, bid)
    assert it["S1"]["title"]["after"] == "Harbour Lane Men's Oxford Shirt - Navy, Size M, Cotton"
    assert it["S1"]["title"]["source"] == "rules"
    assert it["M1"]["title"]["after"] == "Kiln & Co Mug - Stoneware"
    assert it["B1"]["title"]["after"] == "Harbour Lane Beanie"


def test_model_title_kept_or_rejected_with_reasons(client, gateway):
    outputs, calls = gateway
    outputs["OXFORD SHIRT - FREE SHIPPING!!!"] = GOOD_S1
    outputs["Mug"] = {"title": "Kiln & Co Blue Stoneware Mug 2-Pack", "description": ""}      # invented colour + number
    outputs["Beanie"] = {"title": "Kiln & Co Beanie - Best Seller", "description": ""}        # other brand + promo
    bid = batch(client)
    out = propose(client, bid)
    assert out["titles"] == {"model": 1, "rule_based": 2, "original": 0}
    assert out["rejected"]["titles"] == 2
    assert out["rejected"]["reasons"] == {"brand_missing": 1, "brand_other": 1, "colour": 1, "number": 1, "promo": 1}
    it = items(client, bid)
    assert it["S1"]["title"]["source"] == "llm" and it["S1"]["title"]["after"] == GOOD_S1["title"]
    assert it["M1"]["title"]["source"] == "rules" and it["M1"]["title"]["after"] == "Kiln & Co Mug - Stoneware"
    assert {x["code"] for x in it["M1"]["title"]["rejected"]} == {"colour", "number"}
    assert it["M1"]["title"]["model"] == "Kiln & Co Blue Stoneware Mug 2-Pack"
    # The model saw only the row's descriptive fields: no price, id, gtin, links or custom labels.
    seen = calls[0]["product"]
    assert "price" not in seen and "gtin" not in seen and "link" not in seen and "custom_label_0" not in seen
    assert "id" not in seen and calls[0]["key"] == AUTH["X-API-Key"]
    assert calls[0]["body"]["prompt"] == "feed_title" and calls[0]["body"]["vars"]["with_description"] is None


def test_descriptions_checked_and_failed_calls_fall_back(client, gateway):
    outputs, _ = gateway
    outputs["OXFORD SHIRT - FREE SHIPPING!!!"] = GOOD_S1 | {
        "description": "A navy cotton oxford shirt from Harbour Lane with a button-down collar and a chest pocket."}
    outputs["Mug"] = {"title": "Kiln & Co Stoneware Mug, 300 ml",
                      "description": "A 100% stoneware mug, microwave safe."}                  # number + claim
    bid = batch(client)                                   # Beanie: gateway 502 -> rule title, error kept
    out = propose(client, bid, descriptions=True)
    assert out["descriptions_from_model"] == 1 and out["rejected"]["descriptions"] == 1 and out["errors"] == 1
    it = items(client, bid)
    assert it["S1"]["description"]["source"] == "llm"
    assert it["M1"]["description"]["after"] == "Hand-thrown mug, holds 300 ml."            # original kept
    assert {x["code"] for x in it["M1"]["description"]["rejected"]} == {"number", "claim"}
    assert it["M1"]["title"]["source"] == "llm"
    assert it["B1"]["title"]["source"] == "rules" and "502" in it["B1"]["error"]


def test_gateway_down_stops_the_run_and_leaves_products_pending(client, monkeypatch, mock):
    import httpx
    monkeypatch.setenv("GATEWAY_URL", GW)
    mock.post(f"{GW}/v1/run").mock(side_effect=httpx.ConnectError("refused"))
    bid = batch(client)
    out = propose(client, bid)
    assert out["done"] == 0 and out["pending"] == 3 and "unreachable" in out["stopped"]


def test_approval_needs_the_approver_key(client, mock, monkeypatch):
    bid = batch(client)
    propose(client, bid)
    assert client.post(f"/batches/{bid}/approve", json={"all": True}, headers=AUTH).status_code == 403
    bad = {**AUTH, "X-Approver-Key": "wrong"}
    assert client.post(f"/batches/{bid}/approve", json={"all": True}, headers=bad).status_code == 403
    monkeypatch.delenv("APPROVER_KEY")
    assert client.post(f"/batches/{bid}/approve", json={"all": True}, headers=APPROVE).status_code == 503


def test_approve_needs_a_proposal_and_ids_or_all(client, mock):
    bid = batch(client)
    r = client.post(f"/batches/{bid}/approve", json={"ids": ["S1"]}, headers=APPROVE)
    assert r.json()["approved"] == [] and "no proposal" in r.json()["skipped"][0]["reason"]
    assert client.post(f"/batches/{bid}/approve", json={}, headers=APPROVE).status_code == 422
    assert client.post(f"/batches/{bid}/approve", json={"ids": ["NOPE"]}, headers=APPROVE).status_code == 404


def exported(client, bid, kind="tsv", what="export"):
    r = client.get(f"/batches/{bid}/{what}.{kind}", headers=AUTH)
    assert r.status_code == 200, r.text
    return r


def test_export_only_approved_and_never_changed_fields_byte_for_byte(client, gateway):
    outputs, _ = gateway
    outputs["OXFORD SHIRT - FREE SHIPPING!!!"] = GOOD_S1
    bid = batch(client)
    propose(client, bid)
    # Nothing approved yet: the export is the upload, byte for byte.
    assert exported(client, bid).content == FEED_TSV.encode("utf-8")
    r = client.post(f"/batches/{bid}/approve", json={"ids": ["S1"], "approved_by": "Sam"}, headers=APPROVE)
    assert r.json()["approved"] == ["S1"]
    out = exported(client, bid)
    assert out.headers["content-type"].startswith("text/tab-separated-values")
    assert 'filename="feed-optimized.tsv"' in out.headers["content-disposition"]
    got, want = out.text.split("\n"), FEED_TSV.split("\n")
    assert got[0] == want[0] and got[2:] == want[2:]           # header, other rows: same bytes, same order
    g, w = got[1].split("\t"), want[1].split("\t")
    assert g[1] == GOOD_S1["title"]
    assert g[:1] + g[2:] == w[:1] + w[2:]                      # every other cell of S1 untouched
    # A later proposal run cannot change what was approved.
    assert items(client, bid)["S1"]["approved_by"] == "Sam"


def test_export_ignores_a_tampered_never_changed_value(client, mock):
    """Even if the database held a new price for a product, the export writes the uploaded one."""
    from app import main
    bid = batch(client)
    propose(client, bid)
    client.post(f"/batches/{bid}/approve", json={"all": True}, headers=APPROVE)
    with main.db() as conn:
        conn.execute("UPDATE products SET row = replace(row, '49.00 GBP', '0.01 GBP')")
    f = feed.parse(exported(client, bid).content, max_rows=10)
    rows = {r["id"]: r for _, r in f.rows()}
    assert rows["S1"]["price"] == "49.00 GBP" and rows["M1"]["sale_price"] == "15.00 GBP"
    for col in feed.NEVER_CHANGED:
        orig = {r["id"]: r for _, r in feed.parse(FEED_TSV.encode(), max_rows=10).rows()}
        assert all(rows[i][col] == orig[i][col] for i in rows)


def test_revoke_and_supplemental_feed(client, gateway):
    outputs, _ = gateway
    outputs["OXFORD SHIRT - FREE SHIPPING!!!"] = GOOD_S1
    bid = batch(client)
    propose(client, bid)
    client.post(f"/batches/{bid}/approve", json={"ids": ["S1", "M1"]}, headers=APPROVE)
    sup = exported(client, bid, "csv", "supplemental")
    rows = list(csv.reader(io.StringIO(sup.text)))
    assert rows == [["id", "title"], ["S1", GOOD_S1["title"]], ["M1", "Kiln & Co Mug - Stoneware"]]
    r = client.post(f"/batches/{bid}/revoke", json={"ids": ["M1"]}, headers=APPROVE)
    assert r.json() == {"revoked": 1}
    rows = list(csv.reader(io.StringIO(exported(client, bid, "tsv", "supplemental").text), delimiter="\t"))
    assert rows == [["id", "title"], ["S1", GOOD_S1["title"]]]
    assert client.post(f"/batches/{bid}/revoke", json={"all": True}, headers=AUTH).status_code == 403


def test_other_format_keeps_every_value(client, mock):
    bid = batch(client)
    propose(client, bid)
    client.post(f"/batches/{bid}/approve", json={"all": True}, headers=APPROVE)
    out = exported(client, bid, "csv")
    rows = list(csv.reader(io.StringIO(out.text)))
    orig = list(csv.reader(io.StringIO(FEED_TSV), delimiter="\t"))
    assert rows[0] == orig[0] and [r[0] for r in rows] == [r[0] for r in orig]
    for a, b in zip(rows[1:], orig[1:]):
        assert a[:1] + a[2:] == b[:1] + b[2:]
    assert rows[1][1] == "Harbour Lane Men's Oxford Shirt - Navy, Size M, Cotton"


def test_csv_upload_with_bom_round_trips(client, mock):
    text = '﻿id,title,description,brand,price\r\n1,"CERAMIC VASE, LARGE!!",A vase.,Kiln & Co,30.00 USD\r\n'
    bid = batch(client, text, "vases.csv")
    propose(client, bid)
    assert exported(client, bid, "csv").content == text.encode("utf-8")
    client.post(f"/batches/{bid}/approve", json={"all": True}, headers=APPROVE)
    body = exported(client, bid, "csv").content.decode("utf-8")
    assert body.startswith("﻿id,title,") and body.endswith(",A vase.,Kiln & Co,30.00 USD\r\n")
    assert "Kiln & Co Ceramic Vase, Large" in body


def test_upload_caps_and_bad_files(client, monkeypatch):
    monkeypatch.setenv("FEED_MAX_BYTES", "1000")
    assert upload(client, "id,title\n" + "1,a\n" * 400).status_code == 413
    monkeypatch.setenv("FEED_MAX_ROWS", "2")
    r = upload(client, "id,title\n1,a\n2,b\n3,c\n")
    assert r.status_code == 422 and "more than 2 products" in r.json()["detail"]
    assert upload(client, "sku,name\n1,a\n").status_code == 422
    assert upload(client, data="id,title\n1,caf\xe9\n".encode("latin-1")).status_code == 422


def test_filters_list_and_delete(client, mock):
    bid = batch(client)
    propose(client, bid)
    client.post(f"/batches/{bid}/approve", json={"ids": ["S1"]}, headers=APPROVE)
    assert set(items(client, bid, only="approved")) == {"S1"}
    assert set(items(client, bid, only="changed")) == {"S1", "M1", "B1"}
    listed = client.get("/batches", headers=AUTH).json()
    assert listed[0]["id"] == bid and listed[0]["approved"] == 1 and listed[0]["proposed"] == 3
    assert client.delete(f"/batches/{bid}", headers=AUTH).json() == {"deleted": bid}
    assert client.get(f"/batches/{bid}", headers=AUTH).status_code == 404
