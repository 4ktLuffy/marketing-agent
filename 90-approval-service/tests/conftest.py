"""The real content calendar (19) runs in-process on a temporary database; the learning service
(46) and content engine (61) are small fakes that record what they receive.

19 is loaded from the sibling folder ../19-content-calendar (the monorepo). In a checkout of this
service alone it is not there, and the flow tests are skipped with that reason.
"""
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main

KEY = "internal-test-key-0001"
APPROVER = "approver-test-key-0002"
CONTROL = "control-room-test-key-0003"
SECRETS = (KEY, APPROVER, CONTROL)
CAL, LEARN, ENGINE = "http://calendar.test", "http://learning.test", "http://engine.test"
CONTROL_H = {"X-Control-Key": CONTROL}
CAL_H = {"X-API-Key": KEY, "X-Approver-Key": APPROVER}

CALENDAR_DIR = Path(__file__).resolve().parents[2] / "19-content-calendar"


def load_calendar():
    """19's `app` package under another name (this service's package is also called `app`)."""
    if "calendar19.main" in sys.modules:
        return sys.modules["calendar19.main"]
    init = CALENDAR_DIR / "app" / "__init__.py"
    if not init.exists():
        return None
    spec = importlib.util.spec_from_file_location("calendar19", init,
                                                  submodule_search_locations=[str(init.parent)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["calendar19"] = pkg
    spec.loader.exec_module(pkg)
    import calendar19.main  # noqa: E402  (its client_links router imports .main relatively)
    return calendar19.main


@dataclass
class Fake:
    """46 or 61: records each call; `status` is what it answers (None: unreachable)."""
    status: int | None = 201
    calls: list = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.status is None:
            raise httpx.ConnectError("down", request=request)
        self.calls.append({"path": request.url.path, "body": json.loads(request.content),
                           "headers": dict(request.headers)})
        return httpx.Response(self.status, json={})


class Down(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        raise httpx.ConnectError("down", request=request)


class Router(httpx.AsyncBaseTransport):
    """calendar.test -> 19 in-process (ASGI); learning.test and engine.test -> fakes."""

    def __init__(self, calendar_app, learning: Fake, engine: Fake):
        self.cal = httpx.ASGITransport(app=calendar_app)
        self.learning, self.engine = learning, engine
        self.calendar_calls: list[httpx.Request] = []
        self.calendar_down = False
        self.override: dict[tuple[str, str], httpx.Response] = {}  # (method, path) -> answer instead of 19

    async def handle_async_request(self, request):
        host = request.url.host
        if host == "calendar.test":
            if self.calendar_down:
                raise httpx.ConnectError("down", request=request)
            self.calendar_calls.append(request)
            if (request.method, request.url.path) in self.override:
                return self.override[(request.method, request.url.path)]
            return await self.cal.handle_async_request(request)
        if host == "learning.test":
            return self.learning.handle(request)
        if host == "engine.test":
            return self.engine.handle(request)
        raise AssertionError(f"unexpected call to {request.url}")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for k in ("LEARNING_URL", "ENGINE_URL", "REQUEST_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("APPROVER_KEY", APPROVER)
    monkeypatch.setenv("CONTROL_ROOM_KEY", CONTROL)
    monkeypatch.setenv("CALENDAR_URL", CAL)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "calendar.sqlite"))  # 19's database
    # Nothing leaves the process: without the `stack` fixture every service is down.
    monkeypatch.setattr(main, "TRANSPORT", Down())


@pytest.fixture
def client():
    return TestClient(main.app)


@dataclass
class Stack:
    cal: TestClient
    router: Router
    learning: Fake
    engine: Fake

    def create(self, status="in_review", **kw):
        payload = {"title": "Post", "channel": "linkedin", "body": "We launched.", "status": status, **kw}
        r = self.cal.post("/items", json=payload, headers=CAL_H)
        assert r.status_code == 201, r.text
        return r.json()

    def item(self, item_id):
        return self.cal.get(f"/items/{item_id}").json()

    def audit(self, item_id):
        return self.cal.get(f"/items/{item_id}/audit", headers=CAL_H).json()


@pytest.fixture
def stack(monkeypatch):
    cal_mod = load_calendar()
    if cal_mod is None:
        pytest.skip(f"19-content-calendar not found at {CALENDAR_DIR} (flow tests need the monorepo)")
    learning, engine = Fake(), Fake()
    router = Router(cal_mod.app, learning, engine)
    monkeypatch.setattr(main, "TRANSPORT", router)
    monkeypatch.setenv("LEARNING_URL", LEARN)
    monkeypatch.setenv("ENGINE_URL", ENGINE)
    return Stack(TestClient(cal_mod.app), router, learning, engine)
