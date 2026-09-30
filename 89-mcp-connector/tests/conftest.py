"""Fakes for 05 and 88 written from the phase-1 contracts (respx; any call that is not mocked fails).

The fakes deliberately return internal values where the real 88 does too (filled slots, quotes,
details), so the tests prove the connector hides them. Routes that approve, publish, confirm,
retire, export or reconcile are mocked only so the tests can assert nobody called them.
"""
import json
from dataclasses import dataclass, field

import anyio
import httpx
import pytest
import respx
from mcp import Client

from app import config
from app.server import build_server

KEY = "internal-secret-key-123"
BRAND, TASKS, CAL = "http://brand.test", "http://tasks.test", "http://calendar.test"
TID = "T-ABC234"
TOKEN = "tok-" + "x" * 40

INT_SENTINEL = "SENTINEL-INTERNAL"
RES_SENTINEL = "SENTINEL-RESTRICTED"
RANK = {"public": 0, "internal": 1, "restricted": 2}


def fact(key, text, value, value_text, sensitivity="public", status="active", valid_from=None, valid_to=None,
         scope=None, disclosures=()):
    return {"key": key, "text": text, "value": value, "value_text": value_text, "sensitivity": sensitivity,
            "status": status, "valid_from": valid_from, "valid_to": valid_to,
            "scope": scope or {"sites": [], "regions": [], "channels": [], "segments": [], "plan_tiers": [],
                               "variants": []},
            "required_disclosures": list(disclosures), "forbidden_phrasing": ["cheapest in town"],
            "allowed_phrasing": [], "claim_class": "none", "version": 1}


FACTS = [
    fact("weekday-rate", "Midweek Escape costs £180 per room per night, 2 sharing.", 180, "£180 per room per night",
         disclosures=["per room per night, 2 sharing"]),
    fact("lake-view", "Every room at Lakeside has a lake view.", None, "a lake view",
         scope={"sites": ["lakeside"], "regions": [], "channels": [], "segments": [], "plan_tiers": [], "variants": []}),
    fact("summer-rate", "Summer rate £240 per night.", 240, "£240 per night", valid_to="2026-08-31"),
    fact("partner-rate", f"{INT_SENTINEL}-TEXT partner rate is £777 per night.", 777, f"£777 {INT_SENTINEL}",
         sensitivity="internal"),
    fact("owner-margin", f"{RES_SENTINEL}-TEXT our margin is 42.5 percent.", 42.5, f"{RES_SENTINEL} 42.5%",
         sensitivity="restricted"),
]


def cfg(**kw) -> config.Config:
    base = dict(internal_api_key=KEY, brand_url=BRAND, task_bridge_url=TASKS)
    return config.Config(**{**base, **kw})


@dataclass
class Fake:
    facts: list = field(default_factory=lambda: [dict(f) for f in FACTS])
    leak: bool = False           # a broken 05 that ignores max_sensitivity
    queries: list = field(default_factory=list)
    bodies: dict = field(default_factory=dict)
    split_problems: bool = False
    facts_v2_down: bool = False
    routes: dict = field(default_factory=dict)


def _query(fake: Fake, r: httpx.Request):
    p = r.url.params
    fake.queries.append(p)
    at = p.get("at") or "2026-09-29"
    max_s = p.get("max_sensitivity") or "internal"
    out, excluded = [], []
    for f in fake.facts:
        if not fake.leak and RANK[f["sensitivity"]] > RANK[max_s]:
            excluded.append({"key": f["key"], "reason": f["sensitivity"]})
            continue
        if f["valid_to"] and at > f["valid_to"]:
            excluded.append({"key": f["key"], "reason": "expired", "value_text": f["value_text"]})
            continue
        sites = f["scope"]["sites"]
        if sites and not p.get_list("site"):
            excluded.append({"key": f["key"], "reason": "scope_unspecified"})
            continue
        if sites and any(s not in sites for s in p.get_list("site")):
            excluded.append({"key": f["key"], "reason": "out_of_scope"})
            continue
        out.append(f)
    return httpx.Response(200, json={"fact_set_version": "fs-3-abcd1234", "at": at, "facts": out,
                                     "excluded": excluded})


def _task_piece(key, text, blocked, item, findings, checks=None):
    return {"piece_key": key, "filled_text": text, "filled_sha256": "0" * 64, "blocked": blocked,
            "findings": findings, "calendar_item_id": item, "status": "draft" if blocked else "in_review",
            "checks": checks or {"brand": [], "platform": []}, "notes": []}


