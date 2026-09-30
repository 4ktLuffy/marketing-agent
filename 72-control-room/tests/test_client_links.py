"""Client approval links: owner makes a link + PIN (shown once); the client page /c/<token> needs no
login, resolves and answers only through 19 with token + PIN, shows no internal facts, keys or
internal URLs, and has CSRF, a per-address limit, noindex and no caching. 19 is mocked with respx."""
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app import views

from .conftest import CAL, KEYS, PASSWORD, URLS, csrf_of, item, login

TOKEN = "tOk3n_" + "A" * 37                     # 43 characters, like secrets.token_urlsafe(32)
PIN = "482913"
BRAND = URLS["BRAND_URL"]
CARDS = URLS["CARDS_URL"]
CARD = "3f9c" + "0" * 28
SHA = "a" * 64
SECRET_NOTE = "warnings: no source: award-winning spa; owner-margin 42% is restricted"


def post_item(id=7, **kw):
    base = {"id": id, "title": "Autumn midweek", "channel": "linkedin", "body": "Stay midweek for £180 a night.",
            "body_sha256": SHA, "version": 2, "status": "in_review", "scheduled_at": "2026-10-06T09:00:00Z",
            "image_url": f"{CARDS}/cards/{CARD}.png", "video_url": None,
            "link": f"{CAL}/items/7", "origin": "task-bridge", "notes": SECRET_NOTE, "client_responses": []}
    base.update(kw)
    return base


def resolved(**kw):
    return {"id": 3, "label": "October posts", "expires_at": "2026-10-07T10:00:00Z", "items": [post_item(**kw)]}


@pytest.fixture
def nineteen(mock):
    """19's client-link endpoints: the right token + PIN resolve; anything else is a wrong PIN."""
    calls = {"resolve": [], "respond": []}

    def resolve(request):
        body = json.loads(request.content)
        calls["resolve"].append((body, dict(request.headers)))
        if body["token"] != TOKEN:
            return httpx.Response(404, json={"detail": "no such link"})
        if body["pin"] != PIN:
            return httpx.Response(403, json={"detail": {"message": "wrong PIN: 4 tries left", "tries_left": 4}})
        return httpx.Response(200, json=resolved())

    def respond(request):
        body = json.loads(request.content)
        calls["respond"].append(body)
        return httpx.Response(200, json={"item_id": body["item_id"], "decision": body["decision"],
                                         "status": "in_review", "version": 2, "body_sha256": SHA})

    mock.post(f"{CAL}/client-links/resolve").mock(side_effect=resolve)
    mock.post(f"{CAL}/client-links/respond").mock(side_effect=respond)
    mock.get(f"{BRAND}/brand/editable").respond(json={"name": "Lake Ember Lodge"})
    return calls


def open_with_pin(c, pin=PIN, token=TOKEN):
    page = c.get(f"/c/{token}")
    assert page.status_code == 200, page.text
    return c.post(f"/c/{token}", data={"csrf": csrf_of(page.text), "pin": pin})


def signed_in(c):
    r = open_with_pin(c)
    assert r.status_code == 303 and r.headers["location"] == f"/c/{TOKEN}"
    page = c.get(f"/c/{TOKEN}")
    assert page.status_code == 200, page.text
    return page


def answer(c, page, **kw):
    data = {"csrf": csrf_of(page.text), "item_id": "7", "body_sha256": SHA, "decision": "approve",
            "name": "Dana Client", "comment": "", **kw}
    return c.post(f"/c/{TOKEN}/respond", data=data)


def no_leaks(text: str):
    for secret in [*KEYS.values(), PASSWORD]:
        assert secret not in text
    for url in URLS.values():
        assert url.split("//")[1].split(":")[0] not in text, url
    assert "X-API-Key" not in text and "owner-margin" not in text and "award-winning" not in text
    assert "henos" not in text   # the owner's user name


# ---------- the client page (no login)


