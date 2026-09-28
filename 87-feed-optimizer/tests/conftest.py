import json
import os

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app

KEY = "internal-secret-key-123"
APPROVER = "approver-secret-key-456"
AUTH = {"X-API-Key": KEY}
APPROVE = {**AUTH, "X-Approver-Key": APPROVER}
GW = "http://gateway.test:8000"

FEED_TSV = (
    "id\ttitle\tdescription\tbrand\tgtin\tmpn\tcolor\tsize\tmaterial\tgender\tage_group\tproduct_type\tprice\t"
    "sale_price\tlink\timage_link\tavailability\tcondition\tcustom_label_0\n"
    "S1\tOXFORD SHIRT - FREE SHIPPING!!!\tButton-down collar shirt with a chest pocket.\tHarbour Lane\t5012345678900\t"
    "HL-OX-1\tNavy\tM\tCotton\tmale\tadult\tApparel > Shirts\t49.00 GBP\t\thttps://shop.example/p/s1\t"
    "https://shop.example/i/s1.jpg\tin_stock\tnew\tsummer sale\n"
    "M1\tMug\tHand-thrown mug, holds 300 ml.\tKiln & Co\t\t\t\t\tStoneware\t\t\tHome > Kitchen > Mugs\t18.00 GBP\t"
    "15.00 GBP\thttps://shop.example/p/m1\thttps://shop.example/i/m1.jpg\tin_stock\tnew\t\n"
    "B1\tBeanie\t\tHarbour Lane\t\t\t\t\t\t\t\t\t22.00 GBP\t\thttps://shop.example/p/b1\t"
    "https://shop.example/i/b1.jpg\tout_of_stock\tnew\tbestseller\n"
)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("FEED_") or k in ("GATEWAY_URL", "APPROVER_KEY"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("APPROVER_KEY", APPROVER)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "feeds.sqlite"))


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock():
    """Any outgoing HTTP call that is not mocked fails the test."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as m:
        yield m


@pytest.fixture
def gateway(monkeypatch, mock):
    """The gateway answers with outputs[<product title>] (a dict), or 502 when it is missing."""
    monkeypatch.setenv("GATEWAY_URL", GW)
    outputs: dict = {}
    calls: list = []

    def answer(request):
        body = json.loads(request.content)
        product = json.loads(body["vars"]["product"])
        calls.append({"body": body, "product": product, "key": request.headers.get("x-api-key")})
        out = outputs.get(product.get("title"))
        if out is None:
            return httpx.Response(502, json={"detail": "no valid output after 3 attempts"})
        return httpx.Response(200, json={"prompt": "feed_title", "output": out})

    mock.post(f"{GW}/v1/run").mock(side_effect=answer)
    return outputs, calls


def upload(client, text=FEED_TSV, name="feed.tsv", data=None):
    r = client.post("/batches", params={"name": name}, content=data if data is not None else text.encode("utf-8"),
                    headers=AUTH)
    return r


def batch(client, text=FEED_TSV, name="feed.tsv"):
    r = upload(client, text, name)
    assert r.status_code == 201, r.text
    return r.json()["id"]
