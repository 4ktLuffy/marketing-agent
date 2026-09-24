"""Tests for the Postiz bridge.

The mocked Postiz shapes follow the Postiz source and docs (September 2026):
- routes + response shapes: apps/backend/src/public-api/routes/v1/public.integrations.controller.ts
  https://github.com/gitroomhq/postiz-app/blob/main/apps/backend/src/public-api/routes/v1/public.integrations.controller.ts
- auth header `Authorization: <key>`, 401 {"msg": "Invalid API key"}:
  https://github.com/gitroomhq/postiz-app/blob/main/apps/backend/src/services/auth/public.auth.middleware.ts
- create-post body: libraries/nestjs-libraries/src/dtos/posts/create.post.dto.ts and
  https://docs.postiz.com/public-api/posts/create ; response [{postId, integration}]:
  https://github.com/gitroomhq/postiz-docs/blob/main/public-api/openapi.json
- validation 400 {statusCode, provider, name, message}:
  https://github.com/gitroomhq/postiz-app/blob/main/apps/backend/src/api/routes/posts.validation.exception.ts
- 429 from @nestjs/throttler on POST /public/v1/posts only:
  https://github.com/gitroomhq/postiz-app/blob/main/libraries/nestjs-libraries/src/throttler/throttler.provider.ts
"""
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.main import app

KEY = "test-key"
AUTH = {"X-API-Key": KEY}
POSTIZ = "http://postiz.test/api"  # self-hosted Docker image style base
API = POSTIZ + "/public/v1"
SECRET = "pz_SECRET_do_not_leak_4f9a"

LINKEDIN_ID = "cm4linkedin0001"
X_ID = "cm4ean69r0003w8w1cdomox9n"
IG_ID = "cm4instagram001"
REDDIT_ID = "cm4reddit000001"

# GET /public/v1/integrations, shape from listIntegration() in the controller / openapi example
INTEGRATIONS = [
    {"id": LINKEDIN_ID, "name": "Acme Ltd", "identifier": "linkedin-page",
     "picture": "https://uploads.postiz.com/a.jpg", "disabled": False, "profile": "acme"},
    {"id": X_ID, "name": "Nevo David", "identifier": "x",
     "picture": "https://uploads.postiz.com/avatar.jpg", "disabled": False, "profile": "nevodavid",
     "customer": {"id": "customer-id", "name": "My Company"}},
    {"id": IG_ID, "name": "acme.insta", "identifier": "instagram",
     "picture": "", "disabled": False, "profile": "acme.insta"},
    {"id": REDDIT_ID, "name": "u/acme", "identifier": "reddit",
     "picture": "", "disabled": False, "profile": "acme"},
]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("POSTIZ_URL", POSTIZ)
    monkeypatch.setenv("POSTIZ_API_KEY", SECRET)
    monkeypatch.setenv("CHANNEL_MAP", json.dumps({
        "LinkedIn": LINKEDIN_ID, "x": X_ID, "instagram": IG_ID, "reddit": REDDIT_ID,
    }))
    monkeypatch.setenv("DRY_RUN", "false")
    main._integrations_cache.update(at=0.0, url=None, items=None)
    main._published.clear()


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


client = TestClient(app)


def item(**kw):
    return {"id": 12, "channel": "linkedin", "title": "Autumn launch",
            "text": "We launched autumn pricing & more.\nSee it here: https://s.example/abc",
            "link": "https://s.example/abc", "campaign": "autumn-launch",
            "short_url": "https://s.example/abc", **kw}


def mock_integrations(mock):
    return mock.get(f"{API}/integrations").mock(return_value=httpx.Response(200, json=INTEGRATIONS))


def mock_create(mock, response):
    return mock.post(f"{API}/posts").mock(return_value=response)


# ---------- health / config


def test_health_ok():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["dry_run"] is False
    assert body["postiz_url"] == API
    assert body["channels"] == ["instagram", "linkedin", "reddit", "x"]
    assert body["config_errors"] == []


def test_dry_run_is_the_default(monkeypatch):
    monkeypatch.delenv("DRY_RUN")
    assert client.get("/health").json()["dry_run"] is True
    monkeypatch.setenv("DRY_RUN", "maybe")  # anything unclear stays safe
    assert client.get("/health").json()["dry_run"] is True


def test_postiz_url_accepts_full_public_path(monkeypatch):
    monkeypatch.setenv("POSTIZ_URL", "https://api.postiz.com/public/v1/")
    assert client.get("/health").json()["postiz_url"] == "https://api.postiz.com/public/v1"
    monkeypatch.delenv("POSTIZ_URL")
    assert client.get("/health").json()["postiz_url"] == "https://api.postiz.com/public/v1"


def test_invalid_channel_map_still_starts_but_publish_is_503(monkeypatch, mock):
    monkeypatch.setenv("CHANNEL_MAP", "{linkedin: nope")
    health = client.get("/health").json()
    assert health["status"] == "degraded"
    assert "CHANNEL_MAP is not valid JSON" in health["config_errors"][0]
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 503
    assert "CHANNEL_MAP is not valid JSON" in r.json()["detail"]
    assert not mock.calls


