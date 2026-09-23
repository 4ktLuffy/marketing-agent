from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_build_normalizes_and_keeps_existing_params():
    r = client.post("/build", json={
        "url": "https://shop.example.com/sale?ref=nav&utm_source=old",
        "source": "LinkedIn", "medium": "Social", "campaign": "Spring Sale 2026",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["params"] == {
        "utm_source": "linkedin", "utm_medium": "social", "utm_campaign": "spring-sale-2026",
    }
    assert "ref=nav" in body["url"]
    assert "utm_source=old" not in body["url"]


def test_build_rejects_unknown_medium():
    r = client.post("/build", json={
        "url": "https://example.com", "source": "x", "medium": "tweets", "campaign": "c",
    })
    assert r.status_code == 422
    assert "not allowed" in r.json()["detail"]


def test_build_rejects_relative_url():
    r = client.post("/build", json={"url": "/sale", "source": "x", "medium": "social", "campaign": "c"})
    assert r.status_code == 422


def test_build_rejects_value_that_normalizes_to_empty():
    r = client.post("/build", json={
        "url": "https://example.com", "source": "!!!", "medium": "social", "campaign": "c",
    })
    assert r.status_code == 422


def test_parse_roundtrip():
    built = client.post("/build", json={
        "url": "https://example.com/p?a=1", "source": "news", "medium": "email",
        "campaign": "weekly", "content": "hero button",
    }).json()
    parsed = client.post("/parse", json={"url": built["url"]}).json()
    assert parsed["params"] == built["params"]
    assert parsed["base_url"] == "https://example.com/p?a=1"
