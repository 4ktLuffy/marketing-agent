import os
from datetime import datetime, timedelta, timezone

import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.main import app

KEY = "internal-secret-key-123"
APPROVER = "approver-secret-key-456"
AUTH = {"X-API-Key": KEY}
APPROVE = {**AUTH, "X-Approver-Key": APPROVER}
T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

STEPS = [
    {"delay_hours": 0, "subject": "Welcome to Northwind", "body_markdown": "Thanks for joining.\n\n**Start here**"},
    {"delay_hours": 48, "subject": "How to brew it right", "body_markdown": "Grind, bloom, pour."},
    {"delay_hours": 120, "subject": "Your first bag", "body_markdown": "Pick a roast.\n\n**Choose a roast**"},
]


class Clock:
    """The service's clock, moved by the tests."""

    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("FLOW_") or k in ("DRY_RUN", "CALENDAR_URL", "GATEWAY_URL", "CLAIMS_URL", "NEWSLETTER_URL",
                                          "APPROVER_KEY"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("APPROVER_KEY", APPROVER)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "flows.sqlite"))
    monkeypatch.setenv("FLOW_SALT", "test-salt")


@pytest.fixture
def clock(monkeypatch):
    c = Clock(T0)
    monkeypatch.setattr(main, "now_dt", c)
    return c


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def no_network():
    """Any outgoing HTTP call fails the test (nothing is mocked)."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        yield mock


def approved_flow(client, name="welcome", steps=STEPS, exits=None):
    body = {"steps": steps}
    if exits is not None:
        body["exit_events"] = exits
    r = client.post(f"/flows/{name}/versions", json=body, headers=AUTH)
    assert r.status_code == 201, r.text
    v = r.json()["version"]
    r = client.post(f"/flows/{name}/versions/{v}/approve", json={"approved_by": "Sam"}, headers=APPROVE)
    assert r.status_code == 200, r.text
    return v


def event(client, type_, email, consent=None, at=None, id_=None, data=None):
    body = {"type": type_, "contact": {"email": email}}
    if consent is not None:
        body["contact"]["consent"] = consent
        body["contact"]["source"] = "signup form"
    if at is not None:
        body["at"] = at
    if id_ is not None:
        body["id"] = id_
    if data is not None:
        body["data"] = data
    r = client.post("/events", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def tick(client):
    r = client.post("/tick", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def outbox(client, **q):
    r = client.get("/outbox", params={"limit": 1000, **q}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()
