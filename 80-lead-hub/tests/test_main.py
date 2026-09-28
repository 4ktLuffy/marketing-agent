import base64
import hashlib
import hmac
import json
import logging
import sqlite3

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import enrich, main, safe_http, scoring

KEY = "test-key"
H = {"X-API-Key": KEY}
EXTRACTOR = "http://extractor.test"
GATEWAY = "http://gateway.test"
CLAIMS = "http://claims.test"
CAL = "http://calendar.test"
SITE = "https://acme-robotics.com"
HOME = ("Acme Robotics builds warehouse picking robots for mid-sized distributors. "
        "Our robots work alongside your existing shelving.")
ABOUT = "Founded in Leeds in 2019. Today our team of 45 engineers supports customers in 12 countries."
HS_TOKEN = "pat-na1-SECRET-hubspot-token"
PD_TOKEN = "SECRET-pipedrive-token"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    main._migrated.clear()
    for k in ("CALENDAR_URL", "HUBSPOT_TOKEN", "PIPEDRIVE_TOKEN", "PIPEDRIVE_BASE_URL", "CRM",
              "NOTIFY_WEBHOOK_URL", "BOOKING_URL", "SITE_URL_TEMPLATE", "SCORING_RULES",
              "ALLOW_PRIVATE_URLS", "RETENTION_DAYS", "HUBSPOT_TAG_PROPERTY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "leads.sqlite"))
    monkeypatch.setenv("EXTRACTOR_URL", EXTRACTOR)
    monkeypatch.setenv("GATEWAY_URL", GATEWAY)
    monkeypatch.setenv("CLAIMS_URL", CLAIMS)
    monkeypatch.setenv("ENRICH_DELAY_S", "0")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("WEBHOOK_SECRETS", "tally=tally-secret,typeform=tf-secret,n8n=n8n-secret")
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.215.14"])


client = TestClient(main.app)

ENRICH_OUT = {
    "industry": "warehouse robotics",
    "sells": "warehouse picking robots",
    "facts": [
        {"text": "The company builds warehouse picking robots for mid-sized distributors",
         "quote": "Acme Robotics builds warehouse picking robots for mid-sized distributors.",
         "source_url": SITE + "/"},
        {"text": "The company has offices in Tokyo",  # invented: quote is not on the page
         "quote": "Our Tokyo office opened in 2021.", "source_url": SITE + "/about"},
        {"text": "The company won an industry award",  # quote exists, claim checker rejects it
         "quote": "Founded in Leeds in 2019.", "source_url": SITE + "/about"},
    ],
    "size_hints": [
        {"statement": "team of 45 engineers", "quote": "our team of 45 engineers", "source_url": SITE + "/about"},
        {"statement": "about 500 staff", "quote": "we employ 500 people", "source_url": SITE + "/about"},
    ],
}
REPLY_OUT = {"needs_human": False, "reason": "answered the team question",
             "subject": "Coffee for your team",
             "reply": "Hi Maya, thanks for getting in touch. Team Box ships to each teammate. "
                      "We also include a free espresso machine. Would a short call help? "
                      "Pick a time at https://cal.example.com/intro."}


def extractor_handler(request):
    url = json.loads(request.content)["url"]
    if url == SITE + "/":
        return httpx.Response(200, json={"url": url, "title": "Acme", "text": HOME})
    if url == SITE + "/about":
        return httpx.Response(200, json={"url": url, "title": "About", "text": ABOUT})
    return httpx.Response(502, json={"detail": "upstream returned HTTP 404"})


def gateway_handler(request):
    body = json.loads(request.content)
    assert request.headers.get("x-api-key") == KEY
    out = {"lead_enrich": ENRICH_OUT, "lead_first_reply": REPLY_OUT}[body["prompt"]]
    return httpx.Response(200, json={"prompt": body["prompt"], "output": out, "attempts": 1})


def claims_handler(request):
    body = json.loads(request.content)
    claims = []
    for line in [s for part in body["text"].split("\n") for s in part.split(". ") if s.strip()]:
        s = line.strip()
        s = s if s.endswith((".", "?", "!")) else s + "."
        bad = "award" in s or "espresso machine" in s
        claims.append({"claim": s, "supported": not bad, "reasons": ["not in the facts"] if bad else []})
    return httpx.Response(200, json={"ok": not any(not c["supported"] for c in claims),
                                     "unsupported": [c["claim"] for c in claims if not c["supported"]],
                                     "claims": claims, "numbers": []})


@pytest.fixture
def services():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(SITE + "/robots.txt").respond(200, text="User-agent: *\nDisallow: /private\n")
        mock.post(EXTRACTOR + "/extract").mock(side_effect=extractor_handler)
        mock.post(GATEWAY + "/v1/run").mock(side_effect=gateway_handler)
        mock.post(CLAIMS + "/verify").mock(side_effect=claims_handler)
        yield mock


def lead_body(**kw):
    body = {"source": "form", "email": "maya@acme-robotics.com", "name": "Maya Chen",
            "message": "Can we book a demo? We are a remote team of 40 people and want pricing.",
            "consent": {"text": "I agree to be contacted about my enquiry.", "at": "2026-09-27T10:00:00Z"}}
    body.update(kw)
    return body


def post_lead(**kw):
    return client.post("/leads", json=lead_body(**kw), headers=H)


def detail(lead_id):
    r = client.get(f"/leads/{lead_id}", headers=H)
    assert r.status_code == 200
    return r.json()


# ---------- auth


def test_key_required(monkeypatch):
    assert client.post("/leads", json=lead_body()).status_code == 401
    assert client.get("/leads").status_code == 401
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/leads", json=lead_body(), headers=H).status_code == 503


# ---------- capture + dedupe


def test_capture_and_dedupe_merges_activity(services):
    r = post_lead()
    assert r.status_code == 201 and r.json()["created"] is True
    lead_id = r.json()["id"]
    r2 = post_lead(email="MAYA@Acme-Robotics.com", source="site_assistant", name=None,
                   message="Following up: do you ship to Leeds?", transcript_ref="conv-77")
    assert r2.status_code == 200 and r2.json() == {**r2.json(), "id": lead_id, "merged": True}
    d = detail(lead_id)
    assert d["name"] == "Maya Chen"
    assert d["sources"] == ["form", "site_assistant"]
    msgs = [a for a in d["timeline"] if a["message"]]
    assert [m["kind"] for m in msgs] == ["captured", "merged"]
    assert msgs[1]["transcript_ref"] == "conv-77"
    assert len(client.get("/leads", headers=H).json()) == 1


def test_invalid_input_rejected():
    assert post_lead(email="not-an-email").status_code == 422
    assert post_lead(source="linkedin_scrape").status_code == 422
    assert post_lead(consent={"text": " ", "at": "2026-09-27"}).status_code == 422
    assert post_lead(consent={"text": "ok"}).status_code == 422  # `at` is required


# ---------- domain choice


@pytest.mark.parametrize("email,given,expected", [
    ("a@acme-robotics.com", None, "acme-robotics.com"),
    ("a@gmail.com", None, None),
    ("a@GMail.com", "https://www.Acme.io/about", "acme.io"),
    ("a@outlook.com", "linkedin.com/company/acme", None),
    ("a@acme-robotics.com", "other.com", "acme-robotics.com"),
    ("a@gmail.com", "10.0.0.5", None),
])
def test_pick_domain(email, given, expected):
    assert enrich.pick_domain(email, given)[0] == expected


def test_free_mail_is_not_enriched(services):
    r = post_lead(email="maya.chen@gmail.com")
    assert r.json()["enrich_status"] == "skipped"
    assert "free mail" in r.json()["domain_note"]
    assert not services.routes[1].called  # extractor never called
    d = detail(r.json()["id"])
    assert d["enrichment"] is None
    assert d["grade"] in "ABCD"  # still scored


# ---------- enrichment


def test_enrichment_is_grounded(services):
    lead_id = post_lead().json()["id"]
    e = detail(lead_id)["enrichment"]
    assert e["sources"] == [SITE + "/", SITE + "/about"]
    texts = [f["text"] for f in e["facts"]]
    assert texts == ["The company builds warehouse picking robots for mid-sized distributors."]
    dropped = {d["text"]: d["why"] for d in e["dropped"]}
    assert dropped["The company has offices in Tokyo"] == "quote is not on the page"
    assert "claim checker" in dropped["The company won an industry award."]
    assert [h["statement"] for h in e["size_hints"]] == ["team of 45 engineers"]
    assert e["industry"] == "warehouse robotics" and e["sells"] == "warehouse picking robots"
    # 44 got the page text as context, and only the lead's own domain was fetched.
    verify = json.loads(services.routes[3].calls[0].request.content)
    assert HOME in verify["context"] and ABOUT in verify["context"]
    fetched = [json.loads(c.request.content)["url"] for c in services.routes[1].calls]
    assert all(u.startswith(SITE) for u in fetched)


def test_robots_disallow_all_fetches_nothing(services):
    services.get(SITE + "/robots.txt").respond(200, text="User-agent: *\nDisallow: /\n")
    lead_id = post_lead().json()["id"]
    e = detail(lead_id)["enrichment"]
    assert e["sources"] == [] and all("robots" in s["why"] for s in e["skipped"])
    assert not services.routes[1].called
    assert all(json.loads(c.request.content)["prompt"] != "lead_enrich" for c in services.routes[2].calls)


def test_robots_forbidden_marks_failed(services):
    services.get(SITE + "/robots.txt").respond(403)
    d = detail(post_lead().json()["id"])
    assert d["enrich_status"] == "failed" and "403" in d["enrichment"]["error"]


def test_redirect_off_domain_is_discarded(services):
    def handler(request):
        url = json.loads(request.content)["url"]
        return httpx.Response(200, json={"url": "https://parking-site.example/" + url[-5:], "text": "Buy this domain"})
    services.post(EXTRACTOR + "/extract").mock(side_effect=handler)
    e = detail(post_lead().json()["id"])["enrichment"]
    assert e["sources"] == [] and e["note"] == "no readable pages on the company site"
    assert any(s["why"] == "redirected off the company domain" for s in e["skipped"])


def test_private_address_refused_by_hub(services, monkeypatch):
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["10.0.0.7"])
    d = detail(post_lead().json()["id"])
    assert d["enrich_status"] == "failed" and "public address" in d["enrichment"]["error"]


