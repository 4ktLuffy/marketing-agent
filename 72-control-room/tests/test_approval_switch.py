"""APPROVAL_URL set: decisions go to the approval service (90) instead of the n8n webhook, with the
same payload and X-Control-Key; the control room still never sends an approver or internal key."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from .conftest import KEYS, WEBHOOK, csrf_of, flush, login

APPROVAL = "http://approval.internal:8000"
HX = {"HX-Request": "true"}


@pytest.fixture
def approval_app(monkeypatch):
    monkeypatch.setenv("APPROVAL_URL", APPROVAL + "/")
    from app.main import create_app
    return create_app()


@pytest.fixture
def approval_client(approval_app, mock):
    with TestClient(approval_app, follow_redirects=False) as c:
        assert login(c).status_code == 303
        yield c, csrf_of(c.get("/more").text)


def test_decisions_go_to_the_approval_service(approval_client, approval_app, mock):
    c, token = approval_client
    svc = mock.post(f"{APPROVAL}/decisions").mock(return_value=httpx.Response(200, json={
        "ok": True, "summary": ["#7: approved for 2026-10-01 09:00 UTC"], "not_in_review": [], "stale": [],
        "failed": [], "message": "#7: approved"}))
    hook = mock.post(WEBHOOK).mock(return_value=httpx.Response(500))
    c.post("/decide", data={"id": 7, "decision": "approve"}, headers={**HX, "X-CSRF-Token": token})
    flush(c, approval_app)
    assert svc.call_count == 1 and not hook.called
    req = svc.calls.last.request
    assert json.loads(req.content) == {"reviewer": "alex", "decisions": [{"id": 7, "decision": "approve"}]}
    assert req.headers["X-Control-Key"] == KEYS["CONTROL_ROOM_KEY"]
    assert "X-API-Key" not in req.headers and "X-Approver-Key" not in req.headers
    assert "#7: approved for 2026-10-01 09:00 UTC" in c.get("/results").text


def test_approval_service_down_says_not_saved(approval_client, approval_app, mock):
    c, token = approval_client
    mock.post(f"{APPROVAL}/decisions").mock(side_effect=httpx.ConnectError("boom"))
    c.post("/decide", data={"id": 8, "decision": "approve"}, headers={**HX, "X-CSRF-Token": token})
    flush(c, approval_app)
    res = c.get("/results").text
    assert "Not saved" in res and "approval.internal" not in res


def test_unset_keeps_n8n(authed, app, mock):
    c, token = authed
    hook = mock.post(WEBHOOK).mock(return_value=httpx.Response(200, json={
        "ok": True, "summary": ["#9: approved"], "not_in_review": [], "failed": [], "message": ""}))
    c.post("/decide", data={"id": 9, "decision": "approve"}, headers={**HX, "X-CSRF-Token": token})
    flush(c, app)
    assert hook.call_count == 1