def test_client_page_needs_no_login_and_asks_for_the_pin(client, nineteen):
    r = client.get(f"/c/{TOKEN}")
    assert r.status_code == 200 and 'name="pin"' in r.text
    assert "noindex" in r.headers["X-Robots-Tag"] and r.headers["Cache-Control"] == "no-store"
    assert '<meta name="robots" content="noindex' in r.text and "<script" not in r.text
    assert nineteen["resolve"] == []   # nothing is read before the PIN
    assert "/c/" in r.headers["set-cookie"] and "httponly" in r.headers["set-cookie"].lower()


def test_malformed_token_is_404(client, nineteen):
    assert client.get("/c/short").status_code == 404
    assert client.get("/c/" + "x" * 65).status_code == 404


def test_right_pin_shows_the_posts_and_nothing_internal(client, nineteen):
    page = signed_in(client)
    t = page.text
    assert "Stay midweek for £180 a night." in t and "Lake Ember Lodge" in t and "October posts" in t
    assert 'data-label="no_source"' in t and "no source" in t          # the evidence label only
    assert f'src="/c/m/cards/{CARD}.png"' in t                          # media via this link only
    assert 'name="body_sha256" value="' + SHA in t
    no_leaks(t)
    body, headers = nineteen["resolve"][-1]
    assert body == {"token": TOKEN, "pin": PIN} and headers["x-api-key"] == KEYS["INTERNAL_API_KEY"]
    assert "noindex" in page.headers["X-Robots-Tag"] and page.headers["Cache-Control"] == "no-store"


def test_client_item_keeps_only_labels_text_and_safe_urls():
    view, media = views_client(post_item(link="https://lakeember.example/offer", video_url=None))
    assert view["link"] == "https://lakeember.example/offer" and media == {("cards", f"{CARD}.png")}
    assert set(view["chips"][0]) == {"label", "words", "level"}
    view, media = views_client(post_item(image_url="http://10.0.0.5:8117/x.png", link="http://intranet/x", origin=None))
    assert view["image"] is None and view["link"] is None and view["chips"] == [] and media == set()
    view, _ = views_client(post_item(image_url="https://cdn.example.com/a.png", origin="task-bridge", notes=None))
    assert view["image"] == "https://cdn.example.com/a.png"


def views_client(it):
    from app.client_links import client_item
    return client_item(it, [CAL, CARDS])


def test_wrong_pin_message_and_no_session(client, nineteen):
    r = open_with_pin(client, pin="000000")
    assert r.status_code == 401 and "That PIN is not right. 4 more wrong tries and the link locks." in r.text
    assert "cr_client=" not in r.headers.get("set-cookie", "")
    assert 'name="pin"' in client.get(f"/c/{TOKEN}").text   # still the PIN form


def test_pin_must_be_6_digits_and_is_not_sent(client, nineteen):
    r = open_with_pin(client, pin="12ab")
    assert r.status_code == 422 and "6 digits" in r.text and nineteen["resolve"] == []


def test_rate_limit_per_address(monkeypatch, nineteen):
    monkeypatch.setenv("CLIENT_PIN_MAX_FAILURES", "3")
    from app.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        for _ in range(3):
            assert open_with_pin(c, pin="000000").status_code == 401
        sent = len(nineteen["resolve"])
        r = open_with_pin(c)            # even the right PIN waits now
        assert r.status_code == 429 and "Too many wrong tries" in r.text and "Retry-After" in r.headers
        assert len(nineteen["resolve"]) == sent


def test_unknown_link_counts_as_a_failure(monkeypatch, nineteen):
    monkeypatch.setenv("CLIENT_PIN_MAX_FAILURES", "2")
    from app.main import create_app
    other = "B" * 43
    with TestClient(create_app(), follow_redirects=False) as c:
        for _ in range(2):
            r = open_with_pin(c, token=other)
            assert r.status_code == 404 and "does not exist" in r.text
        assert open_with_pin(c, token=other).status_code == 429


