"""Who may call, what is refused, health, configuration. No calendar needed."""
import pytest

from tests.conftest import CONTROL, CONTROL_H, KEY, SECRETS

ONE = {"reviewer": "Sam", "decisions": [{"id": 1, "decision": "approve"}]}


def test_health_needs_no_key_and_shows_no_secret(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["problems"] == [] and body["calendar"] is False  # nothing answers
    assert not any(s in r.text for s in SECRETS)


def test_health_says_what_is_missing_by_name(client, monkeypatch):
    monkeypatch.delenv("APPROVER_KEY")
    monkeypatch.setenv("CONTROL_ROOM_KEY", "short")
    body = client.get("/health").json()
    assert body["status"] == "misconfigured"
    assert body["problems"] == ["APPROVER_KEY is not set", "CONTROL_ROOM_KEY is shorter than 16 characters"]


@pytest.mark.parametrize("unset", ["INTERNAL_API_KEY", "APPROVER_KEY", "CONTROL_ROOM_KEY", "CALENDAR_URL"])
def test_missing_configuration_refuses_everything(client, monkeypatch, unset):
    monkeypatch.delenv(unset)
    r = client.post("/decisions", json=ONE, headers=CONTROL_H)
    assert r.status_code == 503
    assert r.json()["ok"] is False and f"{unset} is not set" in r.json()["error"]
    assert not any(s in r.text for s in SECRETS)


def test_control_key_must_differ_from_the_other_keys(client, monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_KEY", KEY)
    r = client.post("/decisions", json=ONE, headers={"X-Control-Key": KEY})
    assert r.status_code == 503 and "must differ" in r.json()["error"]


@pytest.mark.parametrize("headers", [
    {}, {"X-Control-Key": ""}, {"X-Control-Key": CONTROL + "x"}, {"X-Control-Key": CONTROL[:-1]},
    {"X-Control-Key": CONTROL[:-1] + "X"}, {"X-API-Key": KEY}, {"X-Approver-Key": CONTROL},
])
def test_wrong_or_missing_control_key_is_401(client, headers):
    r = client.post("/decisions", json=ONE, headers=headers)
    assert r.status_code == 401
    assert r.json() == {"ok": False, "error": "unauthorized", "detail": "unauthorized"}


def test_the_key_is_checked_before_the_body(client):
    assert client.post("/decisions", content=b"not json").status_code == 401


@pytest.mark.parametrize("body,error", [
    (b"not json", "body: JSON {reviewer, decisions}"),
    (b"[]", "decisions: 1 to 50 entries"),
    (b'{"decisions": []}', "decisions: 1 to 50 entries"),
    (b'{"decisions": [{"id": 0, "decision": "approve"}]}', "decisions[0].id: a positive integer"),
    (b'{"decisions": [{"id": 1, "decision": "publish"}]}',
     "decisions[0].decision: one of approve, edit, reject_rewrite, reject_drop, back_to_draft, skip"),
    (b'{"decisions": [{"id": 1, "decision": "approve", "reason": "' + b"r" * 501 + b'"}]}',
     "decisions[0]: text/reason/publish_at must be short text"),
    (b'{"decisions": [{"id": 1, "decision": "approve", "publish_at": "' + b"2" * 41 + b'"}]}',
     "decisions[0]: text/reason/publish_at must be short text"),
    (b'{"decisions": [{"id": 1, "decision": "approve", "seen_sha256": "abc"}]}',
     "decisions[0].seen_sha256: 64 hex characters (0-9a-f) or empty"),
    (b'{"reviewer": 5, "decisions": [{"id": 1, "decision": "approve"}]}', "reviewer: text, at most 80 characters"),
])
def test_bad_input_is_422_with_n8ns_message(client, body, error):
    r = client.post("/decisions", content=body, headers={**CONTROL_H, "content-type": "application/json"})
    assert r.status_code == 422
    assert r.json()["error"] == error


def test_at_most_50_decisions(client):
    many = [{"id": i, "decision": "skip"} for i in range(1, 52)]
    r = client.post("/decisions", json={"decisions": many}, headers=CONTROL_H)
    assert r.status_code == 422 and r.json()["error"] == "decisions: 1 to 50 entries"


def test_calendar_unreachable_is_502_and_changes_nothing(client):
    r = client.post("/decisions", json=ONE, headers=CONTROL_H)
    assert r.status_code == 502
    assert r.json()["ok"] is False and "nothing was changed" in r.json()["error"]
