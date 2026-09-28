"""Positioning page (78's map): matrix, cell quotes with links, white space, shifts; no key or
internal URL in the page; 'not installed' when AD_LIBRARY_URL is empty."""
import httpx
import pytest

from .conftest import KEYS

AD = "http://adlib.internal:8000"
Q = lambda quote, kind, ref, url: {"quote": quote, "source": {"kind": kind, "ref": ref, "url": url}}  # noqa: E731
SNAP = {
    "id": 7, "month": "2026-10", "built_at": "2026-10-01T07:00:05+00:00", "previous_month": "2026-09",
    "themes": [{"key": "price", "description": "price, discounts"}, {"key": "guarantee", "description": "refunds"}],
    "brands": [{"name": "Rival Beans", "kind": "competitor", "sources": 2, "error": None},
               {"name": "BeanCo", "kind": "competitor", "sources": 0, "error": "nothing to read yet (no checked page snapshot, no active ad)"},
               {"name": "Northwind Roasters", "kind": "ours", "sources": 4, "error": None}],
    "cells": {
        "price": {"Rival Beans": {"count": 2, "from_facts": 0, "quotes": [
                      Q("Plans from $10 a month.", "page: pricing", "Rival Beans · pricing", "https://rival.example.org/pricing"),
                      Q("First box <b>free</b>", "ad", "ad 11", "javascript:alert(1)")]},
                  "BeanCo": {"count": 0, "from_facts": 0, "quotes": []},
                  "Northwind Roasters": {"count": 0, "from_facts": 0, "quotes": []}},
        "guarantee": {"Rival Beans": {"count": 0, "from_facts": 0, "quotes": []},
                      "BeanCo": {"count": 0, "from_facts": 0, "quotes": []},
                      "Northwind Roasters": {"count": 1, "from_facts": 1, "quotes": [
                          Q("returned within 30 days for a full refund", "fact", "fact f6", None)]}}},
    "white_space": [{"theme": "guarantee", "facts": [Q("returned within 30 days for a full refund", "fact", "fact f6", None)]}],
    "crowded": [], "open_no_fact": ["speed"], "they_claim_we_dont": [{"theme": "price", "competitors": ["Rival Beans"]}],
    "shifts": [{"brand": "Rival Beans", "theme": "price", "kind": "started", "before": 0, "after": 2,
                "text": "Rival Beans started talking about price in October 2026",
                "quote": [Q("Plans from $10 a month.", "page: pricing", "Rival Beans · pricing", "https://rival.example.org/pricing")]}],
    "stats": {"proposed": 9, "kept": 3}, "warnings": [],
}


@pytest.fixture
def adlib(monkeypatch):
    monkeypatch.setenv("AD_LIBRARY_URL", AD)


def mock78(mock, latest=SNAP):
    mock.get(f"{AD}/positioning").respond(json=[{"id": 7, "month": "2026-10"}, {"id": 6, "month": "2026-09"}])
    if latest is None:
        mock.get(f"{AD}/positioning/latest").respond(404, json={"detail": "no positioning map yet"})
    else:
        mock.get(f"{AD}/positioning/latest").respond(json=latest)
    mock.get(f"{AD}/positioning/6").respond(json=SNAP | {"id": 6, "month": "2026-09", "shifts": [], "previous_month": None})
    mock.get(f"{AD}/positioning/99").respond(404, json={"detail": "not found"})


def test_matrix_white_space_shifts(adlib, authed, mock):
    c, _ = authed
    mock78(mock)
    r = c.get("/positioning")
    assert r.status_code == 200, r.text
    h = r.text
    assert "Map 2026-10" in h and "Rival Beans" in h and "Northwind Roasters" in h
    assert 'href="/positioning?id=7&amp;theme=price&amp;brand=Rival+Beans#quotes">2</a>' in h
    assert "(1 fact)" in h                                  # our guarantee cell is backed by a fact
    assert "<b>guarantee</b>: “returned within 30 days for a full refund”" in h
    assert "Nobody claims speed" in h and "They claim it, we say nothing" in h
    assert "Rival Beans started talking about price in October 2026" in h
    assert 'href="https://rival.example.org/pricing" rel="noopener noreferrer"' in h
    assert "BeanCo: nothing to read yet" in h
    assert 'href="/positioning?id=6"' in h                  # month history
    assert 'id="quotes"' not in h                           # no cell selected yet
    for secret in (*KEYS.values(), AD, "adlib.internal"):
        assert secret not in h


def test_cell_quotes_are_escaped_and_only_http_links(adlib, authed, mock):
    c, _ = authed
    mock78(mock)
    h = c.get("/positioning", params={"theme": "price", "brand": "Rival Beans"}).text
    assert 'id="quotes"' in h and "Rival Beans · price" in h
    assert "“Plans from $10 a month.”" in h
    assert "&lt;b&gt;free&lt;/b&gt;" in h and "<b>free</b>" not in h
    assert "javascript:" not in h and "ad 11" in h           # shown, but not as a link
    # Unknown cell: the page renders without a quote panel.
    assert 'id="quotes"' not in c.get("/positioning", params={"theme": "nope", "brand": "x"}).text


def test_older_month_and_missing_one(adlib, authed, mock):
    c, _ = authed
    mock78(mock)
    h = c.get("/positioning", params={"id": 6}).text
    assert "Map 2026-09" in h and "This is the first map" in h
    assert "Positioning map 99 not found." in c.get("/positioning", params={"id": 99}).text


def test_no_map_yet_and_78_down(adlib, authed, mock):
    c, _ = authed
    mock78(mock, latest=None)
    assert "No positioning map yet" in c.get("/positioning").text
    mock.get(f"{AD}/positioning").mock(side_effect=httpx.ConnectError("refused"))
    h = c.get("/positioning").text
    assert "ad-library: unreachable (ConnectError)" in h and AD not in h


def test_not_installed(monkeypatch, authed, mock):
    c, _ = authed
    c.app.state.settings.ad_library_url = ""
    h = c.get("/positioning").text
    assert "isn't installed" in h


def test_needs_login(client):
    r = client.get("/positioning")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_more_links_to_it(authed):
    c, _ = authed
    assert 'href="/positioning"' in c.get("/more").text