SUBMIT_PIECES = [
    _task_piece("p1", "Midweek Escape: £180 per room per night, per room per night, 2 sharing.", False, 11,
                [{"sentence": "Midweek Escape: £180 per room per night.", "label": "match", "fact_key": "weekday-rate",
                  "quote": "Midweek Escape costs £180 per room per night, 2 sharing.", "blocking": False,
                  "detail": "matches weekday-rate"}]),
    _task_piece("p2", f"Partners pay £777 {INT_SENTINEL}. Cheapest in town!", True, 12,
                [{"sentence": f"Partners pay £777 {INT_SENTINEL}.", "label": "wrong_scope", "fact_key": "partner-rate",
                  "quote": f"{INT_SENTINEL}-TEXT partner rate is £777 per night.", "blocking": True,
                  "detail": "£777 is partner-rate's value (777 GBP), valid only for partners"},
                 {"sentence": "Cheapest in town!", "label": "forbidden_phrase", "fact_key": "weekday-rate",
                  "quote": "cheapest in town", "blocking": True, "detail": "forbidden phrasing"}],
                {"brand": [], "platform": [{"rule": "too_long", "severity": "error", "detail": "2300 chars"}]}),
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("APPROVER_KEY", "FACT_OWNER_KEY", "MCP_TOKEN", "MCP_HOST", "MCP_TOKEN_IN_PATH", "MCP_ALLOWED_HOSTS",
              "TOOL_CALLS_PER_MIN", "MAX_TEXT_CHARS"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def fake():
    f = Fake()
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as m:
        def guarded(fn):
            def inner(request, **kw):
                if request.headers.get("x-api-key") != KEY:
                    return httpx.Response(401, json={"detail": "missing or wrong X-API-Key"})
                if request.headers.get("x-approver-key") or request.headers.get("x-owner-key"):
                    raise AssertionError("the connector sent an approver or owner key")
                return fn(request, **kw)
            return inner

        def record(name, fn):
            def inner(request, **kw):
                if request.content:
                    f.bodies[name] = json.loads(request.content)
                return fn(request, **kw)
            return guarded(inner)

        # 05
        m.get(f"{BRAND}/facts/query").mock(side_effect=guarded(lambda r: _query(f, r)))
        m.get(f"{BRAND}/facts/v2").mock(side_effect=guarded(
            lambda r: httpx.Response(503, json={"detail": "down"}) if f.facts_v2_down
            else httpx.Response(200, json={"facts": f.facts})))

        # 88
        def create(r):
            return httpx.Response(201, json={
                "id": TID, "pack": f"Task {TID} · facts fs-3-abcd1234 · publish 2026-10-06\n[[weekday-rate]] ...",
                "pack_sha256": "a" * 64, "fact_set_version": "fs-3-abcd1234",
                "snapshot": [{"key": "weekday-rate", "version": 1, "slot": "[[weekday-rate]]", "sensitivity": "public"},
                             {"key": "partner-rate", "version": 1, "slot": "[[partner-rate]]", "sensitivity": "internal"}],
                "share_preview": {"sent": ["Midweek Escape costs £180 per room per night, 2 sharing. [[weekday-rate]]"],
                                  "slotted": ["partner-rate"], "withheld": [{"key": "owner-margin", "reason": "restricted"}],
                                  "chars": 900},
                "status": "open", "notes": []})

        def paste(r, tid):
            if tid != TID:
                return httpx.Response(404, json={"detail": f"task {tid} not found"})
            if f.split_problems:
                return httpx.Response(201, json={"draft_id": 1, "split": [
                    {"piece_key": "p1", "text": "all of it [[partner-rate]] here", "removed_pre": "", "removed_post": ""}],
                    "problems": ["found 1 marker, the task has 2 pieces"]})
            return httpx.Response(201, json={"draft_id": 1, "split": [
                {"piece_key": "p1", "text": "a", "removed_pre": "", "removed_post": ""},
                {"piece_key": "p2", "text": "b", "removed_pre": "", "removed_post": ""}], "problems": []})

        def split(r, tid, did):
            return httpx.Response(201, json={"draft_id": int(did) + 1, "split": [], "problems": []})

        def submit(r, tid):
            if tid != TID:
                return httpx.Response(404, json={"detail": f"task {tid} not found"})
            did = json.loads(r.content)["draft_id"]
            if did == 99:
                return httpx.Response(409, json={"detail": {"message": "piece p1 is already approved; nothing was submitted",
                                                            "piece_key": "p1", "calendar_item_id": 11}})
            return httpx.Response(200, json={"task_id": tid, "draft_id": did, "pieces": SUBMIT_PIECES})

        def check(r):
            body = json.loads(r.content)
            if len(body["text"]) > 40_000:
                return httpx.Response(413, json={"detail": "the text is longer than 40000 characters"})
            return httpx.Response(200, json={
                "blocked": True, "filled_text": f"Our partners get £777 {INT_SENTINEL}. Margin {RES_SENTINEL} 42.5%.",
                "findings": [{"sentence": f"Our partners get £777 {INT_SENTINEL}.", "label": "conflict_or_expired",
                              "fact_key": "partner-rate", "quote": f"{INT_SENTINEL}-TEXT partner rate is £777 per night.",
                              "blocking": True, "detail": "differs from £777"},
                             {"sentence": f"Margin {RES_SENTINEL} 42.5%.", "label": "no_source", "fact_key": "owner-margin",
                              "quote": f"{RES_SENTINEL}-TEXT our margin is 42.5 percent.", "blocking": True,
                              "detail": "42.5 percent not in any public fact"}]})

        def get_task(r, tid):
            if tid != TID:
                return httpx.Response(404, json={"detail": f"task {tid} not found"})
            return httpx.Response(200, json={
                "id": TID, "goal": "Autumn stays", "status": "submitted", "publish_on": "2026-10-06", "scope": {},
                "fact_set_version": "fs-3-abcd1234", "pack": "the pack", "share_preview": {"sent": []},
                "snapshot": [], "events": [{"kind": "pasted"}],
                "piece_state": [dict(p, channel="linkedin", state=p["status"], stale_facts=["partner-rate"])
                                for p in SUBMIT_PIECES],
                "drafts": [{"draft_id": 1, "kind": "paste", "provider": "claude", "chars": 10, "problems": [],
                            "parent_id": None, "created_at": "2026-09-29T10:00:00Z"}],
                "export": {"ready": False, "reasons": [f"piece p2 is a draft (£777 {INT_SENTINEL})"]}})

        m.post(f"{TASKS}/tasks").mock(side_effect=record("tasks", create))
        m.post(url__regex=rf"^{TASKS}/tasks/(?P<tid>[^/]+)/paste$").mock(side_effect=record("paste", paste))
        m.post(url__regex=rf"^{TASKS}/tasks/(?P<tid>[^/]+)/drafts/(?P<did>\d+)/split$").mock(
            side_effect=record("split", split))
        m.post(url__regex=rf"^{TASKS}/tasks/(?P<tid>[^/]+)/submit$").mock(side_effect=record("submit", submit))
        m.post(f"{TASKS}/check").mock(side_effect=record("check", check))
        m.get(url__regex=rf"^{TASKS}/tasks/(?P<tid>[^/]+)$").mock(side_effect=guarded(get_task))
        m.get(f"{TASKS}/blockers").mock(side_effect=guarded(lambda r: httpx.Response(200, json=[
            {"kind": "missing_facts", "count": 2, "text": "Facts the copy needs that nobody has confirmed",
             "link": "/facts?filter=questions"}])))

        # routes nobody may call: approve, publish, confirm, retire, import, export, reconcile, the calendar
        forbidden = {
            "confirm": m.post(url__regex=rf"^{BRAND}/facts/v2/[^/]+/confirm$"),
            "retire": m.post(url__regex=rf"^{BRAND}/facts/v2/[^/]+/retire$"),
            "import": m.post(f"{BRAND}/facts/v2/import"),
            "fact_write": m.route(method__in=["POST", "PUT", "PATCH", "DELETE"], url__regex=rf"^{BRAND}/facts/v2.*"),
            "export": m.get(url__regex=rf"^{TASKS}/tasks/[^/]+/export.*"),
            "reconcile": m.post(f"{TASKS}/reconcile"),
            "calendar": m.route(url__regex=rf"^{CAL}/.*"),
            "brand_other": m.route(url__regex=rf"^{BRAND}/.*"),
            "tasks_other": m.route(url__regex=rf"^{TASKS}/.*"),
        }
        for route in forbidden.values():
            route.mock(return_value=httpx.Response(599))
        f.routes = forbidden
        yield f


def call(server, name, args=None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(name, args or {})
    return anyio.run(go)


def tools(server):
    async def go():
        async with Client(server) as c:
            return (await c.list_tools()).tools
    return anyio.run(go)


@pytest.fixture
def server():
    return build_server(cfg())


def dump(result) -> str:
    return json.dumps(result.structured_content) + " ".join(getattr(b, "text", "") for b in result.content)
