"""Tests for the CMS bridge.

The mocked CMS shapes follow the WordPress and Ghost docs (September 2026):
- WordPress posts: https://developer.wordpress.org/rest-api/reference/posts/#create-a-post
  -> {id, link, status, ...}; media: https://developer.wordpress.org/rest-api/reference/media/
  -> {id, source_url}; errors {code, message, data: {status}}, e.g. 401 rest_not_logged_in.
- Ghost posts: https://ghost.org/docs/admin-api/#creating-a-post -> {posts: [{id, url, status}]};
  images: https://ghost.org/docs/admin-api/#uploading-an-image -> {images: [{url, ref}]};
  token: https://ghost.org/docs/admin-api/#token-authentication; errors {errors: [{message}]}.
"""
import base64
import json
import time

import httpx
import jwt
import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.main import app

KEY = "bridge-key-123"
AUTH = {"X-API-Key": KEY}
WP = "http://wp.test"
GHOST = "http://ghost.test"
WP_PASSWORD = "abcd EFGH ijkl MNOP qrst UVWX"
GHOST_ID = "6489a5c1f1e2d3001c8b4567"
GHOST_SECRET = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
IMAGE = "http://image-cards:8000/cards/12.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

BODY_MD = (
    "# Autumn pricing is here\n\n"
    "We cut prices for **small teams** & freelancers.\n\n"
    "## What changed\n\n"
    "- Starter: 9 EUR\n- Team: 29 EUR\n\n"
    "Read more: https://s.example/abc\n\n"
    "<script>alert(1)</script>\n"
)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ("CMS_BRIDGE_KEY", "INTERNAL_API_KEY", "CMS", "WP_URL", "WP_USER",
                 "WP_APP_PASSWORD", "WP_STATUS", "GHOST_URL", "GHOST_ADMIN_KEY",
                 "GHOST_STATUS", "DRY_RUN", "ALLOWED_IMAGE_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CMS_BRIDGE_KEY", KEY)
    monkeypatch.setenv("WP_URL", WP + "/")
    monkeypatch.setenv("WP_USER", "editor")
    monkeypatch.setenv("WP_APP_PASSWORD", WP_PASSWORD)
    monkeypatch.setenv("GHOST_URL", GHOST)
    monkeypatch.setenv("GHOST_ADMIN_KEY", f"{GHOST_ID}:{GHOST_SECRET}")
    main._published.clear()


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")


@pytest.fixture
def ghost(monkeypatch):
    monkeypatch.setenv("CMS", "ghost")


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


client = TestClient(app)


def item(**kw):
    # What the publisher (39) sends, plus the optional fields this bridge understands.
    return {"id": 12, "channel": "blog", "title": "Autumn pricing is here", "text": BODY_MD,
            "link": "https://s.example/abc", "short_url": "https://s.example/abc",
            "campaign": "autumn-launch", **kw}


def wp_created(**kw):
    return httpx.Response(201, json={"id": 501, "link": f"{WP}/?p=501", "status": "draft",
                                     "title": {"raw": "Autumn pricing is here"}, **kw})


def ghost_created(**kw):
    post = {"id": "65f0c0ffee", "url": f"{GHOST}/p/65f0c0ffee/", "status": "draft", **kw}
    return httpx.Response(201, json={"posts": [post]})


# ---------- health / config


def test_health_reports_config_without_secrets(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "internal-secret-xyz")
    r = client.get("/health")
    body = r.json()
    assert body["cms"] == "wordpress" and body["dry_run"] is True
    assert body["configured"] is True and body["status"] == "ok"
    assert body["cms_status"] == "draft"
    assert body["allowed_image_hosts"] == ["image-cards", "localhost"]
    for secret in (WP_PASSWORD, GHOST_SECRET, KEY, "internal-secret-xyz", "EFGH"):
        assert secret not in r.text


def test_health_not_configured(monkeypatch):
    monkeypatch.delenv("WP_APP_PASSWORD")
    body = client.get("/health").json()
    assert body["configured"] is False and body["status"] == "degraded"
    assert any("WP_APP_PASSWORD" in e for e in body["config_errors"])


def test_health_bad_ghost_key(monkeypatch, ghost):
    monkeypatch.setenv("GHOST_ADMIN_KEY", "no-colon-here")
    body = client.get("/health").json()
    assert body["cms"] == "ghost" and body["configured"] is False
    assert "no-colon-here" not in json.dumps(body)


