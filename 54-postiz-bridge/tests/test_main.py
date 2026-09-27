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
                        "channel": "linkedin", "provider": "linkedin-page", "media": []}

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
    assert "instagram" in r.json()["detail"] and "no image_url" in r.json()["detail"]
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


# ---------- images (image_url, e.g. a card from 17-image-cards)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
CARD_PUBLIC = "http://localhost:8117"
CARD_INTERNAL = "http://image-cards:8000"
CARD_PATH = "/cards/0123456789abcdef0123456789abcdef.png"
# POST /public/v1/upload -> MediaFile, example from postiz-docs public-api/openapi.json
MEDIA = {"id": "e639003b-f727-4a1e-87bd-74a2c48ae41e", "name": "card.png",
         "path": "https://uploads.postiz.com/card.png", "organizationId": "org",
         "createdAt": "2024-12-14T08:18:54.274Z", "updatedAt": "2024-12-14T08:18:54.274Z"}


@pytest.fixture
def cards_env(monkeypatch):
    monkeypatch.setenv("CARDS_PUBLIC_URL", CARD_PUBLIC)
    monkeypatch.setenv("CARDS_URL", CARD_INTERNAL)


def mock_card(mock, status=200, content=PNG, ctype="image/png"):
    return mock.get(CARD_INTERNAL + CARD_PATH).mock(
        return_value=httpx.Response(status, content=content, headers={"content-type": ctype}))


def test_image_is_fetched_uploaded_and_attached(cards_env, mock):
    mock_integrations(mock)
    card = mock_card(mock)
    upload = mock.post(f"{API}/upload").mock(return_value=httpx.Response(201, json=MEDIA))
    create = mock_create(mock, httpx.Response(201, json=[{"postId": "p1", "integration": LINKEDIN_ID}]))
    r = client.post("/publish", json=item(image_url=CARD_PUBLIC + CARD_PATH), headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["media"] == [{"id": MEDIA["id"], "path": MEDIA["path"]}]

    # fetched from the internal cards URL, without the Postiz key
    assert card.called
    assert "authorization" not in card.calls.last.request.headers
    # uploaded as multipart field "file" with the Postiz key
    up = upload.calls.last.request
    assert up.headers["authorization"] == SECRET
    assert up.headers["content-type"].startswith("multipart/form-data")
    assert b'name="file"; filename="0123456789abcdef0123456789abcdef.png"' in up.content
    assert b"Content-Type: image/png" in up.content and PNG in up.content
    # the post references the upload by id and path
    post = json.loads(create.calls.last.request.content)["posts"][0]
    assert post["value"][0]["image"] == [{"id": MEDIA["id"], "path": MEDIA["path"]}]


def test_instagram_with_image_is_accepted(cards_env, mock):
    mock_integrations(mock)
    mock_card(mock)
    mock.post(f"{API}/upload").mock(return_value=httpx.Response(201, json=MEDIA))
    create = mock_create(mock, httpx.Response(201, json=[{"postId": "p2", "integration": IG_ID}]))
    r = client.post("/publish", json=item(channel="instagram", image_url=CARD_PUBLIC + CARD_PATH), headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "instagram"
    assert json.loads(create.calls.last.request.content)["posts"][0]["value"][0]["image"][0]["id"] == MEDIA["id"]


def test_instagram_without_image_says_so(mock):
    mock_integrations(mock)
    r = client.post("/publish", json=item(channel="instagram", image_url="  "), headers=AUTH)
    assert r.status_code == 422
    assert "no image_url" in r.json()["detail"]


def test_other_urls_are_fetched_as_given(mock):
    mock_integrations(mock)
    img = mock.get("https://cdn.example/a/pic.jpeg").mock(
        return_value=httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}))
    upload = mock.post(f"{API}/upload").mock(return_value=httpx.Response(201, json=MEDIA))
    mock_create(mock, httpx.Response(201, json=[{"postId": "p3", "integration": LINKEDIN_ID}]))
    r = client.post("/publish", json=item(image_url="https://cdn.example/a/pic.jpeg"), headers=AUTH)
    assert r.status_code == 200, r.text
    assert img.called
    assert b'filename="pic.jpg"' in upload.calls.last.request.content


