"""Keys, flows, versions and approval."""
import pytest

from .conftest import APPROVE, APPROVER, AUTH, KEY, STEPS, approved_flow, event, outbox, tick


@pytest.fixture(autouse=True)
def all_in_flow_arm(monkeypatch):
    monkeypatch.setenv("FLOW_HOLDOUT_PCT", "0")


def test_health_is_open_and_says_dry_run(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["dry_run"] is True and r.json()["paused"] is False and r.json()["daily_cap"] == 200


@pytest.mark.parametrize("method,path", [
    ("get", "/flows"), ("post", "/flows"), ("get", "/flows/welcome"), ("patch", "/flows/welcome"),
    ("post", "/flows/welcome/versions"), ("post", "/flows/welcome/draft"),
    ("post", "/flows/welcome/versions/1/submit"), ("post", "/flows/welcome/versions/1/approve"),
    ("post", "/flows/welcome/versions/1/reject"), ("post", "/reviews/sync"), ("post", "/events"),
    ("post", "/tick"), ("post", "/pause"), ("post", "/resume"), ("get", "/outbox"),
    ("get", "/flows/welcome/results"),
])
def test_every_endpoint_but_health_needs_the_key(client, method, path):
    assert getattr(client, method)(path).status_code == 401
    assert getattr(client, method)(path, headers={"X-API-Key": "wrong"}).status_code == 401


def test_no_key_configured_refuses(client, monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.get("/flows", headers=AUTH).status_code == 503


def test_built_in_flows(client):
    flows = {f["name"]: f for f in client.get("/flows", headers=AUTH).json()}
    assert set(flows) == {"welcome", "onboarding", "winback"}
    assert flows["welcome"]["trigger"] == "subscribed" and flows["onboarding"]["trigger"] == "trial_started"
    assert flows["winback"]["trigger"] == "inactive"
    assert not any(f["running"] for f in flows.values())


def test_custom_flow_and_validation(client):
    r = client.post("/flows", json={"name": "Post-Purchase", "trigger": "first_order",
                                    "exit_events": ["refund_requested"]}, headers=AUTH)
    assert r.status_code == 201
    assert r.json()["name"] == "post-purchase"
    assert r.json()["default_exit_events"] == ["unsubscribed", "refund_requested"]  # unsubscribed is always added
    assert client.post("/flows", json={"name": "post-purchase", "trigger": "x"}, headers=AUTH).status_code == 409
    assert client.post("/flows", json={"name": "bad name!", "trigger": "x"}, headers=AUTH).status_code == 422
    assert client.post("/flows", json={"name": "u", "trigger": "unsubscribed"}, headers=AUTH).status_code == 422
    assert client.get("/flows/nope", headers=AUTH).status_code == 404


def test_steps_must_be_in_order_and_non_empty(client):
    bad = [{"delay_hours": 48, "subject": "b", "body_markdown": "b"}, {"delay_hours": 0, "subject": "a", "body_markdown": "a"}]
    assert client.post("/flows/welcome/versions", json={"steps": bad}, headers=AUTH).status_code == 422
    assert client.post("/flows/welcome/versions", json={"steps": []}, headers=AUTH).status_code == 422
    blank = [{"delay_hours": 0, "subject": "  ", "body_markdown": "x"}]
    assert client.post("/flows/welcome/versions", json={"steps": blank}, headers=AUTH).status_code == 422


def test_approval_needs_the_approver_key(client):
    client.post("/flows/welcome/versions", json={"steps": STEPS}, headers=AUTH)
    assert client.post("/flows/welcome/versions/1/approve", headers=AUTH).status_code == 403
    assert client.post("/flows/welcome/versions/1/approve",
                       headers={**AUTH, "X-Approver-Key": KEY}).status_code == 403  # the service key is not enough
    assert client.post("/flows/welcome/versions/1/approve", headers={"X-Approver-Key": APPROVER}).status_code == 401
    r = client.post("/flows/welcome/versions/1/approve", json={"approved_by": "Sam"}, headers=APPROVE)
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["approved_by"] == "Sam"
    assert client.post("/flows/welcome/versions/1/approve", headers=APPROVE).status_code == 409


def test_unset_approver_key_refuses_approval(client, monkeypatch):
    client.post("/flows/welcome/versions", json={"steps": STEPS}, headers=AUTH)
    monkeypatch.delenv("APPROVER_KEY")
    assert client.post("/flows/welcome/versions/1/approve", headers={**AUTH, "X-Approver-Key": ""}).status_code == 503


def test_a_draft_version_never_sends(client, clock):
    client.post("/flows/welcome/versions", json={"steps": STEPS}, headers=AUTH)
    r = event(client, "subscribed", "ana@example.com", consent=True)
    assert r["entered"] == [] and r["skipped"] == [{"flow": "welcome", "reason": "flow has no approved version"}]
    clock.advance(days=10)
    assert tick(client)["sent"] == 0 and outbox(client) == []


def test_editing_makes_a_new_draft_and_the_approved_one_keeps_running(client, clock):
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    new = [dict(s, subject=s["subject"] + " (new)") for s in STEPS]
    r = client.post("/flows/welcome/versions", json={"steps": new}, headers=AUTH)
    assert r.status_code == 201 and r.json()["version"] == 2 and r.json()["status"] == "draft"
    assert r.json()["exit_events"] == ["unsubscribed"]
    tick(client)
    assert [o["subject"] for o in outbox(client)] == ["Welcome to Northwind"]  # v1: v2 is not approved
    client.post("/flows/welcome/versions/2/approve", headers=APPROVE)
    flow = client.get("/flows/welcome", headers=AUTH).json()
    assert flow["approved_version"] == 2
    assert [v["status"] for v in flow["versions"]] == ["retired", "approved"]
    clock.advance(hours=48)
    tick(client)
    assert outbox(client)[0]["subject"] == "How to brew it right (new)"  # step 2 of v2, never step 1 again


def test_reject_and_submit_without_calendar(client):
    client.post("/flows/welcome/versions", json={"steps": STEPS}, headers=AUTH)
    r = client.post("/flows/welcome/versions/1/submit", headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "in_review" and r.json()["calendar_item_id"] is None
    assert client.post("/flows/welcome/versions/1/reject", headers=AUTH).json()["status"] == "rejected"
    assert client.post("/flows/welcome/versions/1/approve", headers=APPROVE).status_code == 409
    assert client.post("/flows/welcome/versions/1/submit", headers=AUTH).status_code == 409


def test_patch_mode_and_holdout(client):
    r = client.patch("/flows/welcome", json={"mode": "aa", "holdout_pct": 50}, headers=AUTH)
    assert r.json()["mode"] == "aa" and r.json()["holdout_pct"] == 50
    assert client.patch("/flows/welcome", json={"holdout_pct": 80}, headers=AUTH).status_code == 422
