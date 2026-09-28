"""Sending: due steps, daily cap, kill switch, dry run, the stub send path and a month's replay."""
import json
import random
from datetime import timedelta

import pytest
import respx

from app import main

from .conftest import APPROVE, AUTH, T0, approved_flow, event, outbox, tick

BRIDGE = "http://listmonk-bridge.test"


@pytest.fixture
def flow_arm_only(monkeypatch):
    monkeypatch.setenv("FLOW_HOLDOUT_PCT", "0")


def test_steps_go_out_when_due_once_each(client, clock, flow_arm_only, no_network):
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    assert tick(client)["sent"] == 1
    assert tick(client)["sent"] == 0              # same tick again: nothing new
    clock.advance(hours=47)
    assert tick(client)["sent"] == 0              # step 2 is at 48 h
    clock.advance(hours=1)
    assert tick(client)["sent"] == 1
    clock.advance(days=30)
    assert tick(client)["sent"] == 1
    assert tick(client)["sent"] == 0              # completed
    rows = outbox(client)
    assert sorted(o["step_index"] for o in rows) == [0, 1, 2]
    assert {o["status"] for o in rows} == {"dry_run"}
    assert client.get("/flows/welcome", headers=AUTH).json()["enrollments"] == {"flow_completed": 1}
    assert not no_network.calls                   # DRY_RUN never calls anything


def test_missed_ticks_never_send_two_in_a_row(client, clock, flow_arm_only):
    steps = [{"delay_hours": 0, "subject": "a", "body_markdown": "a"},
             {"delay_hours": 1, "subject": "b", "body_markdown": "b"}]
    approved_flow(client, steps=steps)
    event(client, "subscribed", "ana@example.com", consent=True)
    clock.advance(hours=5)                        # the tick was down: both steps are due
    assert tick(client)["sent"] == 1
    assert tick(client)["sent"] == 0              # FLOW_MIN_GAP_HOURS (12) after the first
    clock.advance(hours=12)
    assert tick(client)["sent"] == 1


def test_holdout_gets_nothing(client, clock, monkeypatch):
    monkeypatch.setenv("FLOW_HOLDOUT_PCT", "50")
    approved_flow(client)
    arms = {}
    for i in range(40):
        arms[f"c{i}@example.com"] = event(client, "subscribed", f"c{i}@example.com", consent=True)["entered"][0]["arm"]
    for _ in range(12):
        tick(client)
        clock.advance(hours=12)
    got = {o["email"] for o in outbox(client)}
    assert got == {e for e, a in arms.items() if a == "flow"}
    assert 0 < len(got) < 40


def test_aa_mode_sends_to_both_arms(client, clock, monkeypatch):
    monkeypatch.setenv("FLOW_HOLDOUT_PCT", "50")
    approved_flow(client)
    client.patch("/flows/welcome", json={"mode": "aa"}, headers=AUTH)
    for i in range(20):
        event(client, "subscribed", f"c{i}@example.com", consent=True)
    tick(client)
    rows = outbox(client)
    assert len(rows) == 20 and {o["arm"] for o in rows} == {"flow", "holdout"}


def test_daily_cap(client, clock, flow_arm_only, monkeypatch):
    monkeypatch.setenv("FLOW_DAILY_CAP", "5")
    approved_flow(client)
    for i in range(8):
        event(client, "subscribed", f"c{i}@example.com", consent=True)
    r = tick(client)
    assert (r["sent"], r["capped"], r["sent_today"]) == (5, True, 5)
    assert len(r["notices"]) == 1 and "cap" in r["notices"][0]
    clock.advance(minutes=15)
    r = tick(client)
    assert (r["sent"], r["capped"], r["notices"]) == (0, True, [])   # the cap notice is sent once a day
    clock.advance(days=1)
    r = tick(client)
    assert r["sent"] == 3 and len(outbox(client)) == 8


