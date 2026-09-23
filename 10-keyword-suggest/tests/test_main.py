import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_cache():
    main._cache.clear()


def google(request):
    q = request.url.params["q"]
    return httpx.Response(200, json=[q, [q, f"{q} club", "Coffee Subscription"], [], {}])


def ddg_list(request):
    q = request.url.params["q"]
    return httpx.Response(200, json=[q, [q, f"{q} box"]])


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


@respx.mock
def test_suggest_no_expand_merges_and_dedupes():
    g = respx.get(main.GOOGLE_URL).mock(side_effect=google)
    respx.get(main.DDG_URL).mock(side_effect=ddg_list)
    r = client.post("/suggest", json={"seed": "coffee subscription", "expand": False})
    assert r.status_code == 200
    body = r.json()
    words = [k["keyword"] for k in body["keywords"]]
    # "Coffee Subscription" is a case-insensitive duplicate of the seed.
    assert words == ["coffee subscription", "coffee subscription club", "coffee subscription box"]
    assert body["keywords"][2] == {"keyword": "coffee subscription box", "source": "duckduckgo", "modifier": ""}
    assert body["count"] == 3 and body["errors"] == []
    assert g.calls[0].request.url.params["gl"] == "us"


@respx.mock
def test_expand_uses_modifiers_and_old_ddg_format():
    respx.get(main.GOOGLE_URL).mock(side_effect=google)
    respx.get(main.DDG_URL).mock(
        side_effect=lambda req: httpx.Response(200, json=[{"phrase": req.url.params["q"] + " shop"}])
    )
    body = client.post("/suggest", json={"seed": "tea"}).json()
    mods = {k["keyword"]: k["modifier"] for k in body["keywords"]}
    assert mods["how tea"] == "how"
    assert mods["tea near me"] == "near me"
    assert mods["tea near me shop"] == "near me"  # old [{"phrase"}] format parsed
    assert mods["tea z club"] == "z"
    assert len(respx.calls) == 2 * (1 + 4 + 5 + 26)


@respx.mock
def test_failing_source_does_not_fail_request():
    respx.get(main.GOOGLE_URL).mock(side_effect=google)
    respx.get(main.DDG_URL).mock(return_value=httpx.Response(503))
    body = client.post("/suggest", json={"seed": "tea", "expand": False}).json()
    assert body["count"] == 3
    assert {k["source"] for k in body["keywords"]} == {"google"}
    assert body["errors"][0]["source"] == "duckduckgo"


@respx.mock
def test_results_are_cached():
    g = respx.get(main.GOOGLE_URL).mock(side_effect=google)
    respx.get(main.DDG_URL).mock(side_effect=ddg_list)
    first = client.post("/suggest", json={"seed": "tea", "expand": False}).json()
    second = client.post("/suggest", json={"seed": "TEA", "expand": False}).json()
    assert first == second and g.call_count == 1


def test_empty_seed_rejected():
    assert client.post("/suggest", json={"seed": ""}).status_code == 422
