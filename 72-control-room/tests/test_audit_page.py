"""Audit page: URLs / pasted text -> 88 POST /audit (mocked) -> a table of drift. Works without JS."""
import json

import pytest
from fastapi.testclient import TestClient

from .conftest import PASSWORD, URLS, csrf_of, login
from .test_users_roles import PEOPLE, PW, write_file

TASKS = "http://tasks.internal:8000"
BRAND = URLS["BRAND_URL"]
DRIFT = {"sentence": "Midweek Escape from £210 a night.", "label": "conflict_or_expired", "fact_key": "weekday-rate",
         "says": "£210", "should_say": "£180 per room per night", "current_fact_key": "weekday-rate", "leak": False,
         "detail": "£210 is not the price of Midweek Escape"}
LEAK = {"sentence": "Partners pay £13.37.", "label": "slot_blocked", "fact_key": "partner-rate", "says": "Partners pay £13.37.",
        "should_say": None, "current_fact_key": None, "leak": True, "detail": "internal value written out"}


def answer(*sources):
    return {"on": "2026-10-01", "scope": {}, "fact_set_version": "fs-1-abc", "sources": list(sources),
            "totals": {"sources": len(sources), "drift": 0, "errors": 0}}


def src(name, drift=(), checked=4, ok=2, note=None, error=None):
    return {"source": name, "kind": "url", "sentences_checked": checked, "ok_matches": ok, "drift": list(drift),
            "note": note, "error": error}


@pytest.fixture
def bridge(monkeypatch, mock):
    monkeypatch.setenv("TASKS_URL", TASKS)
    from app.main import create_app
    mock.get(f"{BRAND}/facts/v2").respond(json={"facts": [{"key": "a", "scope": {"sites": ["Quayside", "Leeds"]}}]})
    with TestClient(create_app(), follow_redirects=False) as c:
        assert login(c).status_code == 303
        yield c, csrf_of(c.get("/more").text), mock


def test_not_installed(client, mock):
    login(client)
    r = client.get("/audit")
    assert r.status_code == 200 and "task bridge (88) isn't installed" in r.text


def test_login_required(client, mock):
    assert client.get("/audit").status_code == 303
    assert client.post("/audit", data={}).status_code == 401


def test_form_and_more_link(bridge):
    c, _, _ = bridge
    h = c.get("/audit").text
    assert 'name="urls"' in h and 'name="text"' in h and 'name="csrf"' in h and "No audit run yet" in h
    assert 'name="scope_sites" value="Quayside"' in h
    assert 'href="/audit"' in c.get("/more").text


def test_run_shows_drift_table_without_js(bridge):
    c, token, mock = bridge
    route = mock.post(f"{TASKS}/audit").respond(json=answer(
        src("https://lodge.example/rates.pdf", [DRIFT, LEAK]), src("https://lodge.example/ok", ok=3)))
    r = c.post("/audit", data={"csrf": token, "urls": "https://lodge.example/rates.pdf\n\nhttps://lodge.example/ok\n",
                               "text": "Midweek Escape from £210 a night.", "label": "old post", "on": "2026-10-01",
                               "scope_sites": "Quayside"})
    assert r.status_code == 200
    body = json.loads(route.calls[0].request.content)
    assert body == {"sources": [{"url": "https://lodge.example/rates.pdf"}, {"url": "https://lodge.example/ok"},
                                {"text": "Midweek Escape from £210 a night.", "label": "old post"}],
                    "scope": {"sites": ["Quayside"]}, "on": "2026-10-01"}
    assert route.calls[0].request.headers["x-api-key"]
    h = " ".join(r.text.split())
    assert "<th scope=\"col\">The facts say</th>" in h and "£210" in h and "£180 per room per night" in h
    assert "never public" in h and "An internal value must never be written on a public page." in h
    assert "4 sentences checked, 2 out of date, 2 matches" in h
    assert "https://lodge.example/ok</b>: 4 sentences checked, 0 out of date, 3 matches" in h
    assert "Nothing out of date" not in h


