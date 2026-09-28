"""Consent, suppression, exits and idempotent events."""
import sqlite3

import pytest

from app import main

from .conftest import AUTH, approved_flow, event, outbox, tick


@pytest.fixture(autouse=True)
def all_in_flow_arm(monkeypatch):
    monkeypatch.setenv("FLOW_HOLDOUT_PCT", "0")


def test_no_consent_no_entry(client, clock):
    approved_flow(client)
    r = event(client, "subscribed", "no@example.com")
    assert r["entered"] == [] and r["skipped"][0]["reason"] == "no recorded consent"
    r = event(client, "subscribed", "false@example.com", consent=False)
    assert r["skipped"][0]["reason"] == "no recorded consent"
    clock.advance(days=7)
    for _ in range(3):
        tick(client)
        clock.advance(days=2)
    assert outbox(client) == []


def test_consent_recorded_earlier_counts(client, clock):
    approved_flow(client, "onboarding")
    event(client, "subscribed", "ana@example.com", consent=True)       # consent from the signup
    r = event(client, "trial_started", "ana@example.com")              # no consent field: no change
    assert r["entered"] == [{"flow": "onboarding", "arm": "flow"}]


def test_withdrawn_consent_exits_and_blocks(client, clock):
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    tick(client)
    r = event(client, "preferences_changed", "ana@example.com", consent=False)
    assert r["exited"] == ["welcome"]
    clock.advance(days=10)
    tick(client)
    assert len(outbox(client)) == 1


def test_unsubscribed_is_forever(client, clock):
    approved_flow(client)
    approved_flow(client, "winback")
    event(client, "subscribed", "ana@example.com", consent=True)
    tick(client)
    r = event(client, "unsubscribed", "ana@example.com")
    assert r["exited"] == ["welcome"]
    clock.advance(days=1)
    # Consent again, a new trigger, another flow: still nothing, ever.
    r = event(client, "subscribed", "ana@example.com", consent=True)
    assert r["skipped"] == [{"flow": "welcome", "reason": "unsubscribed"}]
    r = event(client, "inactive", "ana@example.com", consent=True)
    assert r["skipped"] == [{"flow": "winback", "reason": "unsubscribed"}]
    for _ in range(20):
        clock.advance(hours=12)
        tick(client)
    assert [o["step_index"] for o in outbox(client)] == [0]


def test_exit_event_ends_the_flow(client, clock):
    approved_flow(client, "onboarding", exits=["purchased"])
    event(client, "trial_started", "ana@example.com", consent=True)
    tick(client)
    clock.advance(hours=1)
    r = event(client, "purchased", "ana@example.com")
    assert r["exited"] == ["onboarding"]
    clock.advance(days=10)
    tick(client)
    assert len(outbox(client)) == 1


def test_purchase_before_entry_does_not_exit(client, clock):
    approved_flow(client, "winback", exits=["purchased"])
    event(client, "purchased", "ana@example.com", consent=True, at="2026-08-01T10:00:00Z")
    event(client, "inactive", "ana@example.com")
    tick(client)
    clock.advance(hours=48)
    tick(client)
    assert len(outbox(client)) == 2


def test_exit_is_checked_right_before_the_send(client, clock):
    """An exit event that reached the database without passing through POST /events (a race,
    a late write) still stops the send: the tick checks inside the send transaction."""
    approved_flow(client, "onboarding", exits=["purchased"])
    event(client, "trial_started", "ana@example.com", consent=True)
    tick(client)
    conn = sqlite3.connect(main.db_path())
    conn.execute("INSERT INTO events (type, email, at, received_at) VALUES ('purchased', 'ana@example.com',"
                 " '2026-09-01T09:00:00Z', '2026-09-01T09:00:00Z')")
    conn.commit()
    conn.close()
    clock.advance(hours=48)
    r = tick(client)
    assert r["sent"] == 0 and r["exited"] == 1
    flow = client.get("/flows/onboarding", headers=AUTH).json()
    assert flow["enrollments"] == {"flow_exited": 1}


def test_suppression_is_checked_right_before_the_send(client, clock):
    approved_flow(client)
    event(client, "subscribed", "ana@example.com", consent=True)
    tick(client)
    conn = sqlite3.connect(main.db_path())
    conn.execute("UPDATE contacts SET suppressed_at = '2026-09-01T09:00:00Z'")
    conn.commit()
    conn.close()
    clock.advance(hours=48)
    assert tick(client)["sent"] == 0


def test_event_id_makes_it_idempotent(client, clock):
    approved_flow(client)
    a = event(client, "subscribed", "ana@example.com", consent=True, id_="evt-1")
    b = event(client, "subscribed", "ana@example.com", consent=True, id_="evt-1")
    assert a["entered"] and not a["duplicate"]
    assert b["duplicate"] is True and b["entered"] == []
    c = event(client, "subscribed", "ana@example.com", consent=True)  # no id: no second entry either
    assert c["skipped"] == [{"flow": "welcome", "reason": "already entered this flow"}]


def test_old_and_future_events(client, clock):
    approved_flow(client)
    r = event(client, "subscribed", "old@example.com", consent=True, at="2026-08-01T08:00:00Z")
    assert r["skipped"][0]["reason"] == "event is too old to start a flow"   # no burst of month-old emails
    body = {"type": "subscribed", "contact": {"email": "f@example.com"}, "at": "2026-09-02T08:00:00Z"}
    assert client.post("/events", json=body, headers=AUTH).status_code == 422
    body = {"type": "Sub scribed", "contact": {"email": "f@example.com"}}
    assert client.post("/events", json=body, headers=AUTH).status_code == 422
    body = {"type": "subscribed", "contact": {"email": "not-an-email"}}
    assert client.post("/events", json=body, headers=AUTH).status_code == 422