@pytest.mark.parametrize("status,content,ctype,expect", [
    (404, b"nope", "text/plain", "HTTP 404"),
    (200, b"<html>", "text/html", "not a PNG"),
    (200, b"", "image/png", "empty body"),
    (200, b"x" * (10 * 1024 * 1024 + 1), "image/png", "larger than 10 MB"),
])
def test_image_that_cannot_be_loaded_is_502_and_nothing_is_posted(cards_env, mock, status, content, ctype, expect):
    mock_integrations(mock)
    mock_card(mock, status=status, content=content, ctype=ctype)
    upload = mock.post(f"{API}/upload").mock(return_value=httpx.Response(201, json=MEDIA))
    create = mock_create(mock, httpx.Response(201, json=[]))
    r = client.post("/publish", json=item(image_url=CARD_PUBLIC + CARD_PATH), headers=AUTH)
    assert r.status_code == 502
    assert expect in r.json()["detail"]["image_error"]
    assert not upload.called and not create.called


def test_image_url_must_be_http(mock):
    mock_integrations(mock)
    r = client.post("/publish", json=item(image_url="file:///etc/passwd"), headers=AUTH)
    assert r.status_code == 502 and "http(s)" in r.json()["detail"]["image_error"]


def test_image_unreachable_is_502(cards_env, mock):
    mock_integrations(mock)
    mock.get(CARD_INTERNAL + CARD_PATH).mock(side_effect=httpx.ConnectError("refused"))
    r = client.post("/publish", json=item(image_url=CARD_PUBLIC + CARD_PATH), headers=AUTH)
    assert r.status_code == 502 and "cannot fetch" in r.json()["detail"]["image_error"]


@pytest.mark.parametrize("resp", [
    httpx.Response(400, json={"msg": "Invalid file type"}),
    httpx.Response(201, json={"name": "no id"}),
])
def test_upload_failure_is_502_and_nothing_is_posted(cards_env, mock, resp):
    mock_integrations(mock)
    mock_card(mock)
    mock.post(f"{API}/upload").mock(return_value=resp)
    create = mock_create(mock, httpx.Response(201, json=[]))
    r = client.post("/publish", json=item(image_url=CARD_PUBLIC + CARD_PATH), headers=AUTH)
    assert r.status_code == 502
    assert not create.called


def test_upload_error_echoing_the_key_is_scrubbed(cards_env, mock):
    mock_integrations(mock)
    mock_card(mock)
    mock.post(f"{API}/upload").mock(return_value=httpx.Response(400, json={"msg": f"bad key {SECRET}"}))
    r = client.post("/publish", json=item(image_url=CARD_PUBLIC + CARD_PATH), headers=AUTH)
    assert r.status_code == 502 and SECRET not in r.text


