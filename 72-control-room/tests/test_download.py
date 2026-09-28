"""Download: an item's body as one standalone HTML file (21 POST /document), to forward a client
report (85) or share a draft. Logged in only; no key or internal URL in the file or the headers."""
import json

import httpx
import pytest

from .conftest import CAL, KEYS, item, login

REPORT = "http://report.internal:8000"
REP = item(9, title="Client report September 2026", channel="client_report",
           body="# Northwind Roasters: monthly report\n\n| Sessions | 1,760 |")


@pytest.fixture(autouse=True)
def report(monkeypatch):   # before the app is created
    monkeypatch.setenv("REPORT_URL", REPORT)


def setup(mock, monkeypatch, doc_status=200):
    mock.get(f"{CAL}/items/9").mock(return_value=httpx.Response(200, json=REP))
    return mock.post(f"{REPORT}/document").mock(return_value=httpx.Response(
        doc_status, json={"html": "<!DOCTYPE html><title>Client report September 2026</title><p>1,760</p>"}
        if doc_status == 200 else {"detail": "boom"}))


def test_download_is_an_attachment_built_from_the_body(client, mock, monkeypatch):
    route = setup(mock, monkeypatch)
    login(client)
    r = client.get("/items/9/download")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"] == 'attachment; filename="client-report-september-2026.html"'
    assert "<p>1,760</p>" in r.text
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"title": "Client report September 2026", "markdown": REP["body"]}
    for secret in KEYS.values():
        assert secret not in r.text and secret not in str(r.headers)
    assert "internal" not in r.text


def test_item_page_links_to_download(client, mock, monkeypatch):
    setup(mock, monkeypatch)
    mock.get(url__regex=r".*/items/9/attempts.*").mock(return_value=httpx.Response(200, json=[]))
    mock.get(url__regex=r".*/pillars.*").mock(return_value=httpx.Response(200, json=[]))
    login(client)
    assert 'href="/items/9/download"' in client.get("/items/9").text


def test_download_needs_login(client, mock, monkeypatch):
    route = setup(mock, monkeypatch)
    r = client.get("/items/9/download")
    assert r.status_code in (302, 303, 401) and not route.called


def test_download_when_report_builder_fails_or_is_not_set(client, mock, monkeypatch):
    setup(mock, monkeypatch, doc_status=500)
    login(client)
    r = client.get("/items/9/download")
    assert r.status_code == 502 and "attachment" not in r.headers.get("content-disposition", "")
    monkeypatch.setenv("REPORT_URL", "")
    from app.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(), follow_redirects=False) as c2:
        login(c2)
        assert c2.get("/items/9/download").status_code == 404