def test_cap_zero_sends_nothing(client, clock, flow_arm_only, monkeypatch):
    monkeypatch.setenv("FLOW_DAILY_CAP", "0")
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    assert tick(client)["sent"] == 0 and outbox(client) == []


def test_pause_endpoint_and_resume(client, clock, flow_arm_only):
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    assert client.post("/pause", json={"reason": "wrong offer"}, headers=AUTH).json()["paused"] is True
    r = tick(client)
    assert r["paused"] is True and r["sent"] == 0 and "wrong offer" in r["notices"][0]
    assert tick(client)["notices"] == []                     # said once per pause
    assert client.get("/health").json()["paused"] is True
    r = event(client, "subscribed", "bo@example.com", consent=True)
    assert r["skipped"] == [{"flow": "welcome", "reason": "flows are paused"}]
    assert client.post("/resume", headers=AUTH).status_code == 403   # resuming needs the approver key
    assert client.post("/resume", headers=APPROVE).json()["paused"] is False
    assert tick(client)["sent"] == 1


def test_pause_env_cannot_be_resumed_by_api(client, clock, flow_arm_only, monkeypatch):
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    monkeypatch.setenv("FLOW_PAUSED", "true")
    assert client.post("/resume", headers=APPROVE).json()["paused"] is True
    r = tick(client)
    assert r["paused"] and r["sent"] == 0 and "FLOW_PAUSED" in r["notices"][0]


def test_dry_run_default_never_calls_the_bridge(client, clock, flow_arm_only, monkeypatch, no_network):
    monkeypatch.setenv("NEWSLETTER_URL", BRIDGE)
    for value in ("", "true", "TRUE", "1", "maybe"):
        monkeypatch.setenv("DRY_RUN", value)
        assert main.dry_run() is True
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    assert tick(client)["dry_run"] is True
    assert not no_network.calls and outbox(client)[0]["status"] == "dry_run"