def test_gateway_down_still_scores(services):
    services.post(GATEWAY + "/v1/run").respond(502)
    d = detail(post_lead().json()["id"])
    assert d["enrich_status"] == "failed" and d["grade"] in "ABCD"
    assert d["reply"]["status"] == "failed"


# ---------- scoring


def test_scoring_rules_unit():
    rules = scoring.load_rules()
    hot = scoring.score_lead({"message": "Can we book a demo? Pricing for our team of 30 people?",
                              "has_company_domain": True, "has_consent": True,
                              "enrichment": "software startup", "size": "team of 30",
                              "source": ["site_assistant"], "messages": 2}, rules)
    assert hot["grade"] == "A" and hot["score"] >= 70
    ids = {r["rule"] for r in hot["reasons"]}
    assert {"demo", "pricing", "company_domain", "size_stated", "repeat", "site_assistant"} <= ids
    assert all(r["reason"] for r in hot["reasons"])
    cold = scoring.score_lead({"message": "I want to apply for the internship, CV attached."}, rules)
    assert cold["grade"] == "D" and cold["score"] == 0
    assert any(r["rule"] == "not_a_buyer" for r in cold["reasons"])
    # whole words only: "recall" is not "call", "cvs" is not "cv"
    assert scoring.score_lead({"message": "recall cvs"}, rules)["reasons"] == []