def test_dry_run_is_the_default(monkeypatch):
    assert client.get("/health").json()["dry_run"] is True
    monkeypatch.setenv("DRY_RUN", "maybe")
    assert client.get("/health").json()["dry_run"] is True


# ---------- auth and routing


def test_wrong_or_missing_key_is_401():
    assert client.post("/publish", json=item(), headers={"X-API-Key": "nope"}).status_code == 401
    assert client.post("/publish", json=item()).status_code == 401


def test_falls_back_to_internal_api_key(monkeypatch):
    monkeypatch.delenv("CMS_BRIDGE_KEY")
    monkeypatch.setenv("INTERNAL_API_KEY", "shared")
    assert client.post("/publish", json=item(), headers={"X-API-Key": "shared"}).status_code == 200
    monkeypatch.delenv("INTERNAL_API_KEY")
    assert client.post("/publish", json=item(), headers=AUTH).status_code == 503


def test_non_blog_channel_is_422(live, mock):
    r = client.post("/publish", json=item(channel="linkedin"), headers=AUTH)
    assert r.status_code == 422
    assert "'linkedin'" in r.json()["detail"] and "blog" in r.json()["detail"]
    assert not mock.calls


def test_missing_title_or_text_is_422(live, mock):
    assert client.post("/publish", json=item(title=" "), headers=AUTH).status_code == 422
    assert client.post("/publish", json=item(text="", link=None, short_url=None), headers=AUTH).status_code == 422
    assert not mock.calls


# ---------- content


def test_markdown_to_html():
    md = main.build_markdown(BODY_MD, "https://s.example/abc")
    html = main.to_html(md)
    assert "<h1>" not in html  # the title line is dropped: the CMS shows the title
    assert "<p>We cut prices for <strong>small teams</strong> &amp; freelancers.</p>" in html
    assert "<h2>What changed</h2>" in html
    assert "<ul>\n<li>Starter: 9 EUR</li>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html  # raw HTML is escaped
    assert main.excerpt_of(html) == "We cut prices for small teams & freelancers."


def test_link_appended_when_missing():
    md = main.build_markdown("Just text.", "https://s.example/q")
    assert '<a href="https://s.example/q">https://s.example/q</a>' in main.to_html(md)


