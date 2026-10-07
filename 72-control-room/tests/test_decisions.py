import json

import httpx

from .conftest import CAL, KEYS, WEBHOOK, flush, item

HX = {"HX-Request": "true"}


def n8n_ok(summary, **kw):
    return httpx.Response(200, json={"ok": True, "summary": summary, "not_in_review": [], "failed": [],
                                     "message": "\n".join(summary), **kw})


def test_approve_waits_for_undo_window_then_posts_once(authed, app, mock):
    c, token = authed
    hook = mock.post(WEBHOOK).mock(return_value=n8n_ok(["#7: approved for 2026-10-01 09:00 UTC"]))
    r = c.post("/decide", data={"id": 7, "decision": "approve"}, headers={**HX, "X-CSRF-Token": token})
    assert r.status_code == 200
    assert 'hx-post="/undo/' in r.text and "#7: approve" in r.text and 'hx-swap-oob="afterbegin:#toasts"' in r.text
    assert not hook.called                           # nothing sent inside the Undo window
    flush(c, app, later=-1)                          # not due yet
    assert not hook.called
    flush(c, app)
    assert hook.call_count == 1
    req = hook.calls.last.request
    assert json.loads(req.content) == {"reviewer": "alex", "decisions": [{"id": 7, "decision": "approve"}]}
    assert req.headers["X-Control-Key"] == KEYS["CONTROL_ROOM_KEY"]
    assert "X-API-Key" not in req.headers and "X-Approver-Key" not in req.headers
    flush(c, app)
    assert hook.call_count == 1                      # sent once
    assert "#7: approved for 2026-10-01 09:00 UTC" in c.get("/results").text


def test_undo_within_window_sends_nothing_and_returns_the_card(authed, app, mock):
    c, token = authed
    hook = mock.post(WEBHOOK).mock(return_value=n8n_ok([]))
    mock.get(f"{CAL}/items/7").respond(json=item(7, title="Autumn post"))
    r = c.post("/decide", data={"id": 7, "decision": "reject_drop"}, headers={**HX, "X-CSRF-Token": token})
    tok = r.text.split('hx-post="/undo/')[1].split('"')[0]
    u = c.post(f"/undo/{tok}", headers={**HX, "X-CSRF-Token": token})
    assert u.status_code == 200 and 'id="card-7"' in u.text and "Autumn post" in u.text
    assert f'id="toast-{tok}" hx-swap-oob="delete"' in u.text
    flush(c, app)
    assert not hook.called
    assert c.post(f"/undo/{tok}", headers={**HX, "X-CSRF-Token": token}).status_code == 404


def test_undo_after_sending_is_refused(authed, app, mock):
    c, token = authed
    mock.post(WEBHOOK).mock(return_value=n8n_ok(["#7: back to draft"]))
    r = c.post("/decide", data={"id": 7, "decision": "back_to_draft"}, headers={**HX, "X-CSRF-Token": token})
    tok = r.text.split('hx-post="/undo/')[1].split('"')[0]
    flush(c, app)
    assert c.post(f"/undo/{tok}", headers={**HX, "X-CSRF-Token": token}).status_code == 404


def test_undo_while_sending_is_409(authed, app):
    c, token = authed
    d = app.state.pending.add(9, "approve")
    d.state = "sending"
    r = c.post(f"/undo/{d.token}", headers={**HX, "X-CSRF-Token": token})
    assert r.status_code == 409 and "already sent" in r.text


def test_edit_reject_and_publish_time_are_passed_as_given(authed, app, mock):
    c, token = authed
    hook = mock.post(WEBHOOK).mock(return_value=n8n_ok(["ok"]))
    h = {**HX, "X-CSRF-Token": token}
    c.post("/decide", data={"id": 1, "decision": "edit", "text": "New words", "reason": "shorter",
                            "publish_at": "2026-10-03T08:30"}, headers=h)
    c.post("/decide", data={"id": 2, "decision": "reject_rewrite", "reason": "too salesy", "text": "ignored"}, headers=h)
    c.post("/decide", data={"id": 3, "decision": "approve", "text": "ignored for approve"}, headers=h)
    flush(c, app)
    sent = json.loads(hook.calls.last.request.content)["decisions"]
    assert sent == [
        {"id": 1, "decision": "edit", "text": "New words", "reason": "shorter", "publish_at": "2026-10-03T08:30"},
        {"id": 2, "decision": "reject_rewrite", "reason": "too salesy"},
        {"id": 3, "decision": "approve"},
    ]


