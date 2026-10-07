from fastapi.testclient import TestClient

from .conftest import CAL, PASSWORD, csrf_of, login


def test_health_needs_no_login(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_pages_redirect_to_login(client):
    for path in ["/", "/calendar", "/performance", "/engine", "/campaigns", "/chat", "/status", "/items/3"]:
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"].startswith("/login?next="), path


def test_htmx_and_api_calls_get_401(client):
    assert client.get("/results", headers={"HX-Request": "true"}).headers["HX-Redirect"] == "/login"
    assert client.get("/calendar/events").status_code == 401
    assert client.post("/decide", data={"id": 1, "decision": "approve"}).status_code == 401


def test_login_sets_strict_httponly_cookie(client):
    r = login(client)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = next(c for c in r.headers.get_list("set-cookie") if c.startswith("cr_session="))
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" not in cookie


def test_cookie_is_secure_over_https(app):
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        r = login(c)
        cookie = next(x for x in r.headers.get_list("set-cookie") if x.startswith("cr_session="))
        assert "Secure" in cookie
        assert "max-age" in r.headers["Strict-Transport-Security"]


def test_wrong_password_then_lockout(client):
    for _ in range(5):
        assert login(client, password="nope").status_code == 401
    r = login(client)                     # right password, but locked out
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert "cr_session" not in r.headers.get("set-cookie", "")


def test_lockout_expires(client, app):
    clock = [1000.0]
    app.state.limiter.clock = lambda: clock[0]
    for _ in range(5):
        login(client, password="nope")
    assert login(client).status_code == 429
    clock[0] += 15 * 60 + 1
    assert login(client).status_code == 303


def test_wrong_user_fails(client):
    assert login(client, user="someone").status_code == 401


def test_login_needs_the_login_csrf_cookie(client):
    token = csrf_of(client.get("/login").text)
    client.cookies.clear()
    r = client.post("/login", data={"user": "alex", "password": PASSWORD, "csrf": token})
    assert r.status_code == 403


def test_no_password_configured_refuses_login(monkeypatch):
    monkeypatch.setenv("CONTROL_PASSWORD", "")
    from app.main import create_app
    with TestClient(create_app(), follow_redirects=False) as c:
        r = login(c, password="")
        assert r.status_code == 503 and "CONTROL_PASSWORD" in r.text


def test_csrf_missing_or_wrong_is_403(authed):
    c, token = authed
    assert c.post("/decide", data={"id": 1, "decision": "approve"}).status_code == 403
    assert c.post("/decide", data={"id": 1, "decision": "approve", "csrf": "x" * 40}).status_code == 403
    assert c.post("/decide", data={"id": 1, "decision": "approve"}, headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert c.post("/engine/1/resume").status_code == 403
    assert c.post("/calendar/reschedule", json={"id": 1, "start": "2026-10-01T09:00:00Z"}).status_code == 403
    assert c.post("/logout").status_code == 403


def test_session_expires_when_idle_and_absolutely(client, app, mock):
    clock = [5000.0]
    app.state.sessions.clock = lambda: clock[0]
    login(client)
    mock.get(f"{CAL}/items").respond(json=[])
    assert client.get("/").status_code == 200
    clock[0] += 119 * 60                 # idle limit 120 min: still in
    assert client.get("/").status_code == 200
    clock[0] += 121 * 60                 # idle too long
    assert client.get("/").status_code == 303
    login(client)
    for _ in range(7):                   # active, but past SESSION_HOURS (12)
        clock[0] += 110 * 60
        client.get("/more")
    assert client.get("/more").status_code == 303


def test_logout_ends_the_session(authed):
    c, token = authed
    assert c.post("/logout", data={"csrf": token}).status_code == 303
    assert c.get("/more").status_code == 303


def test_new_session_on_login(client):
    login(client)
    first = client.cookies.get("cr_session")
    login(client)
    assert client.cookies.get("cr_session") != first


def test_open_redirect_blocked(client):
    token = csrf_of(client.get("/login").text)
    r = client.post("/login", data={"user": "alex", "password": PASSWORD, "csrf": token, "next": "//evil.test/x"})
    assert r.headers["location"] == "/"


def test_security_headers(client):
    r = client.get("/login")
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Cache-Control"] == "no-store"
