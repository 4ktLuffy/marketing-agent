import json
import sqlite3

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app

KEY = "test-key"
AUTH = {"X-API-Key": KEY}
GATEWAY = "http://gateway.test"
RUN = f"{GATEWAY}/v1/run"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "learning.sqlite"))
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("GATEWAY_URL", GATEWAY)


client = TestClient(app)


def event(**kw):
    payload = {"item_id": 1, "channel": "linkedin", "decision": "approved", "draft": "Draft text", **kw}
    r = client.post("/events", json=payload, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def gw(rule="Never use exclamation marks.", scope="all", generalizable=True):
    return httpx.Response(200, json={
        "prompt": "reflect_rule", "model": "m", "attempts": 1, "duration_ms": 5,
        "output": {"generalizable": generalizable, "rule": rule, "scope": scope},
    })


def reflect(**body):
    r = client.post("/reflect", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def set_status(rule_id, status):
    return client.post(f"/rules/{rule_id}/status", json={"status": status}, headers=AUTH)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


# ---------- events


def test_create_event_shape_and_blank_fields_become_null():
    e = event(decision="edited", final="Final text", reason="  ", reviewer="", campaign_id=4)
    assert set(e) == {
        "id", "item_id", "channel", "campaign_id", "decision", "draft", "final", "reason",
        "reviewer", "created_at", "reflected_at",
    }
    assert e["final"] == "Final text" and e["campaign_id"] == 4
    assert e["reason"] is None and e["reviewer"] is None and e["reflected_at"] is None
    assert e["created_at"].endswith("Z")


@pytest.mark.parametrize("bad", [
    {"decision": "maybe"}, {"channel": " "}, {"draft": ""}, {"item_id": "abc"},
])
def test_create_event_validation(bad):
    payload = {"item_id": 1, "channel": "x", "decision": "approved", "draft": "d", **bad}
    assert client.post("/events", json=payload, headers=AUTH).status_code == 422


def test_list_events_filters():
    a = event(decision="approved")
    b = event(decision="rejected", reason="off-brand")
    ids = lambda r: [e["id"] for e in r.json()]
    assert ids(client.get("/events")) == [a["id"], b["id"]]
    assert ids(client.get("/events?decision=rejected")) == [b["id"]]
    assert ids(client.get("/events?since=2000-01-01")) == [a["id"], b["id"]]
    assert ids(client.get("/events?since=2999-01-01T00:00:00Z")) == []
    assert client.get("/events?decision=nope").status_code == 422
    assert client.get("/events?since=yesterday").status_code == 422


def test_attempts_counts_rejections_per_item():
    assert client.get("/items/7/attempts").json() == {"item_id": 7, "rejections": 0}
    event(item_id=7, decision="rejected")
    event(item_id=7, decision="rejected")
    event(item_id=7, decision="edited", final="f")
    event(item_id=8, decision="rejected")
    assert client.get("/items/7/attempts").json() == {"item_id": 7, "rejections": 2}
    assert client.get("/items/8/attempts").json() == {"item_id": 8, "rejections": 1}


# ---------- examples


def test_examples_edited_first_then_approved_most_recent_first():
    event(decision="approved", draft="approved old")
    event(decision="edited", draft="d", final="edited old")
    event(decision="approved", draft="draft ignored", final="approved new final")
    event(decision="edited", draft="d", final="edited new")
    event(decision="rejected", draft="rejected never shown")
    got = client.get("/examples?k=10").json()
    assert [x["text"] for x in got] == ["edited new", "edited old", "approved new final", "approved old"]
    assert got[0] == {"text": "edited new", "channel": "linkedin", "decision": "edited"}


def test_examples_default_k_and_limits():
    for i in range(5):
        event(decision="approved", draft=f"a{i}")
    assert len(client.get("/examples").json()) == 3
    assert len(client.get("/examples?k=10").json()) == 5
    assert client.get("/examples?k=11").status_code == 422
    assert client.get("/examples?k=0").status_code == 422


def test_examples_channel_filter_case_insensitive():
    event(channel="LinkedIn", draft="li")
    event(channel="x", draft="tweet")
    assert [x["text"] for x in client.get("/examples?channel=linkedin").json()] == ["li"]
    assert [x["text"] for x in client.get("/examples?channel=X").json()] == ["tweet"]
    assert client.get("/examples?channel=email").json() == []


def test_examples_skip_edited_without_final():
    event(decision="edited", draft="no final given")
    assert client.get("/examples").json() == []


# ---------- reflect


@respx.mock
def test_reflect_creates_pending_rules_and_marks_events_once():
    route = respx.post(RUN).mock(side_effect=[
        gw("Never use exclamation marks."),
        gw("Keep LinkedIn posts under 120 words.", scope="LinkedIn"),
    ])
    event(decision="approved")  # not reflected
    e1 = event(decision="edited", final="Final!", reason="")
    e2 = event(decision="rejected", reason="too long")
    out = reflect()
    assert out["reflected"] == 2 and out["errors"] == []
    assert [(r["text"], r["scope"], r["status"], r["source_event_ids"]) for r in out["created"]] == [
        ("Never use exclamation marks.", "all", "pending", [e1["id"]]),
        ("Keep LinkedIn posts under 120 words.", "linkedin", "pending", [e2["id"]]),
    ]
    sent = [json.loads(c.request.content) for c in route.calls]
    assert sent[0] == {"prompt": "reflect_rule", "vars": {
        "draft": "Draft text", "final": "Final!", "reason": None, "channel": "linkedin"}}
    assert sent[1]["vars"]["final"] is None and sent[1]["vars"]["reason"] == "too long"
    assert all(e["reflected_at"] for e in client.get("/events?decision=edited").json())
    # second run: nothing left to reflect, gateway not called again
    assert reflect() == {"created": [], "reflected": 0, "errors": []}
    assert route.call_count == 2


@respx.mock
def test_reflect_gateway_error_skips_event_and_reports_it():
    respx.post(RUN).mock(side_effect=[
        httpx.Response(502, json={"detail": "no valid output"}),
        httpx.ConnectError("refused"),
        gw("Lead with the customer benefit."),
    ])
    bad1 = event(decision="edited", final="f")
    bad2 = event(decision="rejected")
    event(decision="rejected")
    out = reflect()
    assert out["reflected"] == 1
    assert [e["event_id"] for e in out["errors"]] == [bad1["id"], bad2["id"]]
    assert "502" in out["errors"][0]["error"] and "unreachable" in out["errors"][1]["error"]
    assert [r["text"] for r in out["created"]] == ["Lead with the customer benefit."]
    pending = [e["id"] for e in client.get("/events").json() if e["reflected_at"] is None]
    assert pending == [bad1["id"], bad2["id"]]  # retried on the next run


@respx.mock
def test_reflect_malformed_output_is_an_error():
    respx.post(RUN).mock(return_value=httpx.Response(200, json={"output": "just text"}))
    e = event(decision="rejected")
    out = reflect()
    assert out["reflected"] == 0 and out["errors"][0]["event_id"] == e["id"]


@respx.mock
def test_reflect_not_generalizable_or_empty_rule_marks_reflected_without_rule():
    respx.post(RUN).mock(side_effect=[gw(generalizable=False), gw(rule="   ")])
    event(decision="rejected")
    event(decision="rejected")
    out = reflect()
    assert out == {"created": [], "reflected": 2, "errors": []}
    assert client.get("/rules").json() == []


@respx.mock
def test_reflect_truncates_rule_to_200_chars_and_collapses_whitespace():
    respx.post(RUN).mock(return_value=gw("Use  short\n sentences " + "x" * 300))
    event(decision="rejected")
    text = reflect()["created"][0]["text"]
    assert len(text) == 200 and text.startswith("Use short sentences x")


@respx.mock
def test_duplicate_rule_is_not_reproposed_but_source_is_recorded():
    respx.post(RUN).mock(side_effect=[
        gw("Never use exclamation marks."), gw("  never use   EXCLAMATION marks. "),
    ])
    e1 = event(decision="rejected")
    e2 = event(decision="rejected")
    out = reflect()
    assert len(out["created"]) == 1 and out["reflected"] == 2
    rules = client.get("/rules").json()
    assert len(rules) == 1 and rules[0]["source_event_ids"] == [e1["id"], e2["id"]]


@respx.mock
@pytest.mark.parametrize("status", ["active", "rejected"])
def test_rule_already_active_or_rejected_is_not_reproposed(status):
    respx.post(RUN).mock(side_effect=[gw("No emojis."), gw("no EMOJIS.")])
    event(decision="rejected")
    rule = reflect()["created"][0]
    assert set_status(rule["id"], status).status_code == 200
    event(decision="edited", final="f")
    out = reflect()
    assert out == {"created": [], "reflected": 1, "errors": []}
    rules = client.get("/rules").json()
    assert len(rules) == 1 and rules[0]["status"] == status
    assert rules[0]["source_event_ids"] == [rule["source_event_ids"][0]]


@respx.mock
def test_reflect_respects_max_events_and_since_days(tmp_path):
    respx.post(RUN).mock(return_value=gw(generalizable=False))
    old = event(decision="rejected")
    with sqlite3.connect(tmp_path / "learning.sqlite") as conn:
        conn.execute("UPDATE events SET created_at = '2000-01-01T00:00:00Z' WHERE id = ?", (old["id"],))
    for _ in range(3):
        event(decision="rejected")
    assert reflect(max_events=2)["reflected"] == 2
    assert reflect(since_days=7)["reflected"] == 1  # the old one is out of range
    assert client.post("/reflect", json={"max_events": 0}, headers=AUTH).status_code == 422


@respx.mock
def test_reflect_without_body_uses_defaults():
    respx.post(RUN).mock(return_value=gw())
    event(decision="rejected")
    r = client.post("/reflect", headers=AUTH)
    assert r.status_code == 200 and r.json()["reflected"] == 1


# ---------- rules


@respx.mock
def test_rule_status_changes_and_listing_filter():
    respx.post(RUN).mock(side_effect=[gw("A rule."), gw("B rule.")])
    event(decision="rejected")
    event(decision="rejected")
    a, b = reflect()["created"]
    r = set_status(a["id"], "active")
    assert r.status_code == 200 and r.json()["status"] == "active"
    ids = lambda s: [x["id"] for x in client.get(f"/rules?status={s}").json()]
    assert ids("active") == [a["id"]] and ids("pending") == [b["id"]] and ids("rejected") == []
    assert client.get("/rules?status=bogus").status_code == 422
    assert set_status(a["id"], "pending").status_code == 422
    assert set_status(999, "active").status_code == 404


def test_summary_empty():
    assert client.get("/rules/summary").json() == {"summary": "", "count": 0}


@respx.mock
def test_summary_lists_active_rules_with_channel_prefix():
    respx.post(RUN).mock(side_effect=[
        gw("No exclamation marks."), gw("Max 3 hashtags.", scope="linkedin"), gw("Pending rule."),
    ])
    for _ in range(3):
        event(decision="rejected")
    a, b, _ = reflect()["created"]
    set_status(a["id"], "active")
    set_status(b["id"], "active")
    assert client.get("/rules/summary").json() == {
        "summary": "Rules learned from your edits:\n- No exclamation marks.\n- [linkedin] Max 3 hashtags.",
        "count": 2,
    }


# ---------- auth


WRITES = [
    ("/events", {"item_id": 1, "channel": "x", "decision": "approved", "draft": "d"}),
    ("/reflect", {}),
    ("/rules/1/status", {"status": "active"}),
]


@pytest.mark.parametrize("path,body", WRITES)
def test_writes_need_api_key(path, body):
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers={"X-API-Key": "wrong"}).status_code == 401


@pytest.mark.parametrize("path,body", WRITES)
def test_writes_503_when_key_not_configured(path, body, monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post(path, json=body, headers=AUTH).status_code == 503


def test_reads_need_no_key():
    for path in ("/events", "/examples", "/rules", "/rules/summary", "/items/1/attempts"):
        assert client.get(path).status_code == 200, path
