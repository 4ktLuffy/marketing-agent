"""Drafting (03 + 44), review in the calendar (19) and approval carried back from it."""
import json

import httpx
import pytest
import respx

from app import sequence

from .conftest import APPROVE, AUTH, STEPS, event, outbox, tick

GW, CL, CAL = "http://gateway.test", "http://claims.test", "http://calendar.test"
EMAILS = [
    {"day": 0, "subject": "Welcome to Northwind Roasters", "preview": "Your first cup starts here",
     "body": "Hi there,\n\nThanks for joining. Our beans are roasted every Monday.\n\nThe Northwind team", "cta_text": "Pick a roast"},
    {"day": 3, "subject": "Brew it the easy way today", "preview": "Three steps, no gear",
     "body": "Hi again,\n\nGrind, bloom, pour. We ship in 24 hours.\n\nThe Northwind team", "cta_text": "See the guide"},
    {"day": 7, "subject": "Still deciding on a roast?", "preview": "A short quiz helps",
     "body": "Hello,\n\nTake the quiz and we suggest one.\n\nThe Northwind team", "cta_text": "Take the quiz"},
]


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", GW)
    monkeypatch.setenv("CLAIMS_URL", CL)
    monkeypatch.setenv("CALENDAR_URL", CAL)
    monkeypatch.setenv("FLOW_HOLDOUT_PCT", "0")
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        yield mock


def verify(request):
    text = json.loads(request.content)["text"]
    bad = "We ship in 24 hours." in text
    return httpx.Response(200, json={"claims": [{"claim": "We ship in 24 hours.", "supported": False}] if bad else [],
                                     "unsupported": ["the number 24"] if bad else []})


def test_sequence_round_trip_and_errors():
    text = sequence.render("welcome", 2, STEPS, ["unsubscribed"])
    assert text.startswith("Flow: welcome · version 2 · 3 emails\nExit when: unsubscribed")
    assert sequence.parse(text) == STEPS
    edited = text.replace("Grind, bloom, pour.", "Grind, bloom, pour slowly.").replace("48 hours", "72 hours")
    assert sequence.parse(edited)[1] == {"delay_hours": 72, "subject": "How to brew it right",
                                         "body_markdown": "Grind, bloom, pour slowly."}
    for broken, why in ((text.replace("Subject: How to brew it right", "How to brew"), "Subject"),
                        ("just some text", "no '--- Email")):
        with pytest.raises(sequence.SequenceError, match=why):
            sequence.parse(broken)


def test_draft_uses_the_email_sequence_prompt_and_flags_claims(client, svc):
    gw = svc.post(f"{GW}/v1/run").respond(200, json={"output": {"emails": EMAILS}})
    svc.post(f"{CL}/verify").mock(side_effect=verify)
    r = client.post("/flows/welcome/draft", json={"goal": "First order from new subscribers",
                                                  "link": "https://northwind.example/roasts"}, headers=AUTH)
    assert r.status_code == 201, r.text
    v = r.json()
    sent = json.loads(gw.calls[0].request.content)
    assert gw.calls[0].request.headers["X-Caller"] == "86 email flows"
    assert sent["prompt"] == "email_sequence" and sent["vars"]["goal"] == "First order from new subscribers"
    assert v["status"] == "draft" and v["source"] == "llm"
    assert [s["delay_hours"] for s in v["steps"]] == [0, 72, 168]
    assert v["steps"][0]["body_markdown"].endswith("[Pick a roast](https://northwind.example/roasts)")
    assert v["claims_flagged"] and "email 2" in v["claims_flagged"][0] and "24 hours" in v["notes"]


def test_draft_fails_cleanly(client, svc, monkeypatch):
    svc.post(f"{GW}/v1/run").respond(500)
    assert client.post("/flows/welcome/draft", json={"goal": "x" * 10}, headers=AUTH).status_code == 502
    monkeypatch.setenv("GATEWAY_URL", "")
    r = client.post("/flows/welcome/draft", json={"goal": "x" * 10}, headers=AUTH)
    assert r.status_code == 502 and "GATEWAY_URL" in r.json()["detail"]
    assert client.get("/flows/welcome", headers=AUTH).json()["versions"] == []