def test_dry_run_reports_the_upload_without_calling_postiz(cards_env, monkeypatch, mock):
    monkeypatch.setenv("DRY_RUN", "true")
    card = mock_card(mock)
    r = client.post("/publish", json=item(channel="instagram", image_url=CARD_PUBLIC + CARD_PATH), headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "dry_run"
    assert body["would_upload"] == {
        "image_url": CARD_PUBLIC + CARD_PATH, "fetched_from": CARD_INTERNAL + CARD_PATH,
        "endpoint": f"{API}/upload", "file": "0123456789abcdef0123456789abcdef.png",
        "content_type": "image/png", "bytes": len(PNG)}
    assert body["would_send"]["posts"][0]["value"][0]["image"] == [
        {"id": "<id from POST /upload>", "path": "<path from POST /upload>"}]
    # only our own image service was called, never Postiz
    assert [c.request.url.host for c in mock.calls] == ["image-cards"]
    assert card.called


def test_dry_run_without_image_has_no_upload(monkeypatch, mock):
    monkeypatch.setenv("DRY_RUN", "true")
    body = client.post("/publish", json=item(), headers=AUTH).json()
    assert body["would_upload"] is None
    assert body["would_send"]["posts"][0]["value"][0]["image"] == []


def test_dry_run_with_broken_image_is_502(cards_env, monkeypatch, mock):
    monkeypatch.setenv("DRY_RUN", "true")
    mock_card(mock, status=404, content=b"", ctype="text/plain")
    r = client.post("/publish", json=item(image_url=CARD_PUBLIC + CARD_PATH), headers=AUTH)
    assert r.status_code == 502


def test_video_networks_still_refuse_an_image_only_post(monkeypatch, mock):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("CHANNEL_MAP", json.dumps({"youtube": "yt1"}))
    r = client.post("/publish", json=item(channel="youtube", image_url="https://cdn.example/p.png"), headers=AUTH)
    assert r.status_code == 422 and "video" in r.json()["detail"]
    assert not mock.calls


# ---------- videos (video_url, e.g. an MP4 from 71-video-assembly)

VIDEO_PUBLIC = "http://localhost:8171"
VIDEO_INTERNAL = "http://video-assembly:8000"
VIDEO_PATH = "/videos/0123456789abcdef0123456789abcdef.mp4"
YT_ID = "cm4youtube00001"


def mp4(seconds: float, version: int = 0) -> bytes:
    """A tiny MP4-like file: ftyp + moov/mvhd with the given duration (timescale 1000)."""
    if version == 1:
        mvhd = b"mvhd" + bytes([1, 0, 0, 0]) + b"\0" * 16 + (1000).to_bytes(4, "big") + int(seconds * 1000).to_bytes(8, "big")
    else:
        mvhd = b"mvhd" + bytes([0, 0, 0, 0]) + b"\0" * 8 + (1000).to_bytes(4, "big") + int(seconds * 1000).to_bytes(4, "big")
    mvhd += b"\0" * 80
    return b"\0\0\0\x18ftypisom" + b"\0" * 12 + (len(mvhd) + 12).to_bytes(4, "big") + b"moov" \
        + (len(mvhd) + 4).to_bytes(4, "big") + mvhd + b"mdat" + b"\x01" * 64


VIDEO_MEDIA = {**MEDIA, "id": "vid-media-1", "name": "clip.mp4", "path": "https://uploads.postiz.com/clip.mp4"}


@pytest.fixture
def video_env(monkeypatch, cards_env):
    monkeypatch.setenv("VIDEO_PUBLIC_URL", VIDEO_PUBLIC)
    monkeypatch.setenv("VIDEO_URL", VIDEO_INTERNAL)


def mock_video(mock, status=200, content=None, ctype="video/mp4"):
    return mock.get(VIDEO_INTERNAL + VIDEO_PATH).mock(return_value=httpx.Response(
        status, content=mp4(31.2) if content is None else content, headers={"content-type": ctype}))


def test_mp4_duration_reads_mvhd_v0_and_v1():
    assert main.mp4_duration(mp4(31.2)) == 31.2
    assert main.mp4_duration(mp4(150, version=1)) == 150
    assert main.mp4_duration(b"not a video") is None


def test_video_is_fetched_uploaded_and_attached_instead_of_the_image(video_env, mock):
    mock_integrations(mock)
    vid = mock_video(mock)
    card = mock_card(mock)
    upload = mock.post(f"{API}/upload").mock(return_value=httpx.Response(201, json=VIDEO_MEDIA))
    create = mock_create(mock, httpx.Response(201, json=[{"postId": "pv", "integration": IG_ID}]))
    r = client.post("/publish", json=item(channel="instagram", image_url=CARD_PUBLIC + CARD_PATH,
                                          video_url=VIDEO_PUBLIC + VIDEO_PATH), headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["media"] == [{"id": "vid-media-1", "path": VIDEO_MEDIA["path"]}]
    assert r.json()["video_note"] is None
    assert vid.called and "authorization" not in vid.calls.last.request.headers
    assert not card.called   # the poster is not attached next to the video
    up = upload.calls.last.request
    assert upload.call_count == 1 and up.headers["authorization"] == SECRET
    assert b'filename="0123456789abcdef0123456789abcdef.mp4"' in up.content
    assert b"Content-Type: video/mp4" in up.content
    post = json.loads(create.calls.last.request.content)["posts"][0]
    assert post["value"][0]["image"] == [{"id": "vid-media-1", "path": VIDEO_MEDIA["path"]}]


def test_youtube_with_a_video_is_accepted(video_env, monkeypatch, mock):
    monkeypatch.setenv("CHANNEL_MAP", json.dumps({"youtube": YT_ID}))
    mock.get(f"{API}/integrations").mock(return_value=httpx.Response(200, json=[
        {"id": YT_ID, "name": "Acme", "identifier": "youtube", "picture": "", "disabled": False, "profile": "acme"}]))
    mock_video(mock)
    mock.post(f"{API}/upload").mock(return_value=httpx.Response(201, json=VIDEO_MEDIA))
    mock_create(mock, httpx.Response(201, json=[{"postId": "py", "integration": YT_ID}]))
    r = client.post("/publish", json=item(channel="youtube", video_url=VIDEO_PUBLIC + VIDEO_PATH), headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "youtube"


def test_video_too_long_for_x_is_left_off_and_the_text_post_goes_out(video_env, mock):
    mock_integrations(mock)
    mock_video(mock, content=mp4(150))
    card = mock_card(mock)
    upload = mock.post(f"{API}/upload").mock(return_value=httpx.Response(201, json=MEDIA))
    create = mock_create(mock, httpx.Response(201, json=[{"postId": "px", "integration": X_ID}]))
    r = client.post("/publish", json=item(channel="x", image_url=CARD_PUBLIC + CARD_PATH,
                                          video_url=VIDEO_PUBLIC + VIDEO_PATH), headers=AUTH)
    assert r.status_code == 200, r.text
    assert "150 s is longer than x takes (140 s)" in r.json()["video_note"]
    # the post goes out as before: text plus the image
    assert card.called and upload.call_count == 1
    assert json.loads(create.calls.last.request.content)["posts"][0]["value"][0]["image"][0]["id"] == MEDIA["id"]


def test_video_over_max_video_mb_is_left_off(video_env, monkeypatch, mock):
    monkeypatch.setenv("MAX_VIDEO_MB", "1")
    mock_integrations(mock)
    mock_video(mock, content=mp4(20) + b"\0" * (1024 * 1024))
    upload = mock.post(f"{API}/upload").mock(return_value=httpx.Response(201, json=MEDIA))
    create = mock_create(mock, httpx.Response(201, json=[{"postId": "pl", "integration": LINKEDIN_ID}]))
    r = client.post("/publish", json=item(video_url=VIDEO_PUBLIC + VIDEO_PATH), headers=AUTH)
    assert r.status_code == 200, r.text
    assert "larger than 1 MB" in r.json()["video_note"] and "MAX_VIDEO_MB=1" in r.json()["video_note"]
    assert not upload.called and create.called


@pytest.mark.parametrize("status,ctype,expect", [(404, "text/plain", "HTTP 404"), (200, "text/html", "not a MP4/MOV")])
def test_video_that_is_gone_or_not_a_video_is_left_off(video_env, mock, status, ctype, expect):
    mock_integrations(mock)
    mock_video(mock, status=status, content=b"<html>", ctype=ctype)
    create = mock_create(mock, httpx.Response(201, json=[{"postId": "pg", "integration": LINKEDIN_ID}]))
    r = client.post("/publish", json=item(video_url=VIDEO_PUBLIC + VIDEO_PATH), headers=AUTH)
    assert r.status_code == 200 and expect in r.json()["video_note"]
    assert create.called


def test_video_service_unreachable_is_502_and_nothing_is_posted(video_env, mock):
    mock_integrations(mock)
    mock.get(VIDEO_INTERNAL + VIDEO_PATH).mock(side_effect=httpx.ConnectError("refused"))
    create = mock_create(mock, httpx.Response(201, json=[]))
    r = client.post("/publish", json=item(video_url=VIDEO_PUBLIC + VIDEO_PATH), headers=AUTH)
    assert r.status_code == 502 and "cannot fetch" in r.json()["detail"]["video_error"]
    assert not create.called


def test_image_only_network_leaves_the_video_off(video_env, monkeypatch, mock):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("CHANNEL_MAP", json.dumps({"pinterest": "pin1"}))
    mock_card(mock)
    r = client.post("/publish", json=item(channel="pinterest", image_url=CARD_PUBLIC + CARD_PATH,
                                          video_url=VIDEO_PUBLIC + VIDEO_PATH), headers=AUTH)
    assert r.status_code == 200, r.text
    assert "takes images only" in r.json()["video_note"]
    assert r.json()["would_upload"]["image_url"] == CARD_PUBLIC + CARD_PATH


def test_dry_run_reports_the_video_upload_without_calling_postiz(video_env, monkeypatch, mock):
    monkeypatch.setenv("DRY_RUN", "true")
    vid = mock_video(mock)
    r = client.post("/publish", json=item(channel="instagram", image_url=CARD_PUBLIC + CARD_PATH,
                                          video_url=VIDEO_PUBLIC + VIDEO_PATH), headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["would_upload"] == {
        "video_url": VIDEO_PUBLIC + VIDEO_PATH, "fetched_from": VIDEO_INTERNAL + VIDEO_PATH,
        "endpoint": f"{API}/upload", "file": "0123456789abcdef0123456789abcdef.mp4",
        "content_type": "video/mp4", "bytes": len(mp4(31.2)), "duration_s": 31.2}
    assert body["video_note"] is None
    assert body["would_send"]["posts"][0]["value"][0]["image"] == [
        {"id": "<id from POST /upload>", "path": "<path from POST /upload>"}]
    assert [c.request.url.host for c in mock.calls] == ["video-assembly"]
    assert vid.called


def test_no_video_url_keeps_the_answer_unchanged(monkeypatch, mock):
    monkeypatch.setenv("DRY_RUN", "true")
    body = client.post("/publish", json=item(video_url=" "), headers=AUTH).json()
    assert "video_note" not in body and body["would_upload"] is None
