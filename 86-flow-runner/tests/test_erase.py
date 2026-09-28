"""Erasure on request (GDPR art. 17): every row about a person goes; an unsubscribe is kept only
as a salted hash, so an erased person who unsubscribed is still never mailed again."""
import sqlite3

import pytest

from .conftest import APPROVE, AUTH, approved_flow, event, outbox, tick


@pytest.fixture(autouse=True)
def all_in_flow_arm(monkeypatch):
    monkeypatch.setenv("FLOW_HOLDOUT_PCT", "0")


def rows_with(email: str, db: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return sum(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE email = ?", (email,)).fetchone()[0]
                   for t in ("contacts", "events", "enrollments", "outbox"))
    finally:
        conn.close()


def test_erase_removes_every_row(client, clock, monkeypatch, tmp_path):
    approved_flow(client)
    event(client, "subscribed", "Ana@Example.com", consent=True)
    tick(client)
    db = str(tmp_path / "flows.sqlite")
    assert rows_with("ana@example.com", db) > 0
    r = client.delete("/contacts/ana@example.com", headers=APPROVE)
    assert r.status_code == 200, r.text
    assert r.json()["erased"] is True and r.json()["kept_unsubscribe"] is False
    assert rows_with("ana@example.com", db) == 0
    assert all("ana" not in str(o) for o in outbox(client))
    assert client.delete("/contacts/ana@example.com", headers=APPROVE).json()["erased"] is False


def test_erased_unsubscriber_is_never_mailed_again(client, clock, tmp_path):
    approved_flow(client)
    event(client, "subscribed", "bo@example.com", consent=True)
    event(client, "unsubscribed", "bo@example.com")
    r = client.delete("/contacts/BO@example.com", headers=APPROVE).json()
    assert r["kept_unsubscribe"] is True
    assert rows_with("bo@example.com", str(tmp_path / "flows.sqlite")) == 0
    conn = sqlite3.connect(str(tmp_path / "flows.sqlite"))
    assert "bo@example.com" not in str(conn.execute("SELECT * FROM erased_unsubscribes").fetchall())
    conn.close()
    r = event(client, "subscribed", "bo@example.com", consent=True)   # the same address comes back
    assert r["entered"] == [] and r["skipped"][0]["reason"] == "unsubscribed"
    clock.advance(days=10)
    tick(client)
    assert outbox(client) == []


def test_erase_needs_both_keys(client):
    assert client.delete("/contacts/a@example.com").status_code == 401
    assert client.delete("/contacts/a@example.com", headers=AUTH).status_code == 403
