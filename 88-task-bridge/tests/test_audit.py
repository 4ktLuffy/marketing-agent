"""Public content drift audit (POST /audit): text and URL sources, 07 mocked, never writes."""
import httpx
import pytest

from .conftest import AUTH, BRAND
from . import data

EXTRACTOR = "http://extractor.test"
SCOPE = {"sites": ["porthleven"]}
OLD = "Hire a hybrid bike for a day at Porthleven for £24."
CURRENT = "Hire a hybrid bike for a day at Porthleven for £28."


@pytest.fixture
def aud(client, stack, monkeypatch, mock):
    stack.facts = data.business("bikes")
    monkeypatch.setenv("EXTRACTOR_URL", EXTRACTOR)
    return client


def run(client, sources, **kw):
    return client.post("/audit", json={"sources": sources, "scope": SCOPE, "on": data.TODAY, **kw}, headers=AUTH)


def extract(mock, text="", status=200, pages=None, url=None):
    body = {"url": url, "title": None, "text": text, "word_count": len(text.split())}
    if pages is not None:
        body.update(source="pdf", pages=[{"page": i + 1, "text": t} for i, t in enumerate(pages)])
    return mock.post(f"{EXTRACTOR}/extract").mock(return_value=httpx.Response(status, json=body if status < 300 else {"detail": text}))


def test_old_price_is_drift_with_current_value(aud):
    r = run(aud, [{"text": OLD, "label": "old post"}])
    assert r.status_code == 200, r.text
    src = r.json()["sources"][0]
    assert src["source"] == "old post" and src["sentences_checked"] == 1
    d = src["drift"]
    assert len(d) == 1 and d[0]["label"] == "conflict_or_expired" and d[0]["fact_key"] == "day-hire-porthleven"
    assert d[0]["should_say"] == "£28 per day" and "24" in d[0]["says"] and d[0]["leak"] is False
    assert r.json()["totals"]["drift"] == 1


def test_matching_text_has_no_drift(aud):
    src = run(aud, [{"text": CURRENT}]).json()["sources"][0]
    assert src["drift"] == [] and src["ok_matches"] == 1 and src["error"] is None
    assert src["source"] == "pasted text 1"


def test_expired_offer_without_replacement_says_nothing_to_replace(aud):
    src = run(aud, [{"text": "Summer Saver: 20% off weekday hires until 30 September."}], on="2026-11-01")
    s = src.json()["sources"][0]
    assert [d["label"] for d in s["drift"]] == ["conflict_or_expired"]
    assert s["drift"][0]["fact_key"] == "summer-saver" and s["drift"][0]["should_say"] is None


def test_internal_value_on_public_page_is_a_leak(aud):
    s = run(aud, [{"text": "Partner hotels pay £13.37 partner rate per bike."}]).json()["sources"][0]
    leak = [d for d in s["drift"] if d["leak"]]
    assert leak and leak[0]["label"] == "slot_blocked" and leak[0]["should_say"] is None
    assert leak[0]["fact_key"] == "partner-rate"


def test_url_source_read_through_extractor(aud, mock):
    route = extract(mock, OLD, url="https://lodge.test/rates")
    r = run(aud, [{"url": "https://lodge.test/rates"}])
    s = r.json()["sources"][0]
    assert s["source"] == "https://lodge.test/rates" and s["kind"] == "url" and len(s["drift"]) == 1
    assert route.calls[0].request.headers["x-api-key"]
    assert b"lodge.test/rates" in route.calls[0].request.content


def test_pdf_pages_are_joined_by_line(aud, mock):
    extract(mock, "x", pages=[CURRENT, OLD])
    s = run(aud, [{"url": "https://lodge.test/rates-2024.pdf"}]).json()["sources"][0]
    assert s["sentences_checked"] == 2 and len(s["drift"]) == 1 and s["ok_matches"] == 1


def test_extractor_error_is_per_source_not_a_crash(aud, mock):
    extract(mock, "could not fetch: HTTP 404", status=502)
    r = run(aud, [{"url": "https://lodge.test/gone"}, {"text": CURRENT, "label": "ok"}])
    assert r.status_code == 200
    a, b = r.json()["sources"]
    assert "could not read the page" in a["error"] and "404" in a["error"] and a["drift"] == []
    assert b["error"] is None and b["ok_matches"] == 1
    assert r.json()["totals"]["errors"] == 1


def test_extractor_unreachable_and_unset(aud, mock, monkeypatch):
    mock.post(f"{EXTRACTOR}/extract").mock(side_effect=httpx.ConnectError("boom"))
    s = run(aud, [{"url": "https://lodge.test/a"}]).json()["sources"][0]
    assert "unreachable" in s["error"]
    monkeypatch.delenv("EXTRACTOR_URL")
    s = run(aud, [{"url": "https://lodge.test/a"}, {"text": OLD}]).json()["sources"]
    assert "EXTRACTOR_URL is not set" in s[0]["error"] and len(s[1]["drift"]) == 1


def test_image_only_pdf_gets_a_clear_note(aud, mock):
    extract(mock, "", pages=["", ""])
    s = run(aud, [{"url": "https://lodge.test/scan.pdf"}]).json()["sources"][0]
    assert s["note"] == "no text found (image-only PDF?)" and s["error"] is None
    assert s["sentences_checked"] == 0 and s["drift"] == []


def test_limits(aud, mock):
    assert run(aud, [{"text": "a"}] * 11).status_code == 422
    assert run(aud, []).status_code == 422
    assert run(aud, [{"text": "x", "url": "https://a.test"}]).status_code == 422
    assert run(aud, [{}]).status_code == 422
    assert run(aud, [{"url": "file:///etc/passwd"}]).status_code == 422
    assert run(aud, [{"text": CURRENT}], on="soon").status_code == 422
    big = "Hello there. " * 6000
    assert run(aud, [{"text": big}, {"text": big}, {"text": big}]).status_code == 413
    # a fetched page that no longer fits the shared budget is that source's error
    extract(mock, "Lots of words. " * 1300)
    ok = "a" * 195_000
    r = run(aud, [{"text": ok}, {"url": "https://lodge.test/big"}])
    assert r.status_code == 200 and "limit" in r.json()["sources"][1]["error"]


def test_key_required_and_nothing_written(aud, stack):
    assert aud.post("/audit", json={"sources": [{"text": OLD}]}).status_code == 401
    assert aud.post("/audit", json={"sources": [{"text": OLD}]}, headers={"X-API-Key": "nope"}).status_code == 401
    run(aud, [{"text": OLD}])
    assert stack.posted == [] and stack.wording_posts == []


def test_rate_limited(aud, monkeypatch):
    monkeypatch.setenv("RATE_PER_MIN", "1")
    assert run(aud, [{"text": CURRENT}]).status_code == 200
    assert run(aud, [{"text": CURRENT}]).status_code == 429


def test_brand_down_is_upstream_error(aud, mock):
    mock.get(f"{BRAND}/facts/query").mock(return_value=httpx.Response(500, json={"detail": "x"}))
    assert run(aud, [{"text": OLD}]).status_code == 502