def test_pin_form_needs_its_csrf_cookie(client, nineteen):
    client.get(f"/c/{TOKEN}")
    assert client.post(f"/c/{TOKEN}", data={"csrf": "forged", "pin": PIN}).status_code == 403
    fresh = TestClient(client.app, follow_redirects=False)
    assert fresh.post(f"/c/{TOKEN}", data={"csrf": "x" * 43, "pin": PIN}).status_code == 403
    assert nineteen["resolve"] == []


@pytest.mark.parametrize("status,detail,words", [
    (410, "this link has expired", "expired or was withdrawn"),
    (410, "this link was withdrawn", "expired or was withdrawn"),
    (409, "this link is locked after too many wrong PINs", "locked"),
])
def test_ended_links(client, mock, status, detail, words):
    mock.post(f"{CAL}/client-links/resolve").respond(status, json={"detail": detail})
    r = open_with_pin(client)
    assert r.status_code == status and words in r.text and 'name="pin"' not in r.text
    no_leaks(r.text)


def test_link_that_ends_while_open_drops_the_session(client, nineteen, mock):
    signed_in(client)
    mock.post(f"{CAL}/client-links/resolve").respond(410, json={"detail": "this link was withdrawn"})
    r = client.get(f"/c/{TOKEN}")
    assert r.status_code == 410 and "withdrawn" in r.text


# ---------- answering


def test_approve_goes_to_19_with_token_pin_and_the_hash_seen(client, nineteen):
    page = signed_in(client)
    r = answer(client, page, name="  Dana   Client ")
    assert r.status_code == 303 and r.headers["location"].startswith(f"/c/{TOKEN}?answered=7&d=approve")
    assert nineteen["respond"] == [{"token": TOKEN, "pin": PIN, "item_id": 7, "body_sha256": SHA,
                                    "decision": "approve", "name": "Dana Client", "comment": ""}]
    done = client.get(f"/c/{TOKEN}?answered=7&d=approve")
    assert "your answer on post #7 was recorded" in done.text


def test_request_changes_needs_a_comment_and_name(client, nineteen):
    page = signed_in(client)
    r = answer(client, page, decision="changes", comment="")
    assert r.status_code == 422 and "Say what should change." in r.text
    r = answer(client, page, name=" ")
    assert r.status_code == 422 and "Write your name" in r.text
    assert nineteen["respond"] == []
    r = answer(client, page, decision="changes", comment="Say 2 sharing.")
    assert r.status_code == 303 and nineteen["respond"][-1]["comment"] == "Say 2 sharing."


def test_hash_mismatch_shows_the_new_text(client, nineteen, mock):
    page = signed_in(client)
    mock.post(f"{CAL}/client-links/respond").respond(
        409, json={"detail": {"message": "the text changed since the link was opened", "current_sha256": "b" * 64}})
    r = answer(client, page)
    assert r.status_code == 409 and "The text changed since you opened the link" in r.text
    no_leaks(r.text)


def test_respond_needs_the_page_csrf(client, nineteen):
    page = signed_in(client)
    r = client.post(f"/c/{TOKEN}/respond", data={"csrf": "forged", "item_id": "7", "body_sha256": SHA,
                                                 "decision": "approve", "name": "Dana"})
    assert r.status_code == 403 and nineteen["respond"] == []
    assert answer(client, page, decision="publish").status_code == 422
    assert answer(client, page, body_sha256="nothex").status_code == 422


def test_respond_without_pin_goes_back_to_the_pin_form(client, nineteen):
    r = client.post(f"/c/{TOKEN}/respond", data={"csrf": "x", "item_id": "7", "body_sha256": SHA,
                                                 "decision": "approve", "name": "Dana"})
    assert r.status_code == 303 and r.headers["location"] == f"/c/{TOKEN}" and nineteen["respond"] == []