def test_scoring_caps_and_grades(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("caps: {fit: 10, intent: 100, engagement: 0}\ngrades: {A: 90, B: 50, C: 20}\nrules:\n"
                 "  - {id: a, category: fit, field: message, any: [x], points: 50, reason: a}\n"
                 "  - {id: b, category: intent, field: message, any: [y], points: 40, reason: b}\n"
                 "  - {id: c, category: engagement, field: messages, min: 1, points: 30, reason: c}\n")
    r = scoring.score_lead({"message": "x y", "messages": 1}, scoring.load_rules(str(p)))
    assert r["score"] == 50 and r["grade"] == "B" and r["categories"] == {"fit": 10, "intent": 40, "engagement": 0}


def test_broken_rules_file_is_an_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("rules:\n  - {id: a, category: vibes, field: message, any: [x], points: 5}\n")
    with pytest.raises(scoring.RulesError):
        scoring.load_rules(str(p))


# ---------- routing


@pytest.mark.parametrize("message,grade,action,tag", [
    ("Can we book a demo? Pricing for our remote team of 40 people, team box as a perk?", "A", "notify_now", "hot"),
    ("Could you share pricing for the team box?", "B", "daily_digest", "digest"),
    ("Do you have a gift option?", "C", "nurture", "nurture"),
])
def test_routing_per_grade(services, message, grade, action, tag, monkeypatch):
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://hooks.example.test/notify")
    lead_id = post_lead(message=message).json()["id"]
    d = detail(lead_id)
    assert d["grade"] == grade, d["score_detail"]
    assert d["route"]["action"] == action and d["route"]["tag"] == tag
    if grade == "A":
        n = d["route"]["notify"]
        assert n["dry_run"] is True and n["sent"] is False
        assert "maya" not in json.dumps(n).lower()  # the alert carries the id, not personal data
    if grade == "B":
        dig = client.get("/digest", headers=H).json()
        assert [x["id"] for x in dig["leads"]] == [lead_id]


def test_grade_d_is_nurture(services):
    d = detail(post_lead(email="x@gmail.com", message="Job application: my CV for the internship").json()["id"])
    assert d["grade"] == "D" and d["route"]["action"] == "nurture"


# ---------- CRM


def test_crm_dry_run_sends_nothing_and_hides_token(services, monkeypatch):
    monkeypatch.setenv("HUBSPOT_TOKEN", HS_TOKEN)
    monkeypatch.setenv("PIPEDRIVE_TOKEN", PD_TOKEN)
    monkeypatch.setenv("PIPEDRIVE_BASE_URL", "https://acme.pipedrive.com")
    hs = services.route(host="api.hubapi.com").respond(500)
    pd = services.route(host="acme.pipedrive.com").respond(500)
    d = detail(post_lead().json()["id"])
    assert not hs.called and not pd.called
    crms = {c["crm"]: c for c in d["route"]["crm"]}
    assert crms["hubspot"]["dry_run"] and crms["pipedrive"]["dry_run"]
    plan = crms["hubspot"]["plan"]
    assert plan["create"]["json"]["properties"]["email"] == "maya@acme-robotics.com"
    assert plan["create"]["json"]["properties"]["firstname"] == "Maya"
    assert plan["note"]["json"]["associations"][0]["types"][0]["associationTypeId"] == 202
    assert "Can we book a demo" in plan["note"]["json"]["properties"]["hs_note_body"]
    assert crms["pipedrive"]["plan"]["lookup"]["params"]["exact_match"] == "true"
    text = json.dumps(d)
    assert HS_TOKEN not in text and PD_TOKEN not in text


def test_hubspot_create_then_update(services, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("HUBSPOT_TOKEN", HS_TOKEN)
    base = "https://api.hubapi.com/crm/v3/objects"
    lookup = services.get(f"{base}/contacts/maya@acme-robotics.com").mock(
        side_effect=[httpx.Response(404), httpx.Response(200, json={"id": "501"})])
    create = services.post(f"{base}/contacts").respond(201, json={"id": "501"})
    update = services.patch(f"{base}/contacts/501").respond(200, json={"id": "501"})
    note = services.post(f"{base}/notes").respond(201, json={"id": "9001"})
    lead_id = post_lead().json()["id"]
    c = detail(lead_id)["route"]["crm"][0]
    assert c == {"dry_run": False, "crm": "hubspot", "action": "created", "contact_id": "501", "note_id": "9001"}
    assert lookup.calls[0].request.headers["authorization"] == f"Bearer {HS_TOKEN}"
    assert lookup.calls[0].request.url.params["idProperty"] == "email"
    assert json.loads(create.calls[0].request.content)["properties"]["lifecyclestage"] == "lead"
    sent_note = json.loads(note.calls[0].request.content)
    assert sent_note["associations"][0]["to"]["id"] == "501"
    # second contact from the same person: update, not a duplicate contact
    post_lead(message="Also, can we book a call on Friday?")
    c2 = detail(lead_id)["route"]["crm"][0]
    assert c2["action"] == "updated" and create.call_count == 1 and update.call_count == 1
    assert "lifecyclestage" not in json.loads(update.calls[0].request.content)["properties"]


def test_pipedrive_create_then_update(services, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("PIPEDRIVE_TOKEN", PD_TOKEN)
    monkeypatch.setenv("PIPEDRIVE_BASE_URL", "https://acme.pipedrive.com")
    b = "https://acme.pipedrive.com"
    search = services.get(f"{b}/api/v2/persons/search").mock(side_effect=[
        httpx.Response(200, json={"success": True, "data": {"items": []}}),
        httpx.Response(200, json={"success": True, "data": {"items": [{"item": {"id": 77}}]}})])
    create = services.post(f"{b}/api/v2/persons").respond(201, json={"success": True, "data": {"id": 77}})
    update = services.patch(f"{b}/api/v2/persons/77").respond(200, json={"success": True, "data": {"id": 77}})
    note = services.post(f"{b}/api/v1/notes").respond(201, json={"success": True, "data": {"id": 5}})
    lead_id = post_lead().json()["id"]
    c = detail(lead_id)["route"]["crm"][0]
    assert c["action"] == "created" and c["person_id"] == 77
    assert search.calls[0].request.headers["x-api-token"] == PD_TOKEN
    assert search.calls[0].request.url.params["fields"] == "email"
    assert json.loads(create.calls[0].request.content)["emails"][0]["value"] == "maya@acme-robotics.com"
    assert json.loads(note.calls[0].request.content)["person_id"] == 77
    post_lead(message="One more question about the demo")
    assert detail(lead_id)["route"]["crm"][0]["action"] == "updated"
    assert "emails" not in json.loads(update.calls[0].request.content)


def test_crm_error_is_recorded_without_token(services, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("HUBSPOT_TOKEN", HS_TOKEN)
    services.get(url__startswith="https://api.hubapi.com/").respond(401, text=f"bad token {HS_TOKEN}")
    c = detail(post_lead().json()["id"])["route"]["crm"][0]
    assert c["error"] == "hubspot lookup contact: HTTP 401"


# ---------- consent


def test_no_consent_means_no_follow_up(services, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("HUBSPOT_TOKEN", HS_TOKEN)
    hs = services.route(host="api.hubapi.com").respond(500)
    body = lead_body()
    body.pop("consent")
    lead_id = client.post("/leads", json=body, headers=H).json()["id"]
    d = detail(lead_id)
    assert d["consent"] is None
    assert "no consent" in d["route"]["blocked"] and d["route"]["crm"] == []
    assert d["reply"] is None and any(a["kind"] == "reply_skipped" for a in d["timeline"])
    assert not hs.called
    assert not [c for c in services.routes[2].calls
                if json.loads(c.request.content)["prompt"] == "lead_first_reply"]
    assert client.post(f"/leads/{lead_id}/reply/approve", json={}, headers=H).status_code == 409
    # consent given later (e.g. they replied to a confirmation): follow-up is now possible
    r = client.post(f"/leads/{lead_id}/consent", json={"given": True, "text": "Ticked on call-back form"}, headers=H)
    assert r.status_code == 200 and r.json()["consent"]["text"] == "Ticked on call-back form"


def test_consent_withdrawn_blocks_send(services):
    lead_id = post_lead().json()["id"]
    client.post(f"/leads/{lead_id}/reply/approve", json={}, headers=H)
    client.post(f"/leads/{lead_id}/consent", json={"given": False}, headers=H)
    assert client.post(f"/leads/{lead_id}/reply/send", headers=H).status_code == 409


# ---------- first reply


def test_first_reply_drafted_checked_and_saved_to_calendar(services, monkeypatch):
    monkeypatch.setenv("CALENDAR_URL", CAL)
    monkeypatch.setenv("BOOKING_URL", "https://cal.example.com/intro")
    cal = services.post(CAL + "/items").respond(201, json={"id": 42})
    lead_id = post_lead().json()["id"]
    reply = detail(lead_id)["reply"]
    assert reply["status"] == "drafted" and reply["sent"] is False
    assert "espresso machine" not in reply["body"]
    assert reply["removed"] == ["We also include a free espresso machine."]
    assert "https://cal.example.com/intro" in reply["body"]
    assert reply["calendar_item_id"] == 42
    item = json.loads(cal.calls[0].request.content)
    assert item["channel"] == "lead_reply" and item["status"] == "draft"
    gw = [json.loads(c.request.content) for c in services.routes[2].calls]
    first = next(g for g in gw if g["prompt"] == "lead_first_reply")
    assert first["vars"]["lead_first_name"] == "Maya" and "message" in first["vars"]
    chk = json.loads(services.routes[3].calls[-1].request.content)
    assert chk["extra_facts"] == ["Booking link: https://cal.example.com/intro"]

    # never sent automatically: must be approved, then send is a dry-run stub
    assert client.post(f"/leads/{lead_id}/reply/send", headers=H).status_code == 409
    a = client.post(f"/leads/{lead_id}/reply/approve", json={"body": "Hi Maya, edited.", "approved_by": "sam"},
                    headers=H).json()
    assert a["status"] == "approved" and a["edited"] is True
    s = client.post(f"/leads/{lead_id}/reply/send", headers=H).json()
    assert s == {"sent": False, "dry_run": True, "would_send": {
        "to": "maya@acme-robotics.com", "subject": "Coffee for your team", "body": "Hi Maya, edited."}}
    monkeypatch.setenv("DRY_RUN", "false")
    assert client.post(f"/leads/{lead_id}/reply/send", headers=H).status_code == 501
    assert client.post(f"/leads/{lead_id}/reply/sent", headers=H).json()["status"] == "sent"
    assert detail(lead_id)["status"] == "replied"


# ---------- privacy


def test_delete_erases_everything(services, monkeypatch, tmp_path):
    monkeypatch.setenv("CALENDAR_URL", CAL)
    services.post(CAL + "/items").respond(201, json={"id": 42})
    services.get(CAL + "/items/42").respond(200, json={"id": 42, "status": "draft"})
    patch = services.patch(CAL + "/items/42").respond(200, json={"id": 42})
    status = services.post(CAL + "/items/42/status").respond(200, json={"id": 42})
    lead_id = post_lead().json()["id"]
    exp = client.get(f"/leads/{lead_id}/export", headers=H).json()
    assert exp["lead"]["email"] == "maya@acme-robotics.com" and exp["lead"]["enrichment"]["facts"]
    r = client.delete(f"/leads/{lead_id}", headers=H)
    assert r.status_code == 200 and r.json()["erased"] is True and r.json()["calendar"] == "erased"
    assert "Maya" not in patch.calls[0].request.content.decode()
    assert json.loads(status.calls[-1].request.content)["status"] == "rejected"
    assert client.get(f"/leads/{lead_id}", headers=H).status_code == 404
    assert client.get(f"/leads/{lead_id}/export", headers=H).status_code == 404
    conn = sqlite3.connect(tmp_path / "leads.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 0
    conn.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("leads.sqlite*"))
    assert b"maya@acme-robotics.com" not in raw and b"warehouse picking" not in raw


def test_retention_purges_old_leads(services, monkeypatch, tmp_path):
    lead_id = post_lead().json()["id"]
    conn = sqlite3.connect(tmp_path / "leads.sqlite")
    conn.execute("UPDATE leads SET updated_at = '2020-01-01T00:00:00Z' WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    monkeypatch.setenv("RETENTION_DAYS", "30")
    assert client.post("/admin/purge", headers=H).json()["erased"] == [lead_id]
    assert client.get(f"/leads/{lead_id}", headers=H).status_code == 404


def test_no_secrets_in_logs_or_responses(services, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HUBSPOT_TOKEN", HS_TOKEN)
    monkeypatch.setenv("PIPEDRIVE_TOKEN", PD_TOKEN)
    monkeypatch.setenv("PIPEDRIVE_BASE_URL", "https://acme.pipedrive.com")
    texts = []
    lead_id = post_lead().json()["id"]
    for path in (f"/leads/{lead_id}", f"/leads/{lead_id}/export", "/leads", "/stats", "/digest?grade=A",
                 "/health", "/scoring/rules"):
        texts.append(client.get(path, headers=H).text)
    texts.append(caplog.text)
    blob = "\n".join(texts)
    for secret in (HS_TOKEN, PD_TOKEN, KEY, "tally-secret", "tf-secret"):
        assert secret not in blob


# ---------- webhooks


def test_webhook_secret_checked(services):
    body = {"email": "sam@acme-robotics.com", "message": "demo please"}
    assert client.post("/leads/webhook/n8n", json=body).status_code == 401
    assert client.post("/leads/webhook/n8n", json=body, headers={"X-Webhook-Secret": "wrong"}).status_code == 401
    assert client.post("/leads/webhook/unknown", json=body, headers={"X-Webhook-Secret": "n8n-secret"}).status_code == 401
    r = client.post("/leads/webhook/n8n", json=body, headers={"X-Webhook-Secret": "n8n-secret"})
    assert r.status_code == 201
    d = detail(r.json()["id"])
    assert d["sources"] == ["webhook"] and d["consent"] is None


def test_tally_webhook_signature_and_mapping(services):
    payload = {"eventType": "FORM_RESPONSE", "data": {"fields": [
        {"key": "q1", "label": "Your name", "type": "INPUT_TEXT", "value": "Ana Ruiz"},
        {"key": "q2", "label": "Work email", "type": "INPUT_EMAIL", "value": "ana@acme-robotics.com"},
        {"key": "q3", "label": "Company name", "type": "INPUT_TEXT", "value": "Acme"},
        {"key": "q4", "label": "How can we help?", "type": "TEXTAREA", "value": "Pricing for 30 people?"},
        {"key": "q5", "label": "I agree to be contacted about this request", "type": "CHECKBOX", "value": True},
        {"key": "q6", "label": "utm_medium", "type": "HIDDEN_FIELDS", "value": "newsletter"},
    ]}}
    raw = json.dumps(payload).encode()
    sig = base64.b64encode(hmac.new(b"tally-secret", raw, hashlib.sha256).digest()).decode()
    bad = client.post("/leads/webhook/tally", content=raw, headers={"Tally-Signature": "AAAA",
                                                                      "content-type": "application/json"})
    assert bad.status_code == 401
    r = client.post("/leads/webhook/tally", content=raw, headers={"Tally-Signature": sig,
                                                                    "content-type": "application/json"})
    assert r.status_code == 201, r.text
    d = detail(r.json()["id"])
    assert d["name"] == "Ana Ruiz" and d["email"] == "ana@acme-robotics.com"
    assert d["consent"]["text"] == "I agree to be contacted about this request"
    assert d["utm"] == {"medium": "newsletter"}
    assert any(a["message"] == "Pricing for 30 people?" for a in d["timeline"])


def test_typeform_webhook_signature(services):
    payload = {"form_response": {
        "definition": {"fields": [{"id": "f1", "title": "Email"}, {"id": "f2", "title": "What do you need?"}]},
        "answers": [{"type": "email", "email": "leo@acme-robotics.com", "field": {"id": "f1", "ref": "email"}},
                    {"type": "text", "text": "A demo for our office", "field": {"id": "f2", "ref": "msg"}}]}}
    raw = json.dumps(payload).encode()
    sig = "sha256=" + base64.b64encode(hmac.new(b"tf-secret", raw, hashlib.sha256).digest()).decode()
    r = client.post("/leads/webhook/typeform", content=raw,
                    headers={"Typeform-Signature": sig, "content-type": "application/json"})
    assert r.status_code == 201, r.text
    assert detail(r.json()["id"])["email"] == "leo@acme-robotics.com"


def test_webhook_without_email_is_422(services):
    r = client.post("/leads/webhook/n8n", json={"message": "hi"}, headers={"X-Webhook-Secret": "n8n-secret"})
    assert r.status_code == 422


# ---------- admin


def test_list_filters_and_stats(services):
    post_lead()
    post_lead(email="x@gmail.com", message="hello", consent=None)
    assert len(client.get("/leads?consent=true", headers=H).json()) == 1
    assert len(client.get("/leads?q=gmail", headers=H).json()) == 1
    assert len(client.get("/leads?source=form", headers=H).json()) == 2
    s = client.get("/stats", headers=H).json()
    assert s["total"] == 2 and s["with_consent"] == 1 and s["by_source"] == {"form": 2}
    assert s["dry_run"] is True


def test_same_company_is_fetched_once(services):
    post_lead()
    fetched = services.routes[1].call_count
    lead2 = post_lead(email="sam@acme-robotics.com", name="Sam").json()["id"]
    assert services.routes[1].call_count == fetched
    e = detail(lead2)
    assert e["enrichment"]["facts"] and any(a["data"] and a["data"].get("reused") for a in e["timeline"])