def test_channel_map_object_form_with_settings(monkeypatch, mock):
    monkeypatch.setenv("CHANNEL_MAP", json.dumps(
        {"reddit": {"id": REDDIT_ID, "settings": {"subreddit": [{"value": {"id": "t5", "name": "x"}}]}}}))
    mock_integrations(mock)
    route = mock_create(mock, httpx.Response(201, json=[{"postId": "p9", "integration": REDDIT_ID}]))
    r = client.post("/publish", json=item(channel="reddit"), headers=AUTH)
    assert r.status_code == 200, r.text
    settings = json.loads(route.calls.last.request.content)["posts"][0]["settings"]
    assert settings["__type"] == "reddit" and "subreddit" in settings


# ---------- auth


def test_publish_without_internal_key_configured_is_503(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/publish", json=item(), headers=AUTH).status_code == 503


def test_publish_wrong_or_missing_key_is_401():
    assert client.post("/publish", json=item(), headers={"X-API-Key": "nope"}).status_code == 401
    assert client.post("/publish", json=item()).status_code == 401


# ---------- publishing


def test_happy_path_now_post(mock):
    ints = mock_integrations(mock)
    route = mock_create(mock, httpx.Response(201, json=[{"postId": "post-123", "integration": LINKEDIN_ID}]))
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"url": None, "postiz_id": "post-123", "status": "queued",
                        "channel": "linkedin", "provider": "linkedin-page"}

    req = route.calls.last.request
    assert req.headers["authorization"] == SECRET  # raw key, no "Bearer"
    body = json.loads(req.content)
    assert body["type"] == "now"
    assert body["shortLink"] is False and body["tags"] == []
    assert body["date"].endswith("Z")
    post = body["posts"][0]
    assert post["integration"] == {"id": LINKEDIN_ID}
    assert post["settings"] == {"__type": "linkedin-page"}
    # plain text becomes <p> paragraphs with '&' escaped; the link was already in the text
    assert post["value"] == [{"content": "<p>We launched autumn pricing &amp; more.</p>"
                                         "<p>See it here: https://s.example/abc</p>", "image": []}]
    assert ints.calls.last.request.headers["authorization"] == SECRET


def test_x_gets_required_reply_setting_and_link_is_appended(mock):
    mock_integrations(mock)
    route = mock_create(mock, httpx.Response(201, json=[{"postId": "p1", "integration": X_ID}]))
    r = client.post("/publish", json=item(channel="X", text="Short post", link="https://s.example/q"),
                    headers=AUTH)
    assert r.status_code == 200, r.text
    post = json.loads(route.calls.last.request.content)["posts"][0]
    assert post["settings"] == {"__type": "x", "who_can_reply_post": "everyone"}
    assert post["value"][0]["content"] == "<p>Short post</p><p></p><p>https://s.example/q</p>"


def test_retry_of_same_item_does_not_post_twice(mock):
    mock_integrations(mock)
    route = mock_create(mock, httpx.Response(201, json=[{"postId": "p1", "integration": LINKEDIN_ID}]))
    assert client.post("/publish", json=item(), headers=AUTH).status_code == 200
    again = client.post("/publish", json=item(), headers=AUTH)
    assert again.status_code == 200
    assert again.json()["status"] == "already_published"
    assert again.json()["postiz_id"] == "p1"
    assert route.call_count == 1


def test_unmapped_channel_is_422_naming_it(mock):
    r = client.post("/publish", json=item(channel="tiktok"), headers=AUTH)
    assert r.status_code == 422
    assert "'tiktok'" in r.json()["detail"]
    assert not mock.calls


def test_media_required_provider_is_422(mock):
    mock_integrations(mock)
    create = mock_create(mock, httpx.Response(201, json=[]))
    r = client.post("/publish", json=item(channel="instagram"), headers=AUTH)
    assert r.status_code == 422
    assert "instagram" in r.json()["detail"] and "text only" in r.json()["detail"]
    assert not create.called


def test_provider_missing_required_settings_is_422(mock):
    mock_integrations(mock)
    create = mock_create(mock, httpx.Response(201, json=[]))
    r = client.post("/publish", json=item(channel="reddit"), headers=AUTH)
    assert r.status_code == 422
    assert "subreddit" in r.json()["detail"]
    assert not create.called


def test_mapped_id_unknown_to_postiz_is_503(monkeypatch, mock):
    monkeypatch.setenv("CHANNEL_MAP", json.dumps({"linkedin": "gone"}))
    mock_integrations(mock)
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 503
    assert "gone" in r.json()["detail"]


def test_empty_text_and_link_is_422(mock):
    r = client.post("/publish", json=item(text="  ", link=None), headers=AUTH)
    assert r.status_code == 422


