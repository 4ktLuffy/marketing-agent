"""Fakes for 05, 14, 19, 44 and 80, written from the phase-1 contracts (respx; any call that is not
mocked fails the test). The fake 05 query is its own small implementation of contract section 1,
not the service's code, so a disagreement shows up as a failing test."""
import hashlib
import json
from dataclasses import dataclass, field

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app

from . import data

KEY = "internal-secret-key-123"
AUTH = {"X-API-Key": KEY}
BRAND, RULES, CAL, CLAIMS, LEADS, GATEWAY = ("http://brand.test", "http://rules.test", "http://calendar.test",
                                             "http://claims.test", "http://leads.test", "http://gateway.test")
DIMS = {"site": "sites", "region": "regions", "channel": "channels", "segment": "segments",
        "plan_tier": "plan_tiers", "variant": "variants"}
RANK = {"public": 0, "internal": 1, "restricted": 2}


def canon(body: str) -> str:
    return hashlib.sha256(body.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


@dataclass
class Stack:
    facts: list = field(default_factory=list)
    summary: str = "Brand: Test brand - friendly\nTone: warm, plain"
    changes: list = field(default_factory=list)
    items: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
    posted: list = field(default_factory=list)
    status_calls: list = field(default_factory=list)
    verify_calls: list = field(default_factory=list)
    verify_answer: list = field(default_factory=list)
    brand_violations: list = field(default_factory=list)
    queries: list = field(default_factory=list)

    def fact(self, key):
        return next(f for f in self.facts if f["key"] == key)

    def change(self, key, kind="confirmed"):
        seq = len(self.changes) + 1
        self.changes.append({"seq": seq, "key": key, "version": self.fact(key)["version"], "kind": kind,
                             "at": "2026-09-30T10:00:00Z"})

    def approve(self, item_id, body_sha256=None):
        it = self.items[item_id]
        it["status"] = "approved"
        self.audit[item_id].append({"at": "2026-09-30T09:00:00Z", "actor": "approver", "action": "status",
                                    "from_status": "in_review", "to_status": "approved",
                                    "body_sha256": body_sha256 or it["body_sha256"], "detail": None})


def query(stack: Stack, request: httpx.Request):
    p = request.url.params
    stack.queries.append(str(request.url))
    at = p.get("at") or data.TODAY
    max_s = p.get("max_sensitivity") or "internal"
    scope = {dim: [v.lower() for v in p.get_list(name)] for name, dim in DIMS.items()}
    out, excluded = [], []
    for f in stack.facts:
        if f["status"] != "active":
            excluded.append({"key": f["key"], "reason": f["status"]})
            continue
        if RANK[f["sensitivity"]] > RANK[max_s]:
            excluded.append({"key": f["key"], "reason": f["sensitivity"]})
            continue
        if f["valid_from"] and at < f["valid_from"]:
            excluded.append({"key": f["key"], "reason": "not_yet_valid"})
            continue
        if f["valid_to"] and at > f["valid_to"]:
            excluded.append({"key": f["key"], "reason": "expired"})
            continue
        reason = None
        for dim, values in f["scope"].items():
            if not values:
                continue
            if not scope[dim]:
                reason = reason or "scope_unspecified"
            elif any(v not in values for v in scope[dim]):
                reason = "out_of_scope"
        if reason:
            excluded.append({"key": f["key"], "reason": reason})
            continue
        out.append(f)
    version = f"fs-{len(stack.changes) + 3}-{hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()[:8]}"
    return httpx.Response(200, json={"fact_set_version": version, "at": at, "facts": out, "excluded": excluded})


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for k in ("BRAND_URL", "RULES_URL", "CALENDAR_URL", "CLAIMS_URL", "LEADS_URL", "MODEL_CHECK", "GATEWAY_URL",
              "PACK_MAX_CHARS", "PASTE_MAX_CHARS", "RATE_PER_MIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tasks.sqlite"))
    monkeypatch.setenv("RECONCILE_MIN", "0")
    from app import main
    main._hits.clear()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as m:
        yield m


