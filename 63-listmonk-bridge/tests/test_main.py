"""Tests for the Listmonk bridge.

Mocked Listmonk shapes follow the Listmonk docs and source (September 2026):
- auth + {data}/{message} envelopes: https://listmonk.app/docs/apis/apis/
- campaigns: https://listmonk.app/docs/apis/campaigns/ and cmd/campaigns.go
  (CreateCampaign, TestCampaign, validateCampaignFields)
- status change needs send_at: internal/core/campaigns.go UpdateCampaignStatus
- lists: https://listmonk.app/docs/apis/lists/
"""
import base64
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app

KEY = "test-key"
AUTH = {"X-API-Key": KEY}
LM = "http://listmonk.test:9000"
API = LM + "/api"
USER = "mkt-bridge"
SECRET = "lm_SECRET_do_not_leak_7c1e"
PUBLIC = "https://news.example.com"

client = TestClient(app)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name, value in {
        "INTERNAL_API_KEY": KEY, "LISTMONK_URL": LM, "LISTMONK_USER": USER,
        "LISTMONK_TOKEN": SECRET, "LISTMONK_PUBLIC_URL": PUBLIC, "LISTMONK_LIST_IDS": "3, 4",
        "DRY_RUN": "false", "TEST_EMAILS": "me@example.com, boss@example.com",
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("ALLOW_SCHEDULE", "LISTMONK_AUTH_MODE", "LISTMONK_FROM_EMAIL", "LISTMONK_TEMPLATE_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


def campaign_created(cid=17, status="draft"):
    return httpx.Response(200, json={"data": {
        "id": cid, "uuid": "57702beb-6fae-4355-a324-c2fd5b59a549", "type": "regular",
        "name": "Newsletter", "subject": "This week", "status": status, "content_type": "html",
        "lists": [{"id": 3, "name": "Newsletter"}], "send_at": None}})


def future(hours=24):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def item(**kw):
    base = {"subject": "This week at Acme Roasters", "preheader": "Three new roasts",
            "body_html": "<p>Hello</p><p><a href=\"{{unsubscribe_url}}\">Unsubscribe</a></p>"}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


# ---------- create draft


def test_create_html_draft(mock):
    route = mock.post(f"{API}/campaigns").mock(return_value=campaign_created())
    r = client.post("/campaigns", json=item(body_text="Hello\nUnsubscribe: {{unsubscribe_url}}"),
                    headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"
    assert r.json()["campaign_id"] == 17
    assert r.json()["url"] == f"{PUBLIC}/admin/campaigns/17"
    assert r.json()["scheduled"] is False

    req = route.calls.last.request
    assert req.headers["authorization"] == f"token {USER}:{SECRET}"
    body = json.loads(req.content)
    assert body["content_type"] == "html" and body["type"] == "regular"
    assert body["lists"] == [3, 4]  # default from LISTMONK_LIST_IDS
    assert body["subject"] == "This week at Acme Roasters"
    assert "send_at" not in body and "status" not in body
    # 18's placeholder becomes Listmonk's template tag (an unknown {{ }} is a Listmonk 400)
    assert "{{ UnsubscribeURL }}" in body["body"] and "unsubscribe_url" not in body["body"]
    assert body["altbody"].endswith("{{ UnsubscribeURL }}")
    # preheader is injected as a hidden block at the top
    assert body["body"].startswith('<div style="display:none')
    assert "Three new roasts" in body["body"]


def test_create_markdown_draft_with_explicit_lists(mock):
    route = mock.post(f"{API}/campaigns").mock(return_value=campaign_created(21))
    r = client.post("/campaigns", headers=AUTH, json=item(
        body_html=None, body_markdown="# Hi\n\nOur **roasts**.", list_ids=[4], name="Week 39"))
    assert r.status_code == 200, r.text
    body = json.loads(route.calls.last.request.content)
    assert body["content_type"] == "markdown"
    assert body["lists"] == [4] and body["name"] == "Week 39"
    assert body["body"].endswith("# Hi\n\nOur **roasts**.")


def test_preheader_not_duplicated_and_inserted_after_body_tag(mock):
    route = mock.post(f"{API}/campaigns").mock(return_value=campaign_created())
    doc = "<!DOCTYPE html><html><body style=\"margin:0\"><span>Three new roasts</span><p>x</p></body></html>"
    client.post("/campaigns", headers=AUTH, json=item(body_html=doc))
    assert json.loads(route.calls.last.request.content)["body"] == doc  # 18 already has it
    client.post("/campaigns", headers=AUTH, json=item(body_html=doc.replace("Three new roasts", "")))
    sent = json.loads(route.calls.last.request.content)["body"]
    assert sent.index('<body style="margin:0">') < sent.index("display:none") < sent.index("<p>x</p>")


def test_from_email_and_template_from_env(monkeypatch, mock):
    monkeypatch.setenv("LISTMONK_FROM_EMAIL", "Acme <news@acme.example>")
    monkeypatch.setenv("LISTMONK_TEMPLATE_ID", "5")
    route = mock.post(f"{API}/campaigns").mock(return_value=campaign_created())
    assert client.post("/campaigns", headers=AUTH, json=item()).status_code == 200
    body = json.loads(route.calls.last.request.content)
    assert body["from_email"] == "Acme <news@acme.example>" and body["template_id"] == 5


def test_basic_auth_mode(monkeypatch, mock):
    monkeypatch.setenv("LISTMONK_AUTH_MODE", "basic")
    route = mock.post(f"{API}/campaigns").mock(return_value=campaign_created())
    assert client.post("/campaigns", headers=AUTH, json=item()).status_code == 200
    expected = "Basic " + base64.b64encode(f"{USER}:{SECRET}".encode()).decode()
    assert route.calls.last.request.headers["authorization"] == expected


# ---------- scheduling


def test_send_at_ignored_when_schedule_not_allowed(mock):
    create = mock.post(f"{API}/campaigns").mock(return_value=campaign_created())
    status = mock.put(f"{API}/campaigns/17/status").mock(return_value=httpx.Response(200, json={"data": {}}))
    r = client.post("/campaigns", headers=AUTH, json=item(send_at=future()))
    assert r.status_code == 200, r.text
    assert r.json()["scheduled"] is False and r.json()["send_at_ignored"] is True
    assert "send_at" not in json.loads(create.calls.last.request.content)
    assert not status.called


def test_schedules_only_when_allowed_and_send_at_given(monkeypatch, mock):
    monkeypatch.setenv("ALLOW_SCHEDULE", "true")
    create = mock.post(f"{API}/campaigns").mock(return_value=campaign_created())
    status = mock.put(f"{API}/campaigns/17/status").mock(
        return_value=httpx.Response(200, json={"data": campaign_created(status="scheduled").json()["data"]}))
    when = future()
    r = client.post("/campaigns", headers=AUTH, json=item(send_at=when))
    assert r.status_code == 200, r.text
    assert r.json()["scheduled"] is True and r.json()["send_at"] == when
    assert json.loads(create.calls.last.request.content)["send_at"] == when  # needed to schedule
    assert json.loads(status.calls.last.request.content) == {"status": "scheduled"}
    # allowed but no send_at: stays a draft
    r = client.post("/campaigns", headers=AUTH, json=item())
    assert r.json()["scheduled"] is False and status.call_count == 1


def test_allow_schedule_needs_explicit_true(monkeypatch, mock):
    monkeypatch.setenv("ALLOW_SCHEDULE", "maybe")
    assert client.get("/health").json()["allow_schedule"] is False


def test_schedule_failure_reports_the_draft(monkeypatch, mock):
    monkeypatch.setenv("ALLOW_SCHEDULE", "true")
    mock.post(f"{API}/campaigns").mock(return_value=campaign_created())
    mock.put(f"{API}/campaigns/17/status").mock(
        return_value=httpx.Response(400, json={"message": "Only draft campaigns can be scheduled"}))
    r = client.post("/campaigns", headers=AUTH, json=item(send_at=future()))
    assert r.status_code == 502
    d = r.json()["detail"]
    assert d["campaign_id"] == 17 and d["url"].endswith("/admin/campaigns/17")


def test_send_at_in_past_is_422(mock):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = client.post("/campaigns", headers=AUTH, json=item(send_at=past))
    assert r.status_code == 422 and "past" in r.json()["detail"]
    assert not mock.calls


# ---------- dry run


def test_dry_run_is_default_and_calls_nothing(monkeypatch, mock):
    monkeypatch.delenv("DRY_RUN")
    monkeypatch.delenv("LISTMONK_TOKEN")  # not needed in dry run
    assert client.get("/health").json()["dry_run"] is True
    r = client.post("/campaigns", headers=AUTH, json=item(send_at=future()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "dry_run" and body["campaign_id"] is None and body["url"] is None
    assert body["would_schedule"] is False
    assert body["would_send"]["content_type"] == "html" and body["would_send"]["lists"] == [3, 4]
    t = client.post("/campaigns/17/test", headers=AUTH)
    assert t.status_code == 200 and t.json() == {"status": "dry_run", "campaign_id": 17, "recipients": 2}
    monkeypatch.setenv("DRY_RUN", "whatever")
    assert client.get("/health").json()["dry_run"] is True
    assert not mock.calls


def test_live_without_credentials_is_503(monkeypatch, mock):
    monkeypatch.delenv("LISTMONK_TOKEN")
    r = client.post("/campaigns", headers=AUTH, json=item())
    assert r.status_code == 503 and "LISTMONK_TOKEN" in r.json()["detail"]
    assert not mock.calls


# ---------- auth


def test_wrong_or_missing_key_is_401(mock):
    for path in ("/campaigns", "/campaigns/1/test"):
        assert client.post(path, json=item(), headers={"X-API-Key": "nope"}).status_code == 401
        assert client.post(path, json=item()).status_code == 401
    assert client.get("/lists").status_code == 401
    assert not mock.calls


def test_no_internal_key_configured_is_503(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/campaigns", json=item(), headers=AUTH).status_code == 503


# ---------- validation


@pytest.mark.parametrize("patch,expect", [
    ({"subject": "x" * 201}, "limit is 200"),
    ({"subject": "   "}, "subject is empty"),
    ({"body_html": "<p>" + "a" * (200 * 1024) + "</p>"}, "200 KB"),
    ({"body_html": "<p>hi</p><SCRIPT src=x></SCRIPT>"}, "script"),
    ({"body_html": None, "body_markdown": "hi <script>alert(1)</script>"}, "script"),
    ({"body_markdown": "also markdown"}, "exactly one"),
    ({"body_html": None}, "exactly one"),
    ({"list_ids": [0]}, "positive"),
])
def test_validation_422(mock, patch, expect):
    r = client.post("/campaigns", headers=AUTH, json=item(**patch))
    assert r.status_code == 422, r.text
    assert expect in json.dumps(r.json())
    assert not mock.calls


def test_list_id_not_allowed_is_422(mock):
    r = client.post("/campaigns", headers=AUTH, json=item(list_ids=[3, 9]))
    assert r.status_code == 422 and "[9]" in r.json()["detail"]
    assert not mock.calls


def test_no_lists_anywhere_is_422(monkeypatch, mock):
    monkeypatch.delenv("LISTMONK_LIST_IDS")
    r = client.post("/campaigns", headers=AUTH, json=item())
    assert r.status_code == 422 and "LISTMONK_LIST_IDS" in r.json()["detail"]
    # without an allowlist, any explicit id is accepted
    mock.post(f"{API}/campaigns").mock(return_value=campaign_created())
    assert client.post("/campaigns", headers=AUTH, json=item(list_ids=[9])).status_code == 200


# ---------- Listmonk errors


@pytest.mark.parametrize("status,body,expect", [
    (401, {"message": "invalid API credentials"}, "rejected LISTMONK_USER"),
    (403, {"message": "permission denied"}, "rejected LISTMONK_USER"),
    (400, {"message": "Error compiling template: function \"foo\" not defined"}, "compiling template"),
    (500, {"message": "Error creating campaign"}, "Error creating campaign"),
    (502, "bad gateway", "bad gateway"),
])
def test_listmonk_errors_are_502(mock, status, body, expect):
    resp = httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
    mock.post(f"{API}/campaigns").mock(return_value=resp)
    r = client.post("/campaigns", headers=AUTH, json=item())
    assert r.status_code == 502
    assert r.json()["detail"]["listmonk_status"] == status
    assert expect in r.json()["detail"]["listmonk_error"]


def test_listmonk_unreachable_and_timeout_are_502(mock):
    mock.post(f"{API}/campaigns").mock(side_effect=httpx.ConnectError("refused"))
    r = client.post("/campaigns", headers=AUTH, json=item())
    assert r.status_code == 502 and "cannot reach Listmonk" in r.json()["detail"]["listmonk_error"]
    mock.post(f"{API}/campaigns").mock(side_effect=httpx.ReadTimeout("slow"))
    r = client.post("/campaigns", headers=AUTH, json=item())
    assert r.status_code == 502 and "30 s" in r.json()["detail"]["listmonk_error"]


# ---------- lists


def test_lists_proxy(mock):
    route = mock.get(f"{API}/lists").mock(return_value=httpx.Response(200, json={"data": {
        "results": [
            {"id": 3, "uuid": "u3", "name": "Newsletter", "type": "public", "optin": "double",
             "tags": [], "subscriber_count": 1204, "subscriber_statuses": {"confirmed": 1200}},
            {"id": 7, "uuid": "u7", "name": "Internal", "type": "private", "optin": "single",
             "tags": [], "subscriber_count": 3},
        ], "total": 2, "per_page": 0, "page": 1}}))
    r = client.get("/lists", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"id": 3, "name": "Newsletter", "subscriber_count": 1204, "allowed": True},
        {"id": 7, "name": "Internal", "subscriber_count": 3, "allowed": False},
    ]
    assert route.calls.last.request.url.params["per_page"] == "all"


def test_lists_listmonk_error_is_502(mock):
    mock.get(f"{API}/lists").mock(return_value=httpx.Response(401, json={"message": "invalid"}))
    assert client.get("/lists", headers=AUTH).status_code == 502


# ---------- test send


def test_test_send_reposts_campaign_fields(mock):
    mock.get(f"{API}/campaigns/17").mock(return_value=httpx.Response(200, json={"data": {
        "id": 17, "name": "Week 39", "subject": "This week", "from_email": "news@acme.example",
        "body": "<p>Hi</p>", "altbody": None, "content_type": "html", "messenger": "email",
        "template_id": 1, "type": "regular", "headers": [], "tags": [], "status": "draft",
        "lists": [{"id": 3, "name": "Newsletter"}]}}))
    test = mock.post(f"{API}/campaigns/17/test").mock(return_value=httpx.Response(200, json={"data": True}))
    r = client.post("/campaigns/17/test", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent" and r.json()["recipients"] == 2
    body = json.loads(test.calls.last.request.content)
    assert body["subscribers"] == ["me@example.com", "boss@example.com"]
    assert body["lists"] == [3] and body["messenger"] == "email" and body["subject"] == "This week"
    assert "altbody" not in body and "status" not in body


def test_test_send_unknown_campaign_is_404_and_no_emails_is_503(monkeypatch, mock):
    mock.get(f"{API}/campaigns/99").mock(return_value=httpx.Response(404, json={"message": "Campaign not found"}))
    assert client.post("/campaigns/99/test", headers=AUTH).status_code == 404
    mock.get(f"{API}/campaigns/17").mock(return_value=campaign_created())
    mock.post(f"{API}/campaigns/17/test").mock(
        return_value=httpx.Response(400, json={"message": "No known subscribers to test"}))
    assert client.post("/campaigns/17/test", headers=AUTH).status_code == 502
    monkeypatch.setenv("TEST_EMAILS", "")
    assert client.post("/campaigns/17/test", headers=AUTH).status_code == 503


# ---------- health + secrets


def test_health_configured_and_degraded(monkeypatch):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["configured"] is True and h["list_ids"] == [3, 4]
    assert h["test_emails"] == 2 and h["auth_mode"] == "token"
    monkeypatch.setenv("LISTMONK_LIST_IDS", "3,news")
    h = client.get("/health").json()
    assert h["configured"] is False and "LISTMONK_LIST_IDS" in h["config_errors"][0]


def test_token_never_in_responses_or_logs(monkeypatch, mock, caplog):
    caplog.set_level(logging.DEBUG)
    echo = {"message": f"bad token {USER}:{SECRET} / {SECRET}"}
    mock.post(f"{API}/campaigns").mock(side_effect=[
        httpx.Response(401, json=echo), httpx.Response(500, json=echo), campaign_created()])
    mock.get(f"{API}/lists").mock(return_value=httpx.Response(403, json=echo))
    mock.get(f"{API}/campaigns/17").mock(return_value=httpx.Response(500, json=echo))
    responses = [
        client.get("/health"),
        client.post("/campaigns", headers=AUTH, json=item()),
        client.post("/campaigns", headers=AUTH, json=item()),
        client.post("/campaigns", headers=AUTH, json=item()),
        client.get("/lists", headers=AUTH),
        client.post("/campaigns/17/test", headers=AUTH),
        client.post("/campaigns", headers={"X-API-Key": "bad"}, json=item()),
        client.post("/campaigns", headers=AUTH, json={"subject": SECRET}),
    ]
    monkeypatch.setenv("LISTMONK_AUTH_MODE", "basic")
    responses.append(client.get("/health"))
    assert {r.status_code for r in responses} >= {200, 401, 422, 502}
    b64 = base64.b64encode(f"{USER}:{SECRET}".encode()).decode()
    for r in responses[:-2] + responses[-1:]:  # the 422 echoes the caller's own input back
        assert SECRET not in r.text and b64 not in r.text, r.text
        assert SECRET not in json.dumps(dict(r.headers))
    assert SECRET not in caplog.text and b64 not in caplog.text
