import re
import time

import pytest
import respx
from fastapi.testclient import TestClient

# Sentinel values: tests grep every response for them.
KEYS = {
    "CONTROL_ROOM_KEY": "ctl-key-7f3a9c1e5b2d4f6a8c0e",
    "INTERNAL_API_KEY": "int-key-2b4d6f8a0c1e3a5c7e9b",
    "APPROVER_KEY": "appr-key-9e8d7c6b5a4f3e2d1c0b",
}
PASSWORD = "pw-" + "q7Lm2Xv9Rt4Kz8Wn"
URLS = {
    "N8N_BASE_URL": "http://n8n.internal:5678",
    "CALENDAR_URL": "http://calendar.internal:8000",
    "CAMPAIGNS_URL": "http://campaigns.internal:8000",
    "LEARNING_URL": "http://learning.internal:8000",
    "ENGINE_URL": "http://engine.internal:8000",
    "RULES_URL": "http://rules.internal:8000",
    "STATUS_URL": "http://status.internal:8000",
    "CARDS_URL": "http://cards.internal:8000",
    "VIDEO_URL": "http://video.internal:8000",
}
CAL, N8N, ENGINE = URLS["CALENDAR_URL"], URLS["N8N_BASE_URL"], URLS["ENGINE_URL"]
WEBHOOK = f"{N8N}/webhook/mkt-apply-decisions"
HEX = "0123456789abcdef0123456789abcdef"


def item(id, **kw):
    base = {"id": id, "title": f"Post {id}", "channel": "linkedin", "body": f"Body of post {id}.", "status": "in_review",
            "scheduled_at": "2026-10-01T09:00:00Z", "published_at": None, "campaign": None, "campaign_id": None,
            "link": None, "external_url": None, "notes": None, "hook_style": None, "image_url": None,
            "video_url": None, "short_url": None, "created_at": "2026-09-27T10:00:00Z", "updated_at": "2026-09-27T10:00:00Z"}
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {**KEYS, **URLS}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CONTROL_USER", "henos")
    monkeypatch.setenv("CONTROL_PASSWORD", PASSWORD)
    monkeypatch.setenv("N8N_PUBLIC_URL", "https://n8n.example.test/")
    monkeypatch.setenv("UNDO_SECONDS", "30")   # tests flush explicitly


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


@pytest.fixture
def app():
    from app.main import create_app
    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app, follow_redirects=False) as c:
        yield c


def csrf_of(html: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', html) or re.search(r'"X-CSRF-Token": "([^"]+)"', html)
    assert m, "no csrf token in page"
    return m.group(1)


def login(client, password=PASSWORD, user="henos"):
    page = client.get("/login")
    token = csrf_of(page.text)
    return client.post("/login", data={"user": user, "password": password, "csrf": token, "next": "/"})


@pytest.fixture
def authed(client, mock):
    """Logged-in client; returns (client, csrf token)."""
    r = login(client)
    assert r.status_code == 303, r.text
    page = client.get("/more")   # calls no backend
    return client, csrf_of(page.text)


def flush(client, app, later=60):
    return client.portal.call(app.state.pending.flush_due, time.time() + later)
