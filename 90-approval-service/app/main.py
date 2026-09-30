"""Approval service: applies the control room's decisions to the content calendar without n8n.

POST /decisions takes exactly what 72 sends to n8n's webhook `mkt-apply-decisions` and answers
the same way. The decision logic is app/planner.py (pure); this module checks the caller, reads
the items in review, runs the planned calendar calls in two stages and reports.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import planner

log = logging.getLogger("approval_service")

REQUIRED = ("INTERNAL_API_KEY", "APPROVER_KEY", "CONTROL_ROOM_KEY", "CALENDAR_URL")
# Tests replace this with an in-process transport (19 via ASGI, fakes for 46 and 61).
TRANSPORT: httpx.AsyncBaseTransport | None = None
# One batch at a time: stage 2 of one batch never interleaves with stage 1 of another.
_batch_lock = asyncio.Lock()


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def config_problems() -> list[str]:
    """What is wrong with the configuration, by name only (never a value)."""
    problems = [f"{k} is not set" for k in REQUIRED if not _env(k)]
    control = _env("CONTROL_ROOM_KEY")
    if control and len(control) < 16:
        problems.append("CONTROL_ROOM_KEY is shorter than 16 characters")
    if control and control in (_env("INTERNAL_API_KEY"), _env("APPROVER_KEY")):
        problems.append("CONTROL_ROOM_KEY must differ from INTERNAL_API_KEY and APPROVER_KEY")
    return problems


def timeout() -> float:
    try:
        return min(max(float(_env("REQUEST_TIMEOUT") or 15), 1.0), 120.0)
    except ValueError:
        return 15.0


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout(), follow_redirects=False, transport=TRANSPORT)


def refuse(status: int, why: str) -> JSONResponse:
    # {ok, error} as n8n's webhook answers; `detail` too, which 72's error text reads.
    return JSONResponse({"ok": False, "error": why, "detail": why}, status_code=status)


def actor_header(reviewer: str) -> dict:
    """X-Actor: the reviewer's full name for 19's audit. Headers are Latin-1; a name outside it
    is left out and 19 falls back to the "by <name>" in the note."""
    name = " ".join(reviewer.split())[:80]
    try:
        return {"X-Actor": name.encode("latin-1")}
    except UnicodeEncodeError:
        return {}


def _detail(r: httpx.Response):
    """n8n's `(body || {}).detail ?? body ?? null`."""
    try:
        body = r.json()
    except ValueError:
        return r.text[:500] or None
    if isinstance(body, dict) and body.get("detail") is not None:
        return body["detail"]
    return body


@asynccontextmanager
async def lifespan(_app):
    for p in config_problems():
        log.error("not configured: %s; POST /decisions answers 503 until it is fixed", p)
    yield


app = FastAPI(title="approval-service", lifespan=lifespan)


@app.get("/health")
async def health():
    problems = config_problems()
    reachable = None
    cal = _env("CALENDAR_URL").rstrip("/")
    if cal:
        try:
            async with client() as c:
                r = await c.get(f"{cal}/health", timeout=3.0)
            reachable = r.status_code == 200
        except httpx.HTTPError:
            reachable = False
    return {"status": "misconfigured" if problems else "ok", "problems": problems, "calendar": reachable,
            "learning": bool(_env("LEARNING_URL")), "engine": bool(_env("ENGINE_URL"))}


def control_key_ok(got: str | None) -> bool:
    want = _env("CONTROL_ROOM_KEY")
    return bool(got) and len(want) >= 16 and hmac.compare_digest(got.encode(), want.encode())


@app.post("/decisions")
async def decisions(request: Request):
    problems = config_problems()
    if problems:
        return refuse(503, "approval service is not configured: " + "; ".join(problems))
    if not control_key_ok(request.headers.get("x-control-key")):
        return refuse(401, "unauthorized")
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        return refuse(422, "body: JSON {reviewer, decisions}")
    try:
        reviewer, ds = planner.validate(body)
    except planner.Invalid as e:
        return refuse(422, str(e))
    async with _batch_lock, client() as c:
        return await apply(c, reviewer, ds)


async def apply(c: httpx.AsyncClient, reviewer: str, ds: list[dict]) -> JSONResponse:
    cal = _env("CALENDAR_URL").rstrip("/")
    key = {"X-API-Key": _env("INTERNAL_API_KEY")}
    try:
        r = await c.get(f"{cal}/items", params={"status": "in_review"}, headers=key)
    except httpx.HTTPError as e:
        return refuse(502, f"calendar unreachable ({type(e).__name__}); nothing was changed")
    if r.status_code != 200 or not isinstance(_json(r), list):
        return refuse(502, f"calendar: HTTP {r.status_code} listing the items in review; nothing was changed")
    p = planner.plan(ds, _json(r), reviewer, datetime.now(timezone.utc))
    if not p["count"]:
        log.info("decisions by %s: nothing in review to decide (%d not in review)", reviewer, len(p["not_in_review"]))
        return JSONResponse(planner.nothing_to_decide(p["not_in_review"]))

    # Approving needs the approver key; the reviewer's name goes to 19's audit.
    write = {**key, "X-Approver-Key": _env("APPROVER_KEY"), **actor_header(reviewer)}
    failures, refused_items = [], set()
    for stage in (1, 2):
        for op in (o for o in p["ops"] if o["stage"] == stage):
            if op["item_id"] in refused_items:
                continue  # stage 1 of this item was refused: do not go on (n8n would try anyway)
            try:
                res = await c.request(op["method"], cal + op["path"], json=op["body"], headers=write)
                status, detail = res.status_code, (_detail(res) if res.status_code >= 300 else None)
            except httpx.HTTPError as e:
                status, detail = None, f"calendar unreachable ({type(e).__name__})"
            if status is None or status >= 300:
                failures.append({"item_id": op["item_id"], "status": status, "detail": detail})
                refused_items.add(op["item_id"])

    notes = []
    done = [e for e in p["events"] if e["item_id"] not in refused_items]
    notes += await fail_soft(c, "learning", "LEARNING_URL", [("/events", e) for e in done],
                             "decision events were not recorded for learning")
    outcomes = [(f"/pillars/{p['pillars'][e['item_id']]}/outcomes", {"item_id": e["item_id"], "decision": e["decision"]})
                for e in done if e["item_id"] in p["pillars"]]
    notes += await fail_soft(c, "engine", "ENGINE_URL", outcomes, "content-engine outcomes were not recorded")
    out = planner.result(p, failures, notes)
    log.info("decisions by %s: %d calendar calls, %d stale, %d failed, %d not in review", reviewer,
             len(p["ops"]), len(out["stale"]), len(out["failed"]), len(out["not_in_review"]))
    return JSONResponse(out)


async def fail_soft(c: httpx.AsyncClient, name: str, env: str, calls: list[tuple[str, dict]], what: str) -> list[str]:
    """POST each call to the optional service; a failure is a summary note, never an error."""
    base = _env(env).rstrip("/")
    if not base or not calls:
        return []
    bad, why = 0, ""
    for path, body in calls:
        try:
            r = await c.post(base + path, json=body, headers={"X-API-Key": _env("INTERNAL_API_KEY")})
            if r.status_code >= 300:
                bad, why = bad + 1, f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            bad, why = bad + 1, f"{name} service unreachable ({type(e).__name__})"
    if not bad:
        return []
    log.warning("%s: %d of %d calls failed (%s)", name, bad, len(calls), why)
    return [f"Note: {bad} of {len(calls)} {what} ({why}); the decisions themselves are saved."]


def _json(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        return None
