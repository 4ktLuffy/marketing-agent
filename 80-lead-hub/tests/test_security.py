"""Security review 2026-09-28 (docs/security-review-2026-09-28.md): each test failed before its fix."""
import asyncio
import json

import httpx

from app import enrich, main, safe_http
from tests.test_main import EXTRACTOR, SITE, client, detail, env, post_lead, services  # noqa: F401  (fixtures)

EVIL = ('Hi <img src="https://tracker.example/p.gif"> <a href="https://evil.example/login">Log in to see the '
        'attached RFP</a> <script>alert(1)</script>')


# LH-1: the public webhook read the whole body into memory before the size check.
def test_webhook_stops_reading_an_oversized_body(services):
    # Straight ASGI: TestClient would buffer the whole body before the app sees it.
    got = {"chunks": 0}

    async def receive():
        got["chunks"] += 1  # 64 x 64 KB = 4 MB, chunked (no Content-Length)
        return {"type": "http.request", "body": b" " * 65536, "more_body": got["chunks"] < 64}

    out = []

    async def send(message):
        out.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/leads/webhook/n8n", "raw_path": b"/leads/webhook/n8n",
             "query_string": b"", "root_path": "", "client": ("203.0.113.5", 5000), "server": ("testserver", 80),
             "headers": [(b"host", b"testserver"), (b"x-webhook-secret", b"n8n-secret")]}
    asyncio.run(main.app(scope, receive, send))
    assert next(m["status"] for m in out if m["type"] == "http.response.start") == 413
    assert got["chunks"] <= 5  # stopped near the 200 KB cap, not after 4 MB


def test_webhook_refuses_a_large_content_length_before_reading(services):
    r = client.post("/leads/webhook/n8n", content=b"{}", headers={"X-Webhook-Secret": "n8n-secret",
                                                                   "Content-Length": "999999999"})
    assert r.status_code == 413


def test_webhook_normal_body_still_works(services):
    r = client.post("/leads/webhook/n8n", json={"email": "sam@acme-robotics.com", "message": "demo please"},
                    headers={"X-Webhook-Secret": "n8n-secret"})
    assert r.status_code == 201


# LH-2: lead-typed text went into the CRMs' HTML notes unescaped (tracking pixels, phishing links).
def test_crm_notes_escape_lead_html(services, monkeypatch):
    monkeypatch.setenv("CRM", "hubspot,pipedrive")
    d = detail(post_lead(message=EVIL, name='Eve <b>Admin</b>').json()["id"])
    plans = {c["crm"]: c["plan"] for c in d["route"]["crm"]}
    notes = {"hubspot": plans["hubspot"]["note"]["json"]["properties"]["hs_note_body"],
             "pipedrive": plans["pipedrive"]["note"]["json"]["content"]}
    for crm, note in notes.items():
        for bad in ("<img", "<a ", "<script", "<b>"):
            assert bad not in note, (crm, bad)
        assert "&lt;img" in note and "Log in to see the attached RFP" in note
        assert "<br>" in note  # lines are still lines


# LH-3: robots.txt redirects and the extractor's final URL were "same site" on any port or scheme.
def test_same_site_needs_same_scheme_and_port():
    base = SITE
    assert enrich.same_site(SITE + "/about", "acme-robotics.com", base)
    assert enrich.same_site("https://www.acme-robotics.com/", "acme-robotics.com", base)
    assert enrich.same_site("https://acme-robotics.com:443/x", "acme-robotics.com", base)
    assert not enrich.same_site("http://acme-robotics.com/", "acme-robotics.com", base)
    assert not enrich.same_site("https://acme-robotics.com:8443/", "acme-robotics.com", base)
    assert not enrich.same_site("http://acme-robotics.com:6379/", "acme-robotics.com", base)


def test_extractor_final_url_on_another_port_is_discarded(services):
    def handler(request):
        url = json.loads(request.content)["url"]
        return httpx.Response(200, json={"url": url.replace("https://acme-robotics.com", "http://acme-robotics.com:8000"),
                                         "text": "internal admin page"})
    services.post(EXTRACTOR + "/extract").mock(side_effect=handler)
    e = detail(post_lead().json()["id"])["enrichment"]
    assert e["sources"] == [] and any(s["why"] == "redirected off the company domain" for s in e["skipped"])


def test_robots_redirect_to_another_port_is_not_followed(services):
    services.get(SITE + "/robots.txt").respond(301, headers={"Location": "http://acme-robotics.com:2375/robots.txt"})
    internal = services.get("http://acme-robotics.com:2375/robots.txt").respond(200, text="User-agent: *\nDisallow:\n")
    post_lead()
    assert not internal.called


def test_ipv6_forms_that_embed_a_private_ipv4_are_refused(monkeypatch):
    for addr in ("64:ff9b::7f00:1", "2002:a00:7::", "::ffff:10.0.0.7", "::10.0.0.7"):
        monkeypatch.setattr(safe_http, "resolve", lambda host, a=addr: [a])
        assert not enrich._public_host("acme-robotics.com"), addr
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["2606:4700::6810:84e5"])
    assert enrich._public_host("acme-robotics.com")