def test_second_decision_on_same_item_replaces_the_first(authed, app, mock):
    c, token = authed
    hook = mock.post(WEBHOOK).mock(return_value=n8n_ok(["ok"]))
    h = {**HX, "X-CSRF-Token": token}
    c.post("/decide", data={"id": 4, "decision": "approve"}, headers=h)
    c.post("/decide", data={"id": 4, "decision": "reject_drop"}, headers=h)
    flush(c, app)
    assert json.loads(hook.calls.last.request.content)["decisions"] == [{"id": 4, "decision": "reject_drop"}]


def test_edit_form_without_htmx_redirects_to_queue_with_toast(authed, app, mock):
    c, token = authed
    r = c.post("/decide", data={"id": 5, "decision": "edit", "text": "x", "csrf": token})
    assert r.status_code == 303 and r.headers["location"].startswith("/?toast=")
    mock.get(f"{CAL}/items").respond(json=[item(5), item(6)])
    page = c.get(r.headers["location"])
    assert 'id="initial-toast"' in page.text and "#5: edit &amp; approve" in page.text
    assert 'id="card-5"' not in page.text and 'id="card-6"' in page.text   # pending: hidden


def test_bad_decisions_are_422(authed):
    c, token = authed
    h = {"X-CSRF-Token": token}
    assert c.post("/decide", data={"id": 1, "decision": "publish"}, headers=h).status_code == 422
    assert c.post("/decide", data={"id": 1, "decision": "skip"}, headers=h).status_code == 422
    assert c.post("/decide", data={"id": 0, "decision": "approve"}, headers=h).status_code == 422
    assert c.post("/decide", data={"id": 1, "decision": "approve", "publish_at": "tomorrow"}, headers=h).status_code == 422
    assert c.post("/decide", data={"id": 1, "decision": "approve", "reason": "x" * 501}, headers=h).status_code == 422


def test_n8n_down_shows_not_saved_and_item_returns(authed, app, mock):
    c, token = authed
    mock.post(WEBHOOK).mock(side_effect=httpx.ConnectError("boom"))
    c.post("/decide", data={"id": 8, "decision": "approve"}, headers={**HX, "X-CSRF-Token": token})
    flush(c, app)
    res = c.get("/results").text
    assert "Not saved" in res and "n8n.internal" not in res
    mock.get(f"{CAL}/items").respond(json=[item(8)])
    assert 'id="card-8"' in c.get("/").text


def test_n8n_refusal_and_stale_items_are_reported(authed, app, mock):
    c, token = authed
    mock.post(WEBHOOK).mock(return_value=httpx.Response(200, json={
        "ok": True, "summary": [], "not_in_review": [12], "failed": [], "message": "No decisions made."}))
    c.post("/decide", data={"id": 12, "decision": "approve"}, headers={**HX, "X-CSRF-Token": token})
    flush(c, app)
    assert "Not in review any more, nothing changed: #12" in c.get("/results").text
    mock.post(WEBHOOK).mock(return_value=httpx.Response(401, json={"ok": False, "error": "unauthorized"}))
    c.post("/decide", data={"id": 13, "decision": "approve"}, headers={**HX, "X-CSRF-Token": token})
    flush(c, app)
    assert "HTTP 401" in c.get("/results").text


def test_failed_calendar_updates_are_shown(authed, app, mock):
    c, token = authed
    mock.post(WEBHOOK).mock(return_value=httpx.Response(200, json={
        "ok": False, "summary": ["#3: approved for 2026-10-01 09:00 UTC"], "not_in_review": [],
        "failed": [{"status": 409, "detail": {"message": "cannot move from draft to approved"}}], "message": ""}))
    c.post("/decide", data={"id": 3, "decision": "approve"}, headers={**HX, "X-CSRF-Token": token})
    flush(c, app)
    assert "cannot move from draft to approved" in c.get("/results").text