def test_body_alias_is_accepted(mock):
    r = client.post("/publish", json={"id": 1, "channel": "BLOG", "title": "T", "body": "Hello"},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["would_send"]["body"]["content"] == "<p>Hello</p>\n"


# ---------- dry run


def test_dry_run_default_sends_nothing(mock):
    r = client.post("/publish", json=item(image_url=IMAGE), headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "dry_run" and body["cms"] == "wordpress"
    assert body["external_id"] is None and body["external_url"] is None and body["url"] is None
    assert body["cms_status"] == "draft"
    sent = body["would_send"]
    assert sent["url"] == f"{WP}/wp-json/wp/v2/posts"
    assert sent["body"]["status"] == "draft" and sent["body"]["title"] == "Autumn pricing is here"
    assert sent["featured_image"] == IMAGE and body["warnings"] == []
    assert not mock.calls


def test_dry_run_ghost_without_credentials(monkeypatch, ghost, mock):
    monkeypatch.delenv("GHOST_ADMIN_KEY")
    r = client.post("/publish", json=item(image_url="http://evil.test/x.png"), headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cms"] == "ghost"
    assert body["would_send"]["url"] == f"{GHOST}/ghost/api/admin/posts/?source=html"
    assert body["would_send"]["body"]["posts"][0]["status"] == "draft"
    assert "not in ALLOWED_IMAGE_HOSTS" in body["warnings"][0]
    assert not mock.calls


# ---------- WordPress


def test_wordpress_happy_path(live, mock):
    route = mock.post(f"{WP}/wp-json/wp/v2/posts").mock(return_value=wp_created())
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "created", "cms": "wordpress", "external_id": 501,
                        "external_url": f"{WP}/?p=501", "url": f"{WP}/?p=501",
                        "cms_status": "draft", "warnings": []}
    req = route.calls.last.request
    expected = base64.b64encode(f"editor:{WP_PASSWORD}".encode()).decode()
    assert req.headers["authorization"] == f"Basic {expected}"
    sent = json.loads(req.content)
    assert sent["status"] == "draft" and "date_gmt" not in sent
    assert sent["title"] == "Autumn pricing is here"
    assert sent["content"].startswith("<p>We cut prices")
    assert sent["excerpt"] == "We cut prices for small teams & freelancers."
    assert "featured_media" not in sent


def test_wordpress_future_uses_scheduled_at(monkeypatch, live, mock):
    monkeypatch.setenv("WP_STATUS", "future")
    route = mock.post(f"{WP}/wp-json/wp/v2/posts").mock(return_value=wp_created(status="future"))
    r = client.post("/publish", json=item(scheduled_at="2026-10-01T08:30:00Z"), headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["cms_status"] == "future"
    sent = json.loads(route.calls.last.request.content)
    assert sent["status"] == "future" and sent["date_gmt"] == "2026-10-01T08:30:00"
    assert client.post("/publish", json=item(id=13), headers=AUTH).status_code == 422


def test_wordpress_featured_image(live, mock):
    img = mock.get(IMAGE).mock(return_value=httpx.Response(200, content=PNG,
                                                           headers={"content-type": "image/png"}))
    media = mock.post(f"{WP}/wp-json/wp/v2/media").mock(
        return_value=httpx.Response(201, json={"id": 77, "source_url": f"{WP}/wp-content/12.png"}))
    post = mock.post(f"{WP}/wp-json/wp/v2/posts").mock(return_value=wp_created())
    r = client.post("/publish", json=item(image_url=IMAGE), headers=AUTH)
    assert r.status_code == 200, r.text
    assert img.called
    up = media.calls.last.request
    assert up.content == PNG and up.headers["content-type"] == "image/png"
    assert up.headers["content-disposition"] == 'attachment; filename="12.png"'
    assert json.loads(post.calls.last.request.content)["featured_media"] == 77


def test_image_host_not_allowed_is_skipped_post_still_created(live, mock):
    post = mock.post(f"{WP}/wp-json/wp/v2/posts").mock(return_value=wp_created())
    r = client.post("/publish", json=item(image_url="http://169.254.169.254/latest/meta-data"),
                    headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"
    assert "169.254.169.254" in r.json()["warnings"][0]
    assert "featured_media" not in json.loads(post.calls.last.request.content)
    assert len(mock.calls) == 1  # the image URL was never fetched


@pytest.mark.parametrize("response,reason", [
    (httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}), "not an image"),
    (httpx.Response(302, headers={"location": "http://169.254.169.254/"}), "HTTP 302"),
    (httpx.Response(200, content=b"x" * (5 * 1024 * 1024 + 1), headers={"content-type": "image/png"}),
     "larger than 5 MB"),
])
def test_bad_image_is_skipped(live, mock, response, reason):
    mock.get(IMAGE).mock(return_value=response)
    mock.post(f"{WP}/wp-json/wp/v2/posts").mock(return_value=wp_created())
    r = client.post("/publish", json=item(image_url=IMAGE), headers=AUTH)
    assert r.status_code == 200, r.text
    assert reason in r.json()["warnings"][0]


def test_retry_does_not_create_twice(live, mock):
    route = mock.post(f"{WP}/wp-json/wp/v2/posts").mock(return_value=wp_created())
    assert client.post("/publish", json=item(), headers=AUTH).status_code == 200
    again = client.post("/publish", json=item(), headers=AUTH)
    assert again.json()["status"] == "already_created" and again.json()["external_id"] == 501
    assert route.call_count == 1


@pytest.mark.parametrize("status,body,expect", [
    (401, {"code": "rest_not_logged_in", "message": "You are not currently logged in.",
           "data": {"status": 401}}, "rejected the credentials"),
    (403, {"code": "rest_cannot_create", "message": "Sorry, you are not allowed to create posts.",
           "data": {"status": 403}}, "not allowed to create posts"),
    (500, {"code": "internal_server_error", "message": "Critical error"}, "Critical error"),
    (502, "bad gateway", "bad gateway"),
])
def test_wordpress_errors_are_502(live, mock, status, body, expect):
    resp = httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
    mock.post(f"{WP}/wp-json/wp/v2/posts").mock(return_value=resp)
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 502
    assert r.json()["detail"]["cms_status"] == status
    assert expect in r.json()["detail"]["cms_error"]


def test_cms_unreachable_is_502(live, mock):
    mock.post(f"{WP}/wp-json/wp/v2/posts").mock(side_effect=httpx.ConnectError("refused"))
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 502 and "cannot reach WordPress" in r.json()["detail"]["cms_error"]


def test_live_without_credentials_is_503(monkeypatch, live, mock):
    monkeypatch.delenv("WP_APP_PASSWORD")
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 503 and "WP_APP_PASSWORD" in r.json()["detail"]
    assert not mock.calls


# ---------- Ghost


def test_ghost_happy_path_with_feature_image(live, ghost, mock):
    mock.get(IMAGE).mock(return_value=httpx.Response(200, content=PNG,
                                                     headers={"content-type": "image/png"}))
    upload = mock.post(f"{GHOST}/ghost/api/admin/images/upload/").mock(
        return_value=httpx.Response(201, json={"images": [{"url": f"{GHOST}/content/images/12.png",
                                                           "ref": None}]}))
    route = mock.post(f"{GHOST}/ghost/api/admin/posts/", params={"source": "html"}).mock(
        return_value=ghost_created())
    r = client.post("/publish", json=item(image_url=IMAGE), headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "created", "cms": "ghost", "external_id": "65f0c0ffee",
                        "external_url": f"{GHOST}/p/65f0c0ffee/", "url": f"{GHOST}/p/65f0c0ffee/",
                        "cms_status": "draft", "warnings": []}
    assert b'name="file"; filename="12.png"' in upload.calls.last.request.content
    req = route.calls.last.request
    assert req.headers["accept-version"] == "v5.0"
    assert req.headers["authorization"].startswith("Ghost ")
    post = json.loads(req.content)["posts"][0]
    assert post["status"] == "draft" and post["title"] == "Autumn pricing is here"
    assert "<strong>small teams</strong>" in post["html"]
    assert post["custom_excerpt"] == "We cut prices for small teams & freelancers."
    assert post["feature_image"] == f"{GHOST}/content/images/12.png"


def test_ghost_jwt_structure(ghost):
    now = int(time.time())
    token = main.ghost_token(now=now)
    header = jwt.get_unverified_header(token)
    assert header == {"alg": "HS256", "kid": GHOST_ID, "typ": "JWT"}
    claims = jwt.decode(token, bytes.fromhex(GHOST_SECRET), algorithms=["HS256"],
                        audience="/admin/")
    assert claims == {"iat": now, "exp": now + 300, "aud": "/admin/"}
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, b"wrong", algorithms=["HS256"], audience="/admin/",
                   options={"verify_exp": False})


def test_ghost_scheduled(monkeypatch, live, ghost, mock):
    monkeypatch.setenv("GHOST_STATUS", "scheduled")
    route = mock.post(f"{GHOST}/ghost/api/admin/posts/", params={"source": "html"}).mock(
        return_value=ghost_created(status="scheduled"))
    r = client.post("/publish", json=item(scheduled_at="2026-10-01T08:30:00+02:00"), headers=AUTH)
    assert r.status_code == 200, r.text
    post = json.loads(route.calls.last.request.content)["posts"][0]
    assert post["status"] == "scheduled" and post["published_at"] == "2026-10-01T06:30:00.000Z"


def test_ghost_401_is_502(live, ghost, mock):
    mock.post(f"{GHOST}/ghost/api/admin/posts/", params={"source": "html"}).mock(
        return_value=httpx.Response(401, json={"errors": [{
            "message": "Invalid token", "context": "invalid signature", "type": "UnauthorizedError"}]}))
    r = client.post("/publish", json=item(), headers=AUTH)
    assert r.status_code == 502
    assert r.json()["detail"]["cms_status"] == 401
    assert "Invalid token (invalid signature)" in r.json()["detail"]["cms_error"]


# ---------- secrets never leak


def test_secrets_never_appear_in_any_response(monkeypatch, live, mock):
    echo = f"bad auth {WP_PASSWORD} {GHOST_SECRET}"
    mock.post(f"{WP}/wp-json/wp/v2/posts").mock(side_effect=[
        httpx.Response(401, json={"code": "x", "message": echo}),
        httpx.Response(500, text=echo),
    ])
    mock.post(f"{GHOST}/ghost/api/admin/posts/", params={"source": "html"}).mock(
        return_value=httpx.Response(403, json={"errors": [{"message": echo}]}))
    responses = [
        client.get("/health"),
        client.post("/publish", json=item(), headers=AUTH),
        client.post("/publish", json=item(), headers=AUTH),
        client.post("/publish", json=item(channel="x"), headers=AUTH),
        client.post("/publish", json=item(), headers={"X-API-Key": "bad"}),
    ]
    monkeypatch.setenv("CMS", "ghost")
    responses.append(client.post("/publish", json=item(), headers=AUTH))
    responses.append(client.get("/health"))
    assert {r.status_code for r in responses} >= {200, 401, 422, 502}
    for r in responses:
        for secret in (WP_PASSWORD, GHOST_SECRET, KEY):
            assert secret not in r.text, r.text