def test_clean_result_empty_state(bridge):
    c, token, mock = bridge
    mock.post(f"{TASKS}/audit").respond(json=answer(src("https://lodge.example/ok", ok=5)))
    r = c.post("/audit", data={"csrf": token, "urls": "https://lodge.example/ok"})
    assert "Nothing out of date found." in r.text


def test_source_error_and_image_only_pdf_shown(bridge):
    c, token, mock = bridge
    mock.post(f"{TASKS}/audit").respond(json=answer(
        src("https://x.example/gone", checked=0, ok=0, error="could not read the page (extractor HTTP 502: 404)"),
        src("https://x.example/scan.pdf", checked=0, ok=0, note="no text found (image-only PDF?)")))
    r = c.post("/audit", data={"csrf": token, "urls": "https://x.example/gone\nhttps://x.example/scan.pdf"})
    assert r.status_code == 200 and "could not read the page" in r.text and "no text found (image-only PDF?)" in r.text
    assert "Nothing out of date found." not in r.text


def test_validation_errors_do_not_call_88(bridge):
    c, token, _ = bridge       # respx refuses any unmocked call
    r = c.post("/audit", data={"csrf": token, "urls": "", "text": "  "})
    assert r.status_code == 422 and "Add at least one page address" in r.text
    r = c.post("/audit", data={"csrf": token, "urls": "ftp://x.example/a\nhttp://10.0.0.1/x"})
    assert r.status_code == 422 and "must start with http" in r.text
    r = c.post("/audit", data={"csrf": token, "text": "hello", "on": "soon"})
    assert r.status_code == 422 and "like 2026-10-01" in r.text
    r = c.post("/audit", data={"csrf": token, "urls": "\n".join(f"https://a.example/{i}" for i in range(11))})
    assert r.status_code == 422 and "At most 10" in r.text
    assert "https://a.example/3" in r.text    # what was typed is kept


def test_88_errors_are_shown(bridge):
    c, token, mock = bridge
    route = mock.post(f"{TASKS}/audit")
    route.respond(429, json={"detail": "too many requests"})
    r = c.post("/audit", data={"csrf": token, "text": "x y"})
    assert r.status_code == 429 and "try again in a minute" in r.text
    route.respond(503, json={"detail": "BRAND_URL is not set"})
    r = c.post("/audit", data={"csrf": token, "text": "x y"})
    assert r.status_code == 502 and "did not run" in r.text and "BRAND_URL" in r.text


def test_csrf_required(bridge):
    c, _, _ = bridge
    assert c.post("/audit", data={"text": "x"}).status_code in (401, 403)
    assert c.post("/audit", data={"csrf": "wrong", "text": "x"}).status_code in (401, 403)


def test_key_never_reaches_the_browser(bridge):
    c, token, mock = bridge
    mock.post(f"{TASKS}/audit").respond(json=answer(src("https://lodge.example/ok")))
    from .conftest import KEYS
    h = c.post("/audit", data={"csrf": token, "urls": "https://lodge.example/ok"}).text
    assert TASKS not in h and KEYS["INTERNAL_API_KEY"] not in h


def test_writer_role_can_run(tmp_path, monkeypatch, mock):
    monkeypatch.setenv("CONTROL_USERS_FILE", str(write_file(tmp_path / "u.json")))
    monkeypatch.setenv("TASKS_URL", TASKS)
    from app.main import create_app
    mock.get(f"{BRAND}/facts/v2").respond(json={"facts": []})
    mock.post(f"{TASKS}/audit").respond(json=answer(src("pasted text")))
    with TestClient(create_app(), follow_redirects=False) as c:
        assert login(c, PW["wanda"], "wanda").status_code == 303
        assert 'name="urls"' in c.get("/audit").text
        assert c.post("/audit", data={"csrf": csrf_of(c.get("/more").text), "text": "hello there"}).status_code == 200