def test_pending_decisions_are_sent_on_shutdown(app, mock):
    from fastapi.testclient import TestClient

    from .conftest import csrf_of, login
    hook = mock.post(WEBHOOK).mock(return_value=n8n_ok(["#2: approved"]))
    with TestClient(app, follow_redirects=False) as c:
        login(c)
        token = csrf_of(c.get("/more").text)
        c.post("/decide", data={"id": 2, "decision": "approve"}, headers={**HX, "X-CSRF-Token": token})
        assert not hook.called
    assert hook.call_count == 1


# ---------- version-bound approval: the hash of the text on the card travels to 19

SEEN = "ab" * 32


def test_card_and_edit_page_carry_the_body_hash(authed, mock):
    c, _ = authed
    mock.get(f"{CAL}/items").respond(json=[item(7, body_sha256=SEEN), item(8)])   # 8: an older calendar
    page = c.get("/").text
    assert f'"seen": "{SEEN}"' in page and f'name="seen" value="{SEEN}"' in page
    assert 'name="seen" value=""' in page
    mock.get(f"{CAL}/items/7").respond(json=item(7, body_sha256=SEEN))
    mock.get("http://rules.internal:8000/rules").respond(json={"channels": {}})
    assert f'name="seen" value="{SEEN}"' in c.get("/items/7/edit").text


def test_card_ignores_a_malformed_body_hash(authed, mock):
    c, _ = authed
    mock.get(f"{CAL}/items").respond(json=[item(7, body_sha256='x" onmouseover="alert(1)')])
    page = c.get("/").text
    assert "onmouseover" not in page and 'name="seen" value=""' in page


def test_seen_hash_is_forwarded_with_the_decision(authed, app, mock):
    c, token = authed
    hook = mock.post(WEBHOOK).mock(return_value=n8n_ok(["ok"]))
    h = {**HX, "X-CSRF-Token": token}
    c.post("/decide", data={"id": 1, "decision": "approve", "seen": SEEN}, headers=h)
    c.post("/decide", data={"id": 2, "decision": "edit", "text": "New", "seen": SEEN}, headers=h)
    c.post("/decide", data={"id": 3, "decision": "approve", "seen": ""}, headers=h)
    flush(c, app)
    assert json.loads(hook.calls.last.request.content)["decisions"] == [
        {"id": 1, "decision": "approve", "seen_sha256": SEEN},
        {"id": 2, "decision": "edit", "text": "New", "seen_sha256": SEEN},
        {"id": 3, "decision": "approve"},
    ]


def test_malformed_seen_hash_is_422(authed, app, mock):
    c, token = authed
    h = {"X-CSRF-Token": token}
    for bad in ["abc", "AB" * 32, "g" * 64, SEEN + "0"]:
        assert c.post("/decide", data={"id": 1, "decision": "approve", "seen": bad}, headers=h).status_code == 422
    assert not app.state.pending.items


def test_changed_since_you_looked_is_one_clear_line(authed, app, mock):
    c, token = authed
    mock.post(WEBHOOK).mock(return_value=httpx.Response(200, json={
        "ok": False, "summary": ["#4: approved for 2026-10-01 09:00 UTC",
                                 "#3: NOT approved: changed since you looked — reopen the card"],
        "not_in_review": [], "stale": [3], "failed": [], "message": ""}))
    c.post("/decide", data={"id": 3, "decision": "approve", "seen": SEEN}, headers={**HX, "X-CSRF-Token": token})
    flush(c, app)
    res = c.get("/results").text
    assert res.count("changed since you looked — reopen the card") == 1 and "#4: approved" in res
    assert app.state.pending.results[0].ok is False


def test_raw_409_from_an_older_workflow_is_translated(authed, app, mock):
    c, token = authed
    mock.post(WEBHOOK).mock(return_value=httpx.Response(200, json={
        "ok": False, "summary": ["#5: approved for 2026-10-01 09:00 UTC"], "not_in_review": [],
        "failed": [{"status": 409, "detail": {"message": "changed since you looked", "current_sha256": "cd" * 32}}],
        "message": ""}))
    c.post("/decide", data={"id": 5, "decision": "approve", "seen": SEEN}, headers={**HX, "X-CSRF-Token": token})
    flush(c, app)
    res = c.get("/results").text
    assert "changed since you looked — reopen the card" in res and "current_sha256" not in res