def submitted(client, svc, item_id=41):
    client.post("/flows/welcome/versions", json={"steps": STEPS}, headers=AUTH)
    create = svc.post(f"{CAL}/items").respond(201, json={"id": item_id, "status": "in_review"})
    r = client.post("/flows/welcome/versions/1/submit", headers=AUTH)
    assert r.status_code == 200 and r.json()["calendar_item_id"] == item_id
    return json.loads(create.calls[0].request.content), r.json()


def test_submit_puts_one_email_flow_item_in_review(client, svc):
    item, v = submitted(client, svc)
    assert item["channel"] == "email_flow" and item["status"] == "in_review"
    assert item["title"] == "Email flow: welcome v1 (3 emails)"
    assert sequence.parse(item["body"]) == STEPS            # the whole sequence, readable back
    assert v["status"] == "in_review"


def test_sync_approves_only_with_the_approver_key(client, svc, clock):
    item, _ = submitted(client, svc)
    svc.get(f"{CAL}/items/41").respond(200, json={"id": 41, "status": "approved", "body": item["body"]})
    r = client.post("/reviews/sync", headers=AUTH).json()
    assert r["approved"] == [] and r["waiting_for_approver_key"] == ["welcome v1"]
    event(client, "subscribed", "ana@example.com", consent=True)
    assert tick(client)["sent"] == 0                         # still not approved
    r = client.post("/reviews/sync", headers=APPROVE).json()
    assert r["approved"] == ["welcome v1"]
    flow = client.get("/flows/welcome", headers=AUTH).json()
    assert flow["approved_version"] == 1 and "19 item 41" in flow["versions"][0]["approved_by"]
    assert client.post("/reviews/sync", headers=APPROVE).json()["approved"] == []   # done once


def test_sync_approves_the_text_as_edited_in_review(client, svc, clock):
    item, _ = submitted(client, svc)
    edited = item["body"].replace("Thanks for joining.", "Thanks for joining us!")
    svc.get(f"{CAL}/items/41").respond(200, json={"id": 41, "status": "approved", "body": edited})
    r = client.post("/reviews/sync", headers=APPROVE).json()
    assert r["approved"] == ["welcome v2"]
    vs = client.get("/flows/welcome", headers=AUTH).json()["versions"]
    assert [(v["version"], v["status"], v["source"]) for v in vs] == [(1, "superseded", "manual"), (2, "approved", "calendar_edit")]
    event(client, "subscribed", "ana@example.com", consent=True)
    tick(client)
    assert outbox(client)[0]["body_markdown"].startswith("Thanks for joining us!")


def test_sync_refuses_an_unreadable_edit(client, svc):
    submitted(client, svc)
    svc.get(f"{CAL}/items/41").respond(200, json={"id": 41, "status": "approved", "body": "I rewrote it all as prose."})
    r = client.post("/reviews/sync", headers=APPROVE).json()
    assert r["approved"] == [] and "could not be read" in r["problems"][0]
    assert client.get("/flows/welcome", headers=AUTH).json()["approved_version"] is None


def test_sync_rejects_waits_and_reports_problems(client, svc):
    submitted(client, svc)
    route = svc.get(f"{CAL}/items/41")
    route.respond(200, json={"id": 41, "status": "in_review", "body": "x"})
    assert client.post("/reviews/sync", headers=APPROVE).json()["waiting"] == ["welcome v1"]
    route.respond(503)
    assert "HTTP 503" in client.post("/reviews/sync", headers=APPROVE).json()["problems"][0]
    route.respond(200, json={"id": 41, "status": "rejected", "body": "x"})
    assert client.post("/reviews/sync", headers=APPROVE).json()["rejected"] == ["welcome v1"]
    assert client.get("/flows/welcome", headers=AUTH).json()["versions"][0]["status"] == "rejected"


def test_sync_creates_a_missing_item(client, svc, monkeypatch):
    monkeypatch.setenv("CALENDAR_URL", "")
    client.post("/flows/welcome/versions", json={"steps": STEPS}, headers=AUTH)
    client.post("/flows/welcome/versions/1/submit", headers=AUTH)       # no calendar then
    monkeypatch.setenv("CALENDAR_URL", CAL)
    svc.post(f"{CAL}/items").respond(201, json={"id": 7})
    r = client.post("/reviews/sync", headers=AUTH).json()
    assert r["created"] == [{"version": "welcome v1", "calendar_item_id": 7}]


def test_sync_without_calendar_says_how_to_approve(client, monkeypatch):
    r = client.post("/reviews/sync", headers=AUTH).json()
    assert "approve" in r["note"]