@pytest.fixture
def stack(monkeypatch, mock):
    s = Stack()
    for k, v in (("BRAND_URL", BRAND), ("RULES_URL", RULES), ("CALENDAR_URL", CAL), ("CLAIMS_URL", CLAIMS),
                 ("LEADS_URL", LEADS)):
        monkeypatch.setenv(k, v)

    def key_ok(request):
        return request.headers.get("x-api-key") == KEY

    def guarded(fn):
        def inner(request, **kw):
            if not key_ok(request):
                return httpx.Response(401, json={"detail": "missing or wrong X-API-Key"})
            return fn(request, **kw)
        return inner

    # 05 brand service
    mock.get(f"{BRAND}/facts/query").mock(side_effect=guarded(lambda r: query(s, r)))
    mock.get(f"{BRAND}/facts/v2").mock(side_effect=guarded(lambda r: httpx.Response(200, json={"facts": s.facts})))
    mock.get(f"{BRAND}/profile/summary").mock(side_effect=guarded(lambda r: httpx.Response(200, json={"summary": s.summary})))
    mock.post(f"{BRAND}/check").mock(side_effect=guarded(lambda r: httpx.Response(
        200, json={"ok": not any(v["severity"] == "error" for v in s.brand_violations), "violations": s.brand_violations})))
    mock.get(f"{BRAND}/questions").mock(side_effect=guarded(lambda r: httpx.Response(200, json=[])))

    def changes(r):
        since = int(r.url.params.get("since") or 0)
        seq = max([c["seq"] for c in s.changes] + [0])
        return httpx.Response(200, json={"seq": seq, "changes": [c for c in s.changes if c["seq"] > since]})
    mock.get(f"{BRAND}/facts/changes").mock(side_effect=guarded(changes))

    # 14 platform rules
    rules = {"linkedin": {"limit": 3000}, "instagram": {"limit": 2200, "max_hashtags": 30}, "x": {"limit": 280}}
    mock.get(f"{RULES}/rules").mock(return_value=httpx.Response(200, json={"channels": rules}))

    def validate(r):
        body = json.loads(r.content)
        rule = rules.get(body["channel"])
        if rule is None:
            return httpx.Response(422, json={"detail": "unknown channel"})
        v = [{"rule": "too_long", "detail": "too long", "severity": "error"}] if len(body["text"]) > rule["limit"] else []
        return httpx.Response(200, json={"ok": not v, "violations": v})
    mock.post(f"{RULES}/validate").mock(side_effect=validate)

    # 19 content calendar
    def create(r):
        body = json.loads(r.content)
        s.posted.append(body)
        iid = len(s.items) + 1
        text = body["body"].strip()
        s.items[iid] = {"id": iid, "title": body["title"], "channel": body["channel"], "body": text,
                        "status": body.get("status", "draft"), "notes": body.get("notes"),
                        "origin": body.get("origin"), "require_bound": body.get("require_bound_approval", False),
                        "body_sha256": canon(text), "version": 1}
        s.audit[iid] = [{"at": "2026-09-29T10:00:00Z", "actor": "api", "action": "create", "from_status": None,
                         "to_status": s.items[iid]["status"], "body_sha256": canon(text), "detail": None}]
        return httpx.Response(201, json=s.items[iid])

    def get(r, iid):
        it = s.items.get(int(iid))
        return httpx.Response(200, json=it) if it else httpx.Response(404, json={"detail": "not found"})

    def status(r, iid):
        body = json.loads(r.content)
        it = s.items[int(iid)]
        s.status_calls.append({"id": int(iid), **body, "approver": r.headers.get("x-approver-key")})
        if body["status"] in ("approved", "published"):
            return httpx.Response(403, json={"detail": "approving needs the approver key"})
        allowed = {"draft": ["in_review", "rejected"], "in_review": ["approved", "draft", "rejected"],
                   "approved": ["published", "draft"], "rejected": ["draft"], "published": []}[it["status"]]
        if body["status"] not in allowed:
            return httpx.Response(409, json={"detail": {"message": "cannot move"}})
        s.audit[int(iid)].append({"at": "2026-09-30T11:00:00Z", "actor": "api", "action": "status",
                                  "from_status": it["status"], "to_status": body["status"],
                                  "body_sha256": it["body_sha256"], "detail": body.get("note")})
        it["status"] = body["status"]
        it["notes"] = (it["notes"] or "") + f"\n[2026-09-30T11:00:00Z] -> {body['status']}: {body.get('note')}"
        return httpx.Response(200, json=it)

    def notes(r, iid):
        it = s.items[int(iid)]
        it["notes"] = (it["notes"] or "") + "\n" + json.loads(r.content)["note"]
        return httpx.Response(200, json=it)

    mock.post(f"{CAL}/items").mock(side_effect=guarded(create))
    mock.get(url__regex=rf"^{CAL}/items/(?P<iid>\d+)$").mock(side_effect=get)
    mock.get(url__regex=rf"^{CAL}/items/(?P<iid>\d+)/audit$").mock(
        side_effect=guarded(lambda r, iid: httpx.Response(200, json=s.audit[int(iid)])))
    mock.post(url__regex=rf"^{CAL}/items/(?P<iid>\d+)/status$").mock(side_effect=guarded(status))
    mock.post(url__regex=rf"^{CAL}/items/(?P<iid>\d+)/notes$").mock(side_effect=guarded(notes))

    # 44 claim checker (called only with MODEL_CHECK=auto) and 80 lead hub
    def verify(r):
        s.verify_calls.append(json.loads(r.content))
        return httpx.Response(200, json={"ok": not s.verify_answer, "claims": s.verify_answer, "unsupported": []})
    s.verify_route = mock.post(f"{CLAIMS}/verify").mock(side_effect=guarded(verify))
    s.gateway_route = mock.post(url__regex=rf"^{GATEWAY}/.*").mock(return_value=httpx.Response(500))
    mock.get(f"{LEADS}/stats").mock(return_value=httpx.Response(200, json={"replies": {"drafted": 2, "sent": 5}}))
    return s


BIKE_TASK = {
    "goal": "Autumn weekday hires at the Porthleven shop, hybrid bikes and e-bikes",
    "pieces": [{"key": "p1", "channel": "linkedin", "kind": "post"}, {"key": "p2", "channel": "instagram", "kind": "caption"}],
    "scope": {"sites": ["porthleven"]},
    "publish_on": data.PUBLISH,
    "audience": "families on holiday",
}


def make_task(client, stack, facts="bikes", task=None):
    stack.facts = data.business(facts) if isinstance(facts, str) else facts
    r = client.post("/tasks", json=task or BIKE_TASK, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def paste_and_submit(client, tid, text, provider="chatgpt"):
    r = client.post(f"/tasks/{tid}/paste", json={"text": text, "provider": provider}, headers=AUTH)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["problems"] == [], d
    r = client.post(f"/tasks/{tid}/submit", json={"draft_id": d["draft_id"]}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()

