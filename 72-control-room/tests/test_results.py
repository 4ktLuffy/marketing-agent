"""Results page (/tasks/results): time to a usable draft, pastes, blocked on first paste and why,
approval turnaround, reviewer edits, exports, facts changed after approval, by AI (with counts and
"too few to compare"), cost. Computed from 88's real task shape (GET /tasks, GET /tasks/{id}) and
19's audit and versions, all mocked with respx."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import results as R

from .conftest import CAL, KEYS, PASSWORD, URLS, csrf_of, login

TASKS = "http://tasks.internal:8000"
GW = URLS["GATEWAY_URL"]
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ts(base: datetime, **delta) -> str:
    return (base + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ev(at, kind, **detail):
    return {"at": at, "kind": kind, "detail": detail}


A0 = NOW - timedelta(days=2)
B0 = NOW - timedelta(days=40)
# Task A: first paste (ChatGPT) split by hand, one piece blocked; second paste (Claude) passes;
# both approved; a fact changed after approval; exported.
TASK_A = {
    "id": "T-AAAA11", "goal": "Fill midweek rooms", "created_at": ts(A0), "status": "exported",
    "pieces": [{"key": "p1", "channel": "linkedin", "kind": "post", "max_chars": None},
               {"key": "p2", "channel": "instagram", "kind": "caption", "max_chars": 300}],
    "drafts": [{"draft_id": 1, "kind": "paste", "provider": "chatgpt", "parent_id": None, "created_at": ts(A0, minutes=10)},
               {"draft_id": 2, "kind": "manual", "provider": None, "parent_id": 1, "created_at": ts(A0, minutes=12)},
               {"draft_id": 3, "kind": "paste", "provider": "claude", "parent_id": None, "created_at": ts(A0, hours=2)}],
    "events": [ev(ts(A0), "created", chars=2000),
               ev(ts(A0, minutes=10), "pasted", draft_id=1, provider="chatgpt"),
               ev(ts(A0, minutes=15), "submitted", piece_key="p1", draft_id=2, blocked=True, labels=["match", "wrong_scope"]),
               ev(ts(A0, minutes=15), "submitted", piece_key="p2", draft_id=2, blocked=False, labels=["match"]),
               ev(ts(A0, hours=3), "submitted", piece_key="p1", draft_id=3, blocked=False, labels=["match"]),
               ev(ts(A0, hours=3), "submitted", piece_key="p2", draft_id=3, blocked=False, labels=["review"]),
               ev(ts(A0, hours=20), "fact_changed", piece_key="p1", item_id=11, from_status="approved", to_state="draft",
                  facts=["weekday-rate"]),
               ev(ts(A0, hours=30), "exported", export_id=1, format="md")],
    "exports": [{"id": 1, "format": "md", "sha256": "0" * 64, "created_at": ts(A0, hours=30)}],
    "piece_state": [
        {"piece_key": "p1", "n": 1, "channel": "linkedin", "state": "in_review", "draft_id": 3, "blocked": False,
         "findings": [], "checks": {"brand": [], "platform": []}, "calendar_item_id": 11},
        {"piece_key": "p2", "n": 2, "channel": "instagram", "state": "in_review", "draft_id": 3, "blocked": False,
         "findings": [], "checks": {"brand": [], "platform": []}, "calendar_item_id": 12}],
}
# Task B (40 days ago): one Gemini paste, blocked by a missing disclosure and a brand rule, never approved.
TASK_B = {
    "id": "T-BBBB22", "goal": "Winter newsletter", "created_at": ts(B0), "status": "submitted",
    "pieces": [{"key": "p1", "channel": "email", "kind": "email", "max_chars": None}],
    "drafts": [{"draft_id": 5, "kind": "paste", "provider": "gemini", "parent_id": None, "created_at": ts(B0, minutes=5)}],
    "events": [ev(ts(B0), "created"),
               ev(ts(B0, minutes=6), "submitted", piece_key="p1", draft_id=5, blocked=True,
                  labels=["match", "missing_disclosure"])],
    "exports": [],
    "piece_state": [{"piece_key": "p1", "n": 1, "channel": "email", "state": "draft", "draft_id": 5, "blocked": True,
                     "findings": [{"sentence": None, "label": "missing_disclosure", "blocking": True},
                                  {"sentence": "£180 a night.", "label": "match", "blocking": False}],
                     "checks": {"brand": [{"rule": "emoji_not_allowed", "severity": "error", "detail": "emoji"}],
                                "platform": [{"rule": "too_long", "severity": "warn", "detail": "long"}]},
                     "calendar_item_id": 21}],
}


def audit(*rows):
    return [{"at": at, "actor": "x", "action": act, "from_status": fr, "to_status": to, "body_sha256": "a" * 64,
             "detail": None} for at, act, fr, to in rows]


ITEMS = {
    11: {"audit": audit((ts(A0, hours=3), "created", None, "in_review"), (ts(A0, hours=4), "edit", "in_review", "in_review"),
                        (ts(A0, hours=5), "status", "in_review", "approved"),
                        (ts(A0, hours=20), "status", "approved", "draft")),
         "versions": [{"n": 1, "created_at": ts(A0, hours=3)}, {"n": 2, "created_at": ts(A0, hours=4)}]},
    12: {"audit": audit((ts(A0, hours=3), "created", None, "in_review"),
                        (ts(A0, hours=3, minutes=30), "status", "in_review", "approved")),
         "versions": [{"n": 1, "created_at": ts(A0, hours=3)}]},
    21: {"audit": audit((ts(B0, minutes=6), "created", None, "draft")),
         "versions": [{"n": 1, "created_at": ts(B0, minutes=6)}]},
}


# ------------------------------------------------------------------ computation

def test_task_a_numbers():
    a = R.analyse_task(TASK_A, ITEMS)
    assert a["pastes"] == 2                             # the hand split is not a paste
    assert a["to_usable"] == 3 * 3600                   # first submit with nothing blocked
    p1, p2 = a["pieces"]
    assert p1["provider"] == "chatgpt"                  # hand split inherits its paste's AI
    assert p1["blocked_first"] and p1["labels"] == ["wrong_scope"] and not p1["rules_known"]
    assert not p2["blocked_first"] and p2["labels"] == []
    assert p1["approved"] and p1["turnaround"] == 7200 and p1["edits"] == 1
    assert p2["approved"] and p2["turnaround"] == 1800 and p2["edits"] == 0
    assert a["exported"] and a["pieces_exported"] == 2 and a["facts_changed_after_approval"] == 1


def test_task_b_reasons_from_current_findings_and_rules():
    b = R.analyse_task(TASK_B, ITEMS)
    p = b["pieces"][0]
    assert b["to_usable"] is None and b["pastes"] == 1
    assert p["provider"] == "gemini" and p["blocked_first"]
    assert p["labels"] == ["missing_disclosure"]        # blocking findings only (not "match")
    assert p["rules"] == ["Brand rule: emoji not allowed"] and p["rules_known"]   # warn is not a reason
    assert not p["approved"] and p["turnaround"] is None and p["edits"] == 0
    assert not b["exported"]


def test_unreadable_item_is_not_counted_as_unapproved():
    a = R.analyse_task(TASK_A, {})
    assert all(not p["known"] and p["edits"] is None for p in a["pieces"])
    o = R.summarise([a], NOW, None)["overall"]
    assert o["approved"]["n"] == 0 and o["not_readable"] == 2


def test_summary_window_and_overall():
    an = [R.analyse_task(t, ITEMS) for t in (TASK_A, TASK_B)]
    last30 = R.summarise(an, NOW, 30)
    o = last30["overall"]
    assert o["tasks"] == 1 and [t["id"] for t in last30["tasks"]] == ["T-AAAA11"]
    assert o["blocked_first"]["text"] == "1 of 2 (50%)"
    assert o["turnaround_median"] == 4500 and o["turnaround_n"] == 2
    assert o["edits_total"] == 1 and o["edited_pieces"]["text"] == "1 of 2 (50%)"
    assert o["pieces_exported"] == 2 and o["facts_changed"] == 1
    everything = R.summarise(an, NOW, None)["overall"]
    assert everything["tasks"] == 2 and everything["blocked_first"]["text"] == "2 of 3 (67%)"
    assert dict(everything["reasons_label"]) == {"wrong scope": 1, "missing disclosure": 1}
    assert dict(everything["reasons_rule"]) == {"Brand rule: emoji not allowed": 1}
    assert everything["rules_unknown"] == 1
    assert everything["usable"]["text"] == "1 of 2 (50%)"


def _pieces(provider, n, blocked):
    return [{"submitted": True, "provider": provider, "blocked_first": i < blocked, "known": True,
             "approved": i >= blocked, "edits": i % 3, "turnaround": 60} for i in range(n)]


def test_by_ai_small_samples_are_not_ranked():
    rows = R.by_ai(_pieces("chatgpt", 9, 1) + _pieces("claude", 9, 5))
    assert [r["enough"] for r in rows["rows"]] == [False, False]
    assert rows["fewest_blocked"] is None and rows["comparable"] == 0
    # one AI with enough is still not a comparison
    assert R.by_ai(_pieces("chatgpt", 12, 1) + _pieces("claude", 9, 0))["fewest_blocked"] is None


def test_by_ai_ranks_only_with_ten_or_more_each():
    rows = R.by_ai(_pieces("chatgpt", 10, 5) + _pieces("claude", 12, 2) + _pieces("gemini", 3, 0))
    assert rows["fewest_blocked"] == "Claude"           # gemini (0 of 3) is too few to win
    by = {r["name"]: r for r in rows["rows"]}
    assert by["Claude"]["blocked_first"]["text"] == "2 of 12 (17%)" and by["Gemini"]["enough"] is False
    assert by["ChatGPT"]["approved"]["text"] == "5 of 10 (50%)"
    tie = R.by_ai(_pieces("chatgpt", 10, 2) + _pieces("claude", 10, 2))
    assert tie["fewest_blocked"] is None


def test_provider_names_and_durations():
    assert R.provider_name("claude") == "Claude" and R.provider_name(None) == "Not recorded"
    assert R.provider_name("self") == "Written by hand" and R.provider_name("weird") == "Not recorded"
    assert R.duration(30) == "under a minute" and R.duration(600) == "10 min"
    assert R.duration(3 * 3600 + 120) == "3 h 2 min" and R.duration(2 * 86400 + 3600) == "2 days 1 h"
    assert R.share(0, 0)["text"] == "none yet"


def test_ai_steps_counts_only_this_flows_callers():
    rows = [{"caller": "44 claim checker", "ok": True, "duration_ms": 1500, "provider": "ollama", "finished_at": ts(NOW)},
            {"caller": "88 task bridge", "ok": False, "duration_ms": 500, "provider": "api.groq.com", "finished_at": ts(NOW)},
            {"caller": "26 Social post writer", "ok": True, "duration_ms": 9000, "provider": "ollama", "finished_at": ts(NOW)},
            {"caller": "440 something", "ok": True, "duration_ms": 1, "provider": "ollama", "finished_at": ts(NOW)},
            {"caller": "72 onboarding", "ok": True, "duration_ms": 1000, "provider": "ollama",
             "finished_at": ts(NOW, days=-45)}]
    out = R.ai_steps(rows, NOW - timedelta(days=30))
    by = {s["name"]: s for s in out["steps"]}
    assert set(by) == {"Claim checker (also used by other workflows)", "Model check of pasted drafts (task bridge)"}
    assert by["Model check of pasted drafts (task bridge)"]["hosted"] == 1 and by["Model check of pasted drafts (task bridge)"]["failed"] == 1
    assert R.ai_steps(rows, None)["steps"][-1]["name"].startswith("Setting up facts")


# ------------------------------------------------------------------ the page

@pytest.fixture
def page(monkeypatch, mock):
    monkeypatch.setenv("TASKS_URL", TASKS)
    from app.main import create_app
    mock.get(f"{GW}/v1/activity").respond(json={"running": [], "recent": [
        {"id": 1, "caller": "44 claim checker", "ok": True, "duration_ms": 2000, "provider": "ollama",
         "model": "qwen", "prompt": "claims", "finished_at": ts(NOW, hours=-1)}], "models": []})
    with TestClient(create_app(), follow_redirects=False) as c:
        assert login(c).status_code == 303
        yield c, mock


def mock_88_19(mock, tasks=(TASK_A, TASK_B)):
    listed = [{"id": t["id"], "goal": t["goal"], "created_at": t["created_at"]} for t in tasks]
    lst = mock.get(f"{TASKS}/tasks", params={"limit": "200"}).respond(json=listed)
    for t in tasks:
        mock.get(f"{TASKS}/tasks/{t['id']}").respond(json=t)
    for i, it in ITEMS.items():
        mock.get(f"{CAL}/items/{i}/audit").respond(json=it["audit"])
        mock.get(f"{CAL}/items/{i}/versions").respond(json=it["versions"])
    return lst


def test_page_shows_numbers_with_counts(page):
    c, mock = page
    lst = mock_88_19(mock)
    r = c.get("/tasks/results")
    assert r.status_code == 200, r.text
    h = r.text
    assert "typically 3 h" in h                        # task A: created -> first usable submit
    assert "1 of 2 (50%)" in h                          # blocked on first paste, 30 days
    assert "typically 1 h 15 min" in h                 # approval turnaround median (2 h + 30 min) / 2
    assert "T-AAAA11" in h and "T-BBBB22" not in h      # B is older than 30 days
    assert "Too few to compare (under 10 pieces)" in h
    assert "Not enough pieces yet to say which AI works best" in h
    assert "Model calls: 0" in h and "Claim checker (also used by other workflows): 1 model call" in h
    assert "Your own time is not measured automatically" in h
    everything = c.get("/tasks/results?range=all").text
    assert "T-BBBB22" in everything and "missing disclosure: 1" in everything
    assert "Brand rule: emoji not allowed: 1" in everything and "Gemini" in everything
    assert lst.call_count == 1                          # cached for 60 s across both periods
    for call in mock.calls:
        if TASKS in str(call.request.url) or CAL in str(call.request.url):
            assert call.request.headers.get("x-api-key") == KEYS["INTERNAL_API_KEY"]
            assert call.request.method == "GET"


def test_no_secret_or_internal_url_in_html(page):
    c, mock = page
    mock_88_19(mock)
    h = c.get("/tasks/results").text + c.get("/tasks/results?range=all").text
    for secret in [*KEYS.values(), PASSWORD, TASKS, "tasks.internal", "calendar.internal", "gateway.internal",
                   "X-API-Key", "X-Owner-Key"]:
        assert secret not in h, secret


def test_empty_state(page):
    c, mock = page
    mock.get(f"{TASKS}/tasks", params={"limit": "200"}).respond(json=[])
    h = c.get("/tasks/results").text
    assert "No tasks in the last 30 days" in h and 'href="/tasks/new"' in h
    assert "No tasks yet" in c.get("/tasks/results?range=all").text


def test_not_installed_makes_no_calls(authed, mock):
    c, _ = authed
    r = c.get("/tasks/results")
    assert r.status_code == 200 and "task bridge (88) isn't installed" in r.text
    assert not mock.calls


def test_88_down_and_19_partly_down(page):
    c, mock = page
    mock.get(f"{TASKS}/tasks", params={"limit": "200"}).respond(503, json={"detail": "busy"})
    r = c.get("/tasks/results")
    assert r.status_code == 200 and "The task bridge (88) did not answer" in r.text


def test_unreadable_calendar_item_is_said_not_hidden(page):
    c, mock = page
    mock.get(f"{TASKS}/tasks", params={"limit": "200"}).respond(json=[{"id": "T-AAAA11"}])
    mock.get(f"{TASKS}/tasks/T-AAAA11").respond(json=TASK_A)
    mock.get(f"{CAL}/items/11/audit").respond(404, json={"detail": "no item"})
    mock.get(f"{CAL}/items/11/versions").respond(404, json={"detail": "no item"})
    mock.get(f"{CAL}/items/12/audit").respond(json=ITEMS[12]["audit"])
    mock.get(f"{CAL}/items/12/versions").respond(json=ITEMS[12]["versions"])
    h = c.get("/tasks/results").text
    assert "1 calendar item(s) could not be read" in h and "1 of 1 (100%)" in h


def test_gateway_down_says_not_measured(monkeypatch, page):
    c, mock = page
    mock_88_19(mock)
    mock.get(f"{GW}/v1/activity").respond(503)
    h = c.get("/tasks/results").text
    assert "Not measured for: main gateway" in h and "Model calls: 0" in h


def test_no_gateway_configured(monkeypatch, mock):
    monkeypatch.setenv("TASKS_URL", TASKS)
    monkeypatch.setenv("GATEWAY_URL", "")
    from app.main import create_app
    mock_88_19(mock)
    with TestClient(create_app(), follow_redirects=False) as c:
        assert login(c).status_code == 303
        h = c.get("/tasks/results").text
    assert "not measured, no gateway is set up" in h


def test_login_required_and_csp(page, client):
    r = client.get("/tasks/results")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    c, mock = page
    mock_88_19(mock)
    r = c.get("/tasks/results")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", r.text)
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", r.text, re.I)


def test_linked_from_tasks_and_more(page):
    c, mock = page
    mock.get(f"{TASKS}/tasks").respond(json=[])
    assert 'href="/tasks/results"' in c.get("/tasks").text
    assert 'href="/tasks/results"' in c.get("/more").text


def test_results_template_has_no_script():
    text = (Path(__file__).parent.parent / "app" / "templates" / "results.html").read_text()
    assert "<script" not in text and csrf_of('name="csrf" value="x"') == "x"