def test_real_send_stub_goes_through_the_bridge_once(client, clock, flow_arm_only, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("NEWSLETTER_URL", BRIDGE)
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    with respx.mock(assert_all_mocked=True) as mock:
        route = mock.post(f"{BRIDGE}/tx").respond(200, json={"ok": True})
        r = tick(client)
        assert r["sent"] == 1 and route.call_count == 1
        sent = json.loads(route.calls[0].request.content)
        assert sent["to"] == "ana@example.com" and sent["subject"] == "Welcome to Northwind"
        assert route.calls[0].request.headers["X-API-Key"]
        tick(client)
        assert route.call_count == 1
    assert outbox(client)[0]["status"] == "sent"


def test_real_send_failure_is_recorded_and_never_retried(client, clock, flow_arm_only, monkeypatch):
    """63 has no /tx today: the stub's call gets 404, the row says failed, and no later tick retries."""
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("NEWSLETTER_URL", BRIDGE)
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    with respx.mock(assert_all_mocked=True) as mock:
        route = mock.post(f"{BRIDGE}/tx").respond(404, json={"detail": "Not Found"})
        r = tick(client)
        assert (r["sent"], r["failed"]) == (0, 1)
        tick(client)
        assert route.call_count == 1
    row = outbox(client)[0]
    assert row["status"] == "failed" and "404" in row["detail"]


def test_real_send_refuses_without_approval_or_over_cap(client, clock, flow_arm_only, monkeypatch, no_network):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("NEWSLETTER_URL", BRIDGE)
    client.post("/flows/welcome/versions", json={"steps": [{"delay_hours": 0, "subject": "s", "body_markdown": "b"}]},
                headers=AUTH)                               # a draft only
    event(client, "subscribed", "ana@example.com", consent=True)
    assert tick(client)["sent"] == 0
    monkeypatch.setenv("FLOW_DAILY_CAP", "0")
    client.post("/flows/welcome/versions/1/approve", headers=APPROVE)
    event(client, "subscribed", "bo@example.com", consent=True)
    assert tick(client)["sent"] == 0
    assert not no_network.calls


def test_real_send_without_bridge_url_fails_closed(client, clock, flow_arm_only, monkeypatch, no_network):
    monkeypatch.setenv("DRY_RUN", "false")
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    assert tick(client)["failed"] == 1
    assert "NEWSLETTER_URL" in outbox(client)[0]["detail"] and not no_network.calls


# ---------- a month of synthetic events, replayed


def month_of_events(seed=7, contacts=150):
    """Signups over 30 days, some without consent, some who unsubscribe or buy, some clicks."""
    rng = random.Random(seed)
    evs = []
    for i in range(contacts):
        email = f"p{i}@shop.example"
        start = T0 + timedelta(minutes=rng.randrange(0, 30 * 24 * 60))
        consent = rng.random() > 0.1
        evs.append({"id": f"sub-{i}", "type": "subscribed", "email": email, "consent": consent, "at": start})
        evs.append({"id": f"trial-{i}", "type": "trial_started", "email": email, "at": start + timedelta(hours=2)})
        r = rng.random()
        if r < 0.2:
            evs.append({"id": f"unsub-{i}", "type": "unsubscribed", "email": email,
                        "at": start + timedelta(hours=rng.randrange(1, 200))})
        elif r < 0.4:
            evs.append({"id": f"buy-{i}", "type": "purchased", "email": email,
                        "at": start + timedelta(hours=rng.randrange(1, 200))})
        if rng.random() < 0.3:
            evs.append({"id": f"click-{i}", "type": "clicked", "email": email,
                        "at": start + timedelta(hours=rng.randrange(1, 100))})
    return sorted(evs, key=lambda e: (e["at"], e["id"]))


def replay(client, clock, evs, twice=False):
    """Hourly ticks over 40 days; each event is posted in the hour it happened (twice if asked)."""
    approved_flow(client)
    approved_flow(client, "onboarding", exits=["purchased"])
    t, i = T0, 0
    while t < T0 + timedelta(days=40):
        clock.t = t
        while i < len(evs) and evs[i]["at"] <= t:
            e = evs[i]
            for _ in range(2 if twice else 1):
                event(client, e["type"], e["email"], consent=e.get("consent"), at=main.fmt(e["at"]), id_=e["id"])
            i += 1
        tick(client)
        t += timedelta(hours=1)
    return outbox(client)


def key(rows):
    return sorted((o["email"], o["flow"], o["step_index"], o["arm"], o["created_at"]) for o in rows)


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    def use(name):
        monkeypatch.setenv("DB_PATH", str(tmp_path / name))
    return use


def test_month_replayed_twice_gives_the_same_outbox(client, clock, fresh_db, no_network):
    evs = month_of_events()
    fresh_db("run1.sqlite")
    first = replay(client, clock, evs)
    fresh_db("run2.sqlite")
    second = replay(client, clock, evs, twice=True)          # every event delivered twice
    assert key(first) == key(second)
    assert len(first) > 100
    # Nobody gets the same step of the same flow twice.
    steps = [(o["email"], o["flow"], o["step_index"]) for o in first]
    assert len(steps) == len(set(steps))
    # Nobody unsubscribed gets mail after unsubscribing; nobody without consent gets any.
    unsub = {e["email"]: e["at"] for e in evs if e["type"] == "unsubscribed"}
    no_consent = {e["email"] for e in evs if e["type"] == "subscribed" and not e["consent"]}
    for o in first:
        assert o["email"] not in no_consent
        if o["email"] in unsub:
            assert main.parse_time(o["created_at"]) < unsub[o["email"]]
    # Nobody in onboarding gets mail after buying (its exit event).
    bought = {e["email"]: e["at"] for e in evs if e["type"] == "purchased"}
    for o in first:
        if o["flow"] == "onboarding" and o["email"] in bought:
            assert main.parse_time(o["created_at"]) < bought[o["email"]]
    # The holdout got nothing.
    assert {o["arm"] for o in first} == {"flow"}
    assert not no_network.calls