def test_client_session_is_for_one_link_only(client, nineteen):
    signed_in(client)
    other = "C" * 43
    r = client.get(f"/c/{other}")
    assert 'name="pin"' in r.text and len(nineteen["resolve"]) == 2   # no resolve with the stored PIN


def test_client_has_no_owner_powers_and_owner_has_no_client_shortcut(client, nineteen, mock):
    signed_in(client)
    for p in ("/", "/client-links", "/client-links/new", "/items/7", "/more"):
        r = client.get(p)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), p
    owner = TestClient(client.app, follow_redirects=False)
    assert login(owner).status_code == 303
    assert 'name="pin"' in owner.get(f"/c/{TOKEN}").text   # logging in does not open a client link


def test_media_only_for_this_links_items(client, nineteen, mock):
    assert client.get(f"/c/m/cards/{CARD}.png").status_code == 404        # no PIN yet
    signed_in(client)
    mock.get(f"{CARDS}/cards/{CARD}.png").respond(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    r = client.get(f"/c/m/cards/{CARD}.png")
    assert r.status_code == 200 and r.content == b"\x89PNG" and r.headers["Cache-Control"] == "no-store"
    assert client.get(f"/c/m/cards/{'f' * 32}.png").status_code == 404   # not in this link


# ---------- owner side


@pytest.fixture
def owner(authed, mock):
    c, token = authed
    mock.get(f"{CAL}/items").respond(json=[item(7, title="Autumn midweek"), item(8, status="draft")])
    return c, token, mock


def test_owner_pages_need_login(client, mock):
    for p in ("/client-links", "/client-links/new"):
        r = client.get(p)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.post("/client-links/new", data={"item_id": "7"}).status_code == 401
    assert client.post("/client-links/3/revoke").status_code == 401


def test_make_link_shows_link_and_pin_once(owner, monkeypatch):
    c, token, mock = owner
    made = {}

    def create(request):
        made.update(json.loads(request.content))
        return httpx.Response(201, json={"id": 3, "token": TOKEN, "label": made["label"], "item_ids": made["item_ids"],
                                         "expires_at": "2026-10-07T10:00:00Z", "state": "active", "uses": 0})
    mock.post(f"{CAL}/client-links").mock(side_effect=create)
    form = c.get("/client-links/new?items=7")
    assert 'value="7" checked' in form.text and 'value="8" checked' not in form.text and 'value="8"' in form.text
    r = c.post("/client-links/new", data={"csrf": token, "item_id": ["7", "8"], "label": "October posts", "days": "7"})
    assert r.status_code == 201, r.text
    pin = made["pin"]
    assert re.fullmatch(r"\d{6}", pin) and made["item_ids"] == [7, 8] and made["days"] == 7
    assert f"http://testserver/c/{TOKEN}" in r.text and f'value="{pin}"' in r.text
    assert "different routes" in r.text and "not a legal signature" in r.text
    no_leaks_owner(r.text)
    # never again: the list has neither
    mock.get(f"{CAL}/client-links").respond(json=[{"id": 3, "label": "October posts", "item_ids": [7, 8],
                                                   "expires_at": "2026-10-07T10:00:00Z", "state": "active",
                                                   "uses": 1, "wrong_pins": 0, "created_by": "henos"}])
    listed = c.get("/client-links")
    assert listed.status_code == 200 and "October posts" in listed.text
    assert TOKEN not in listed.text and pin not in listed.text


def no_leaks_owner(text):
    for secret in [*KEYS.values(), PASSWORD]:
        assert secret not in text
    for url in URLS.values():
        assert url.split("//")[1].split(":")[0] not in text, url


def test_public_url_setting_is_used(monkeypatch, mock):
    monkeypatch.setenv("CONTROL_PUBLIC_URL", "https://review.agency.example/")
    from app.main import create_app
    mock.post(f"{CAL}/client-links").respond(201, json={"id": 1, "token": TOKEN, "item_ids": [7],
                                                        "expires_at": "2026-10-07T10:00:00Z"})
    with TestClient(create_app(), follow_redirects=False) as c:
        assert login(c).status_code == 303
        tok = csrf_of(c.get("/more").text)
        r = c.post("/client-links/new", data={"csrf": tok, "item_id": "7", "label": "", "days": "3"})
        assert f"https://review.agency.example/c/{TOKEN}" in r.text


def test_make_link_validates_before_calling_19(owner):
    c, token, mock = owner
    route = mock.post(f"{CAL}/client-links").respond(201, json={})
    assert c.post("/client-links/new", data={"csrf": token, "label": "x", "days": "7"}).status_code == 422
    assert c.post("/client-links/new", data={"csrf": token, "item_id": "7", "days": "5"}).status_code == 422
    assert c.post("/client-links/new", data={"csrf": token, "item_id": "7", "days": "7", "label": "x" * 81}).status_code == 422
    assert c.post("/client-links/new", data={"item_id": "7", "days": "7"}).status_code == 403   # CSRF
    assert not route.called


def test_revoke(owner):
    c, token, mock = owner
    route = mock.post(f"{CAL}/client-links/3/revoke").respond(json={"id": 3, "state": "revoked"})
    assert c.post("/client-links/3/revoke", data={"csrf": "wrong"}).status_code == 403
    r = c.post("/client-links/3/revoke", data={"csrf": token})
    assert r.status_code == 303 and r.headers["location"] == "/client-links" and route.called
    assert route.calls.last.request.headers["x-api-key"] == KEYS["INTERNAL_API_KEY"]


# ---------- queue chips from notes


def test_client_signoff_from_notes():
    ok = {"notes": "[2026-09-30T10:00:00Z] client approved by Dana Client (v2)", "version": 2}
    assert views.client_signoff(ok) == {"approved": True, "name": "Dana Client", "version": 2, "earlier": False}
    changes = {"notes": "[2026-09-30T10:00:00Z] in_review -> draft: client requested changes (Sam, v1): say 2 sharing",
               "version": 2}
    assert views.client_signoff(changes) == {"approved": False, "name": "Sam", "version": 1, "earlier": True}
    both = {"notes": ok["notes"] + "\n" + changes["notes"], "version": 2}
    assert views.client_signoff(both)["approved"] is False     # the latest answer counts
    assert views.client_signoff({"notes": "approved by Henos"}) is None
    assert views.client_signoff({"notes": None}) is None


def test_queue_card_shows_client_chips(authed, mock):
    c, _ = authed
    mock.get(f"{CAL}/items").respond(json=[
        item(1, notes="[2026-09-30T10:00:00Z] client approved by Dana (v1)", version=1),
        item(2, notes="[2026-09-30T10:00:00Z] client requested changes (Sam, v1): shorter", version=2)])
    t = c.get("/").text
    assert "client approved by Dana" in t and 'data-client="approved"' in t
    assert "client asked for changes (Sam)" in t and "earlier version (v1)" in t
    assert 'href="/client-links/new?items=1"' in t


def test_status_words_follow_the_clients_own_answer_on_this_version():
    from app.client_links import client_item
    fresh, _ = client_item(post_item(), [])
    assert fresh["status_words"] == "waiting for your answer"
    ok, _ = client_item(post_item(client_responses=[{"name": "Maya", "action": "client_approved", "version": 2}]), [])
    assert ok["status_words"] == "you approved this version"
    ch, _ = client_item(post_item(client_responses=[{"name": "Maya", "action": "client_changes_requested", "version": 2}]), [])
    assert ch["status_words"] == "you asked for changes"
    old, _ = client_item(post_item(client_responses=[{"name": "Maya", "action": "client_approved", "version": 1}]), [])
    assert old["status_words"] == "waiting for your answer"   # an answer on an older version doesn't count