@pytest.mark.parametrize("status,body,expect", [
    (400, {"statusCode": 400, "provider": "linkedin-page", "name": "LinkedIn Page",
           "message": "post is too long, please fix it"}, "post is too long"),
    (400, {"msg": "Integration with id x not found"}, "not found"),
    (401, {"msg": "Invalid API key"}, "rejected POSTIZ_API_KEY"),
    (500, {"statusCode": 500, "message": "Internal server error"}, "Internal server error"),
    (503, "upstream down", "upstream down"),
])
def test_postiz_errors_are_502(mock, status, body, expect):
    mock_integrations(mock)
    resp = httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
    mock_create(mock, resp)
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["postiz_status"] == status
    assert expect in detail["postiz_error"]


def test_postiz_rate_limit_is_502_with_retry_after(mock):
    mock_integrations(mock)
    mock_create(mock, httpx.Response(429, json={"statusCode": 429, "message": "ThrottlerException: Too Many Requests"},
                                     headers={"Retry-After": "1800"}))
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 502
    assert r.headers["retry-after"] == "1800"
    assert r.json()["detail"]["retry_after"] == "1800"
    assert "rate limit" in r.json()["detail"]["message"]


def test_postiz_unreachable_and_timeout_are_502(mock):
    mock.get(f"{API}/integrations").mock(side_effect=httpx.ConnectError("refused"))
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 502 and "cannot reach Postiz" in r.json()["detail"]["postiz_error"]
    mock.get(f"{API}/integrations").mock(side_effect=httpx.ReadTimeout("slow"))
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 502 and "30 s" in r.json()["detail"]["postiz_error"]


def test_missing_postiz_key_when_live_is_503(monkeypatch, mock):
    monkeypatch.delenv("POSTIZ_API_KEY")
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 503
    assert not mock.calls


# ---------- dry run


def test_dry_run_validates_without_calling_postiz(monkeypatch, mock):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.delenv("POSTIZ_API_KEY")  # not needed in dry run
    r = client.post("/publish", json=item(channel="x"), headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "dry_run"
    assert body["postiz_id"] is None and body["url"] is None
    assert body["would_send"]["posts"][0]["integration"] == {"id": X_ID}
    assert body["would_send"]["posts"][0]["settings"]["who_can_reply_post"] == "everyone"
    assert not mock.calls


def test_dry_run_still_rejects_unmapped_and_media_channels(monkeypatch, mock):
    monkeypatch.setenv("DRY_RUN", "true")
    assert client.post("/publish", json=item(channel="tiktok"), headers=AUTH).status_code == 422
    assert client.post("/publish", json=item(channel="instagram"), headers=AUTH).status_code == 422
    assert not mock.calls


# ---------- integrations


def test_integrations_lists_postiz_channels(mock):
    mock_integrations(mock)
    r = client.get("/integrations")
    assert r.status_code == 200
    rows = {row["id"]: row for row in r.json()}
    assert rows[X_ID] == {"id": X_ID, "name": "Nevo David", "provider": "x", "disabled": False,
                          "profile": "nevodavid", "channels": ["x"]}
    assert rows[LINKEDIN_ID]["channels"] == ["linkedin"]


def test_integrations_postiz_error_is_502(mock):
    mock.get(f"{API}/integrations").mock(return_value=httpx.Response(401, json={"msg": "Invalid API key"}))
    assert client.get("/integrations").status_code == 502


# ---------- the Postiz key never leaks


def test_postiz_key_never_appears_in_any_response(monkeypatch, mock):
    # Postiz echoing the key back in an error must not reach our caller either.
    echo = {"msg": f"Invalid API key {SECRET}"}
    mock.get(f"{API}/integrations").mock(side_effect=[
        httpx.Response(200, json=INTEGRATIONS),
        httpx.Response(401, json=echo),
        httpx.Response(200, json=INTEGRATIONS),
    ])
    mock.post(f"{API}/posts").mock(side_effect=[
        httpx.Response(400, json={"message": f"bad {SECRET}"}),
        httpx.Response(429, json={"message": SECRET}),
        httpx.Response(201, json=[{"postId": "p1", "integration": LINKEDIN_ID}]),
    ])
    responses = [
        client.get("/health"),
        client.get("/integrations"),           # 200
        client.get("/integrations"),           # 401 echoing the key
        client.post("/publish", json=item(), headers=AUTH),   # 400 echo
        client.post("/publish", json=item(), headers=AUTH),   # 429 echo
        client.post("/publish", json=item(), headers=AUTH),   # ok
        client.post("/publish", json=item(channel="nope"), headers=AUTH),
        client.post("/publish", json=item(), headers={"X-API-Key": "bad"}),
        client.post("/publish", json={"bad": 1}, headers=AUTH),
    ]
    monkeypatch.setenv("DRY_RUN", "true")
    responses.append(client.post("/publish", json=item(id=99), headers=AUTH))
    monkeypatch.setenv("CHANNEL_MAP", "{bad")
    responses.append(client.post("/publish", json=item(), headers=AUTH))
    responses.append(client.get("/health"))
    assert {r.status_code for r in responses} >= {200, 401, 422, 502, 503}
    for r in responses:
        assert SECRET not in r.text, r.text
        assert SECRET not in json.dumps(dict(r.headers))
