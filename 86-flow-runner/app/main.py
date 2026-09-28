"""Flow runner: triggered lifecycle emails (welcome, onboarding, win-back) with a randomised
holdout, a daily cap and a kill switch.

Events come in (subscribed, trial_started, inactive, purchased, clicked, unsubscribed, ...).
A contact with recorded consent enters the flow whose trigger matches, and is assigned to the
`flow` arm or the `holdout` arm. POST /tick (every 15 minutes, from n8n) sends the steps that are
due. Only an approved flow version ever sends. With DRY_RUN (the default) a send is a row in the
outbox and nothing leaves this service.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from . import sequence, services, stats

app = FastAPI(title="flow-runner")

EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,253}\.[a-zA-Z]{2,63}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
EVENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# The three flows every install has. Their steps are empty until someone writes and approves a version.
BUILT_IN = {
    "welcome": ("subscribed", ["unsubscribed"]),
    "onboarding": ("trial_started", ["unsubscribed", "purchased"]),
    "winback": ("inactive", ["unsubscribed", "purchased"]),
}
COUNTED = ("dry_run", "sent")  # outbox rows that reached (or would have reached) the contact

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS flows (
    name TEXT PRIMARY KEY,
    trigger TEXT NOT NULL,
    default_exits TEXT NOT NULL,
    holdout_pct REAL,
    mode TEXT NOT NULL DEFAULT 'holdout',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flow TEXT NOT NULL REFERENCES flows (name),
    version INTEGER NOT NULL,
    steps TEXT NOT NULL,
    exit_events TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    notes TEXT,
    calendar_item_id INTEGER,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    approved_by TEXT,
    UNIQUE (flow, version)
);
CREATE TABLE IF NOT EXISTS contacts (
    email TEXT PRIMARY KEY,
    consent INTEGER NOT NULL DEFAULT 0,
    consent_source TEXT,
    consent_at TEXT,
    suppressed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE,
    type TEXT NOT NULL,
    email TEXT NOT NULL,
    at TEXT NOT NULL,
    data TEXT,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_email ON events (email, type, at);
CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flow TEXT NOT NULL,
    email TEXT NOT NULL,
    arm TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    status TEXT NOT NULL,
    exit_reason TEXT,
    ended_at TEXT,
    UNIQUE (flow, email)
);
CREATE INDEX IF NOT EXISTS enrollments_active ON enrollments (status, flow);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_id INTEGER NOT NULL,
    flow TEXT NOT NULL,
    version_id INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    email TEXT NOT NULL,
    arm TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_markdown TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (enrollment_id, step_index)
);
CREATE INDEX IF NOT EXISTS outbox_day ON outbox (created_at);
-- An erased person who had unsubscribed: only a salted hash of the address, so they stay suppressed.
CREATE TABLE IF NOT EXISTS erased_unsubscribes (hash TEXT PRIMARY KEY, erased_at TEXT NOT NULL);
"""
_migrate_lock = threading.Lock()
_migrated: set[str] = set()
_tick_lock = threading.Lock()  # one tick at a time: two overlapping ticks must not both send


# ---------- time and settings


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"not an ISO 8601 date/time: {value!r}")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def env_flag(name: str, default: bool) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if not v:
        return default
    return v not in ("false", "0", "no", "off")


def dry_run() -> bool:
    """Only an explicit false turns DRY_RUN off."""
    v = (os.environ.get("DRY_RUN") or "").strip().lower()
    return v not in ("false", "0", "no", "off")


def env_num(name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(os.environ.get(name) or default)
    except ValueError:
        return default
    return min(max(v, lo), hi)


def daily_cap() -> int:
    return int(env_num("FLOW_DAILY_CAP", 200, 0, 100000))


def default_holdout() -> float:
    return env_num("FLOW_HOLDOUT_PCT", 15, 0, 50)


def min_n() -> int:
    return int(env_num("FLOW_MIN_N", 100, 10, 1000000))


def entry_max_age() -> timedelta:
    return timedelta(hours=env_num("FLOW_ENTRY_MAX_AGE_HOURS", 48, 1, 24 * 365))


def min_gap() -> timedelta:
    return timedelta(hours=env_num("FLOW_MIN_GAP_HOURS", 12, 0, 24 * 30))


# ---------- storage


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/flows.sqlite")


def migrate(conn: sqlite3.Connection) -> None:
    path = db_path()
    if path in _migrated:
        return
    with _migrate_lock:
        if path in _migrated:
            return
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in filter(str.strip, SCHEMA.split(";")):
                conn.execute(stmt)
            for name, (trigger, exits) in BUILT_IN.items():
                conn.execute("INSERT OR IGNORE INTO flows (name, trigger, default_exits, created_at)"
                             " VALUES (?, ?, ?, ?)", (name, trigger, json.dumps(exits), fmt(now_dt())))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        _migrated.add(path)


@contextmanager
def db():
    conn = sqlite3.connect(db_path(), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        migrate(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def write(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def meta_get(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(conn, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM meta WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (key, value))


def salt(conn) -> str:
    """Per-install salt for the arm hash: FLOW_SALT, or one generated once and kept in the database."""
    env = (os.environ.get("FLOW_SALT") or "").strip()
    if env:
        return env
    s = meta_get(conn, "salt")
    if s is None:
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('salt', ?)", (secrets.token_hex(16),))
        s = meta_get(conn, "salt")
    return s


def flow_or_404(conn, name: str) -> dict:
    row = conn.execute("SELECT * FROM flows WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise HTTPException(404, f"flow {name!r} not found")
    f = dict(row)
    f["default_exits"] = json.loads(f["default_exits"])
    f["holdout_pct"] = default_holdout() if f["holdout_pct"] is None else f["holdout_pct"]
    return f


def version_row(row) -> dict:
    v = dict(row)
    v["steps"] = json.loads(v["steps"])
    v["exit_events"] = json.loads(v["exit_events"])
    return v


def version_or_404(conn, name: str, number: int) -> dict:
    row = conn.execute("SELECT * FROM versions WHERE flow = ? AND version = ?", (name, number)).fetchone()
    if row is None:
        raise HTTPException(404, f"flow {name!r} has no version {number}")
    return version_row(row)


def approved_version(conn, name: str) -> dict | None:
    row = conn.execute("SELECT * FROM versions WHERE flow = ? AND status = 'approved'", (name,)).fetchone()
    return version_row(row) if row else None


def paused_state(conn) -> dict | None:
    if env_flag("FLOW_PAUSED", False):
        return {"since": None, "reason": "FLOW_PAUSED is true", "by": "env"}
    raw = meta_get(conn, "paused")
    return json.loads(raw) if raw else None


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def approver_ok(x_approver_key: str | None) -> bool:
    expected = os.environ.get("APPROVER_KEY")
    return bool(expected and x_approver_key and hmac.compare_digest(x_approver_key, expected))


def require_approver(x_approver_key: str | None) -> None:
    """Approving a flow version (and resuming after a pause) needs APPROVER_KEY. Unlike 19, an
    unset APPROVER_KEY refuses: this service sends email, so there is no open mode."""
    if not os.environ.get("APPROVER_KEY"):
        raise HTTPException(503, "APPROVER_KEY is not configured on this service")
    if not approver_ok(x_approver_key):
        raise HTTPException(403, "approving needs the approver key (X-Approver-Key)")


def erased_hash(conn, email: str) -> str:
    return hashlib.sha256(f"{salt(conn)}:{email}".encode()).hexdigest()


# ---------- models


def _email(v: str) -> str:
    v = v.strip().lower()
    if not EMAIL_RE.match(v):
        raise ValueError("not a valid email address")
    return v


def _event_name(v: str) -> str:
    v = v.strip().lower()
    if not EVENT_RE.match(v):
        raise ValueError("an event name: lower-case letters, digits and _, at most 40")
    return v


class Step(BaseModel):
    delay_hours: int = Field(ge=0, le=24 * 90, description="hours after the contact entered the flow")
    subject: str = Field(min_length=1, max_length=150)
    body_markdown: str = Field(min_length=1, max_length=20000)

    @field_validator("subject", "body_markdown")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("subject")
    @classmethod
    def one_line(cls, v: str) -> str:
        return " ".join(v.split())


def _exits(v: list[str] | None) -> list[str] | None:
    if v is None:
        return None
    out = []
    for e in v:
        e = _event_name(e)
        if e not in out:
            out.append(e)
    if "unsubscribed" not in out:
        out.insert(0, "unsubscribed")  # always an exit, whatever the flow says
    return out


class VersionIn(BaseModel):
    steps: list[Step] = Field(min_length=1, max_length=10)
    exit_events: list[str] | None = Field(default=None, max_length=10)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("steps")
    @classmethod
    def in_order(cls, v: list[Step]) -> list[Step]:
        if any(b.delay_hours < a.delay_hours for a, b in zip(v, v[1:])):
            raise ValueError("steps must be in order of delay_hours")
        return v

    @field_validator("exit_events")
    @classmethod
    def exits(cls, v):
        return _exits(v)


class FlowIn(BaseModel):
    name: str
    trigger: str
    exit_events: list[str] | None = Field(default=None, max_length=10)
    holdout_pct: float | None = Field(default=None, ge=0, le=50)
    mode: Literal["holdout", "aa"] = "holdout"

    @field_validator("name")
    @classmethod
    def slug(cls, v: str) -> str:
        v = v.strip().lower()
        if not NAME_RE.match(v):
            raise ValueError("a flow name: lower-case letters, digits, - and _, at most 40")
        return v

    @field_validator("trigger")
    @classmethod
    def trig(cls, v: str) -> str:
        v = _event_name(v)
        if v in ("unsubscribed", "clicked", "purchased"):
            raise ValueError(f"{v!r} cannot start a flow")
        return v

    @field_validator("exit_events")
    @classmethod
    def exits(cls, v):
        return _exits(v)


class FlowPatch(BaseModel):
    holdout_pct: float | None = Field(default=None, ge=0, le=50)
    mode: Literal["holdout", "aa"] | None = None


class ContactIn(BaseModel):
    email: str = Field(max_length=320)
    consent: bool | None = None  # true = consent given now; false = withdrawn; absent = no change
    source: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str) -> str:
        return _email(v)


class EventIn(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=200)
    type: str
    contact: ContactIn
    at: str | None = None
    data: dict | None = None

    @field_validator("type")
    @classmethod
    def event_type(cls, v: str) -> str:
        return _event_name(v)

    @field_validator("at")
    @classmethod
    def when(cls, v):
        return None if v in (None, "") else fmt(parse_time(v))

    @field_validator("data")
    @classmethod
    def small(cls, v):
        if v is not None and len(json.dumps(v)) > 5000:
            raise ValueError("data is at most 5000 characters of JSON")
        return v


class DraftIn(BaseModel):
    goal: str = Field(min_length=3, max_length=500)
    audience: str | None = Field(default=None, max_length=500)
    offer: str | None = Field(default=None, max_length=500)
    emails: int = Field(default=4, ge=3, le=5)
    link: str | None = Field(default=None, max_length=2000)

    @field_validator("link")
    @classmethod
    def http_link(cls, v):
        if v is None or not v.strip():
            return None
        if not re.match(r"^https?://[^\s/]+\S*$", v.strip()):
            raise ValueError("link must be an http(s) URL")
        return v.strip()


class ApproveIn(BaseModel):
    approved_by: str | None = Field(default=None, max_length=80)


class PauseIn(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


# ---------- flows and versions


def flow_view(conn, f: dict) -> dict:
    vs = [version_row(r) for r in conn.execute("SELECT * FROM versions WHERE flow = ? ORDER BY version", (f["name"],))]
    counts = {r["arm"] + "_" + r["status"]: r["n"] for r in conn.execute(
        "SELECT arm, status, COUNT(*) AS n FROM enrollments WHERE flow = ? GROUP BY arm, status", (f["name"],))}
    approved = next((v for v in vs if v["status"] == "approved"), None)
    return {"name": f["name"], "trigger": f["trigger"], "mode": f["mode"], "holdout_pct": f["holdout_pct"],
            "default_exit_events": f["default_exits"],
            "approved_version": approved["version"] if approved else None,
            "running": approved is not None, "versions": vs, "enrollments": counts}


def new_version(conn, f: dict, steps: list[dict], exits: list[str] | None, source: str,
                notes: str | None = None, calendar_item_id: int | None = None, status: str = "draft") -> dict:
    last = conn.execute("SELECT MAX(version) AS m FROM versions WHERE flow = ?", (f["name"],)).fetchone()["m"] or 0
    if exits is None:  # an edit keeps the exits of the version it replaces
        prev = conn.execute("SELECT exit_events FROM versions WHERE flow = ? ORDER BY version DESC LIMIT 1",
                            (f["name"],)).fetchone()
        exits = json.loads(prev["exit_events"]) if prev else _exits(f["default_exits"])
    conn.execute("INSERT INTO versions (flow, version, steps, exit_events, status, source, notes,"
                 " calendar_item_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (f["name"], last + 1, json.dumps(steps), json.dumps(exits), status, source, notes,
                  calendar_item_id, fmt(now_dt())))
    return version_or_404(conn, f["name"], last + 1)


def approve(conn, f: dict, v: dict, by: str | None) -> dict:
    """The one way a version becomes approved. The previous approved version retires; contacts
    already in the flow continue with the new version's steps from where they are."""
    conn.execute("UPDATE versions SET status = 'retired' WHERE flow = ? AND status = 'approved'", (f["name"],))
    conn.execute("UPDATE versions SET status = 'approved', approved_at = ?, approved_by = ? WHERE id = ?",
                 (fmt(now_dt()), (by or "approver").strip()[:80], v["id"]))
    return version_or_404(conn, f["name"], v["version"])


def review_title(v: dict) -> str:
    return f"Email flow: {v['flow']} v{v['version']} ({len(v['steps'])} emails)"


def review_body(v: dict) -> str:
    return sequence.render(v["flow"], v["version"], v["steps"], v["exit_events"])


# ---------- endpoints: health, flows


@app.get("/health")
def health():
    with db() as conn:
        paused = paused_state(conn)
    return {"status": "ok", "dry_run": dry_run(), "paused": paused is not None, "daily_cap": daily_cap(),
            "send_path": "outbox only (dry run)" if dry_run() else "listmonk-bridge /tx (stub, see README)"}


@app.get("/flows", dependencies=[Depends(require_key)])
def list_flows():
    with db() as conn:
        out = []
        for r in conn.execute("SELECT name FROM flows ORDER BY created_at, name"):
            view = flow_view(conn, flow_or_404(conn, r["name"]))
            view.pop("versions")
            out.append(view)
        return out


@app.post("/flows", status_code=201, dependencies=[Depends(require_key)])
def create_flow(req: FlowIn):
    with db() as conn, write(conn):
        if conn.execute("SELECT 1 FROM flows WHERE name = ?", (req.name,)).fetchone():
            raise HTTPException(409, f"flow {req.name!r} exists")
        conn.execute("INSERT INTO flows (name, trigger, default_exits, holdout_pct, mode, created_at)"
                     " VALUES (?, ?, ?, ?, ?, ?)",
                     (req.name, req.trigger, json.dumps(req.exit_events or ["unsubscribed"]), req.holdout_pct,
                      req.mode, fmt(now_dt())))
        return flow_view(conn, flow_or_404(conn, req.name))


@app.get("/flows/{name}", dependencies=[Depends(require_key)])
def get_flow(name: str):
    with db() as conn:
        return flow_view(conn, flow_or_404(conn, name))


@app.patch("/flows/{name}", dependencies=[Depends(require_key)])
def patch_flow(name: str, req: FlowPatch):
    with db() as conn, write(conn):
        flow_or_404(conn, name)
        if req.holdout_pct is not None:  # affects contacts who enter from now on
            conn.execute("UPDATE flows SET holdout_pct = ? WHERE name = ?", (req.holdout_pct, name))
        if req.mode is not None:
            conn.execute("UPDATE flows SET mode = ? WHERE name = ?", (req.mode, name))
        return flow_view(conn, flow_or_404(conn, name))


@app.post("/flows/{name}/versions", status_code=201, dependencies=[Depends(require_key)])
def edit_flow(name: str, req: VersionIn):
    """Every edit is a new draft version. The approved version keeps running until a person
    approves the new one."""
    with db() as conn, write(conn):
        f = flow_or_404(conn, name)
        return new_version(conn, f, [s.model_dump() for s in req.steps], req.exit_events, "manual", req.notes)


@app.post("/flows/{name}/draft", status_code=201, dependencies=[Depends(require_key)])
def draft_flow(name: str, req: DraftIn):
    """Draft a version with the email_sequence prompt (03, the prompt 57 uses), check each email
    with the claim checker (44) and save it as a draft. Nothing is approved here."""
    with db() as conn:
        f = flow_or_404(conn, name)
    try:
        emails = services.write_sequence(req.goal, req.audience, req.offer, req.emails)
        context = "\n".join(x for x in (req.goal, req.audience, req.offer) if x)
        steps, flagged = [], []
        for i, e in enumerate(emails, 1):
            cta = str(e.get("cta_text") or "").strip()
            button = f"[{cta}]({req.link})" if req.link and cta else f"**{cta}**" if cta else ""
            body = "\n\n".join(x for x in (str(e.get("body") or "").strip(), button) if x)
            steps.append(Step(delay_hours=int(e.get("day") or 0) * 24, subject=str(e.get("subject") or ""),
                              body_markdown=body).model_dump())
            flagged += [f"email {i}: {c}" for c in services.check_claims(str(e.get("body") or ""), context)]
    except services.ServiceError as exc:
        raise HTTPException(502, str(exc))
    except ValueError as exc:  # the model's output did not make valid steps
        raise HTTPException(502, f"llm-gateway returned emails that are not valid steps: {exc}")
    steps.sort(key=lambda s: s["delay_hours"])
    notes = ("Claim check: not supported by the goal, audience or offer: " + "; ".join(flagged)[:1800]
             if flagged else None)
    with db() as conn, write(conn):
        v = new_version(conn, f, steps, None, "llm", notes)
    return {**v, "claims_flagged": flagged}


@app.post("/flows/{name}/versions/{number}/submit", dependencies=[Depends(require_key)])
def submit_version(name: str, number: int):
    """Ask for approval: the version goes to in_review and, when CALENDAR_URL is set, to the
    content calendar (19) as one email_flow item holding the whole sequence."""
    with db() as conn:
        f = flow_or_404(conn, name)
        v = version_or_404(conn, name, number)
    if v["status"] not in ("draft", "in_review"):
        raise HTTPException(409, f"version {number} is {v['status']}; only a draft can be submitted")
    item_id = v["calendar_item_id"]
    if services.calendar_url() and not item_id:
        try:
            item_id = services.create_review_item(review_title(v), review_body(v), v["notes"])
        except services.ServiceError as exc:
            raise HTTPException(502, str(exc))
    with db() as conn, write(conn):
        conn.execute("UPDATE versions SET status = 'in_review', calendar_item_id = ? WHERE id = ?"
                     " AND status IN ('draft', 'in_review')", (item_id, v["id"]))
        return version_or_404(conn, f["name"], number)


@app.post("/flows/{name}/versions/{number}/approve", dependencies=[Depends(require_key)])
def approve_version(name: str, number: int, req: ApproveIn | None = None,
                    x_approver_key: str | None = Header(default=None)):
    require_approver(x_approver_key)
    with db() as conn, write(conn):
        f = flow_or_404(conn, name)
        v = version_or_404(conn, name, number)
        if v["status"] not in ("draft", "in_review"):
            raise HTTPException(409, f"version {number} is {v['status']}; edit the flow to make a new version")
        return approve(conn, f, v, req.approved_by if req else None)


@app.post("/flows/{name}/versions/{number}/reject", dependencies=[Depends(require_key)])
def reject_version(name: str, number: int):
    with db() as conn, write(conn):
        flow_or_404(conn, name)
        v = version_or_404(conn, name, number)
        if v["status"] not in ("draft", "in_review"):
            raise HTTPException(409, f"version {number} is {v['status']}")
        conn.execute("UPDATE versions SET status = 'rejected' WHERE id = ?", (v["id"],))
        return version_or_404(conn, name, number)


@app.post("/reviews/sync", dependencies=[Depends(require_key)])
def sync_reviews(x_approver_key: str | None = Header(default=None)):
    """Carry decisions made in the control room (72) or the approval form (38) over from the
    calendar (19). Called by n8n every 15 minutes WITH the approver key; without it, nothing is
    approved and the versions that would be are listed under `waiting_for_approver_key`.

    Item approved in 19 → the version is approved. If the reviewer edited the text, the edited
    sequence becomes a new version, approved as it was approved in 19 (19 freezes an approved
    item, so this is exactly the text the person approved). Item rejected → version rejected.
    """
    can_approve = approver_ok(x_approver_key)
    out = {"created": [], "approved": [], "rejected": [], "waiting": [], "waiting_for_approver_key": [],
           "problems": []}
    if not services.calendar_url():
        return {**out, "note": "CALENDAR_URL is not set: approve with POST /flows/{name}/versions/{n}/approve"}
    with db() as conn:
        pending = [version_row(r) for r in conn.execute("SELECT * FROM versions WHERE status = 'in_review' ORDER BY id")]
    for v in pending:
        label = f"{v['flow']} v{v['version']}"
        try:
            if not v["calendar_item_id"]:
                item_id = services.create_review_item(review_title(v), review_body(v), v["notes"])
                with db() as conn, write(conn):
                    conn.execute("UPDATE versions SET calendar_item_id = ? WHERE id = ?", (item_id, v["id"]))
                out["created"].append({"version": label, "calendar_item_id": item_id})
                continue
            item = services.get_item(v["calendar_item_id"])
        except services.ServiceError as exc:
            out["problems"].append(f"{label}: {exc}")
            continue
        status = item.get("status")
        if status in ("approved", "published"):
            if not can_approve:
                out["waiting_for_approver_key"].append(label)
                continue
            by = "control room / approval form (19 item %s)" % v["calendar_item_id"]
            with db() as conn, write(conn):
                f = flow_or_404(conn, v["flow"])
                cur = version_or_404(conn, v["flow"], v["version"])
                if cur["status"] != "in_review":
                    continue
                if (item.get("body") or "").strip() == review_body(cur).strip():
                    done = approve(conn, f, cur, by)
                else:
                    try:
                        steps = [Step(**s).model_dump() for s in sequence.parse(item.get("body") or "")]
                    except (sequence.SequenceError, ValueError) as exc:
                        out["problems"].append(f"{label}: the approved text could not be read ({str(exc)[:200]}); "
                                               "not approved. Fix the text or approve in 86 directly.")
                        continue
                    conn.execute("UPDATE versions SET status = 'superseded' WHERE id = ?", (cur["id"],))
                    edited = new_version(conn, f, steps, cur["exit_events"], "calendar_edit",
                                         f"edited in review from v{cur['version']}", cur["calendar_item_id"],
                                         status="in_review")
                    done = approve(conn, f, edited, by)
            out["approved"].append(f"{done['flow']} v{done['version']}")
        elif status == "rejected":
            with db() as conn, write(conn):
                conn.execute("UPDATE versions SET status = 'rejected' WHERE id = ? AND status = 'in_review'", (v["id"],))
            out["rejected"].append(label)
        else:
            out["waiting"].append(label)
    return out


# ---------- events


def exit_enrollments(conn, email: str, reason: str, at: str, flows: list[str] | None = None) -> list[str]:
    rows = conn.execute("SELECT id, flow FROM enrollments WHERE email = ? AND status = 'active'", (email,)).fetchall()
    out = []
    for r in rows:
        if flows is None or r["flow"] in flows:
            conn.execute("UPDATE enrollments SET status = 'exited', exit_reason = ?, ended_at = ? WHERE id = ?",
                         (reason, at, r["id"]))
            out.append(r["flow"])
    return out


@app.post("/events", dependencies=[Depends(require_key)])
def post_event(req: EventIn):
    now = now_dt()
    at = req.at or fmt(now)
    if parse_time(at) > now + timedelta(minutes=5):
        raise HTTPException(422, "at is in the future")
    email = req.contact.email
    result = {"type": req.type, "email": email, "duplicate": False, "entered": [], "exited": [], "skipped": []}
    with db() as conn, write(conn):
        if req.id and conn.execute("SELECT 1 FROM events WHERE event_id = ?", (req.id,)).fetchone():
            return {**result, "duplicate": True}
        conn.execute("INSERT OR IGNORE INTO contacts (email, created_at) VALUES (?, ?)", (email, fmt(now)))
        if conn.execute("SELECT 1 FROM erased_unsubscribes WHERE hash = ?", (erased_hash(conn, email),)).fetchone():
            conn.execute("UPDATE contacts SET suppressed_at = COALESCE(suppressed_at, ?) WHERE email = ?", (at, email))
        if req.contact.consent is True:
            conn.execute("UPDATE contacts SET consent = 1, consent_at = ?, consent_source = ? WHERE email = ? AND consent = 0",
                         (at, req.contact.source or req.type, email))
        elif req.contact.consent is False:
            conn.execute("UPDATE contacts SET consent = 0 WHERE email = ?", (email,))
            result["exited"] += exit_enrollments(conn, email, "consent withdrawn", at)
        conn.execute("INSERT INTO events (event_id, type, email, at, data, received_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (req.id, req.type, email, at, json.dumps(req.data) if req.data else None, fmt(now)))
        if req.type == "unsubscribed":  # forever: nothing here ever lifts it
            conn.execute("UPDATE contacts SET suppressed_at = COALESCE(suppressed_at, ?) WHERE email = ?", (at, email))
            result["exited"] += exit_enrollments(conn, email, "unsubscribed", at)
        else:
            for r in conn.execute("SELECT id, flow, entered_at FROM enrollments WHERE email = ? AND status = 'active'",
                                  (email,)).fetchall():
                v = approved_version(conn, r["flow"])
                if v and req.type in v["exit_events"] and at >= r["entered_at"]:
                    result["exited"] += exit_enrollments(conn, email, req.type, at, [r["flow"]])
        contact = conn.execute("SELECT * FROM contacts WHERE email = ?", (email,)).fetchone()
        for f in conn.execute("SELECT name FROM flows WHERE trigger = ? ORDER BY name", (req.type,)).fetchall():
            f = flow_or_404(conn, f["name"])
            why = None
            if contact["suppressed_at"]:
                why = "unsubscribed"
            elif not contact["consent"]:
                why = "no recorded consent"
            elif approved_version(conn, f["name"]) is None:
                why = "flow has no approved version"
            elif paused_state(conn) is not None:
                why = "flows are paused"
            elif now - parse_time(at) > entry_max_age():
                why = "event is too old to start a flow"
            elif conn.execute("SELECT 1 FROM enrollments WHERE flow = ? AND email = ?", (f["name"], email)).fetchone():
                why = "already entered this flow"
            if why:
                result["skipped"].append({"flow": f["name"], "reason": why})
                continue
            arm = stats.arm_for(salt(conn), f["name"], email, f["holdout_pct"])
            conn.execute("INSERT INTO enrollments (flow, email, arm, entered_at, status) VALUES (?, ?, ?, ?, 'active')",
                         (f["name"], email, arm, at))
            result["entered"].append({"flow": f["name"], "arm": arm})
    return result


# ---------- sending


def exit_reason(conn, e: dict, v: dict) -> str | None:
    """Checked right before each send, inside the send transaction."""
    c = conn.execute("SELECT consent, suppressed_at FROM contacts WHERE email = ?", (e["email"],)).fetchone()
    if c is None or c["suppressed_at"]:
        return "unsubscribed"
    if not c["consent"]:
        return "consent withdrawn"
    marks = ",".join("?" * len(v["exit_events"]))
    hit = conn.execute(f"SELECT type FROM events WHERE email = ? AND type IN ({marks}) AND at >= ? ORDER BY at LIMIT 1",
                       (e["email"], *v["exit_events"], e["entered_at"])).fetchone()
    return hit["type"] if hit else None


def sent_today(conn, now: datetime) -> int:
    day = now.strftime("%Y-%m-%d")
    return conn.execute("SELECT COUNT(*) AS n FROM outbox WHERE created_at >= ? AND created_at < ?",
                        (day + "T00:00:00Z", (now + timedelta(days=1)).strftime("%Y-%m-%d") + "T00:00:00Z")).fetchone()["n"]


def due_steps(conn, now: datetime) -> tuple[list[tuple], dict]:
    """(due_at, enrollment, version, step_index) for every contact whose next step is due."""
    due, skipped = [], {"no approved version": 0}
    versions: dict[str, dict | None] = {}
    rows = conn.execute("SELECT e.*, f.mode FROM enrollments e JOIN flows f ON f.name = e.flow"
                        " WHERE e.status = 'active' ORDER BY e.id").fetchall()
    for r in rows:
        e = dict(r)
        if e["arm"] == "holdout" and e["mode"] != "aa":
            continue  # the holdout gets nothing; it is only tracked
        if e["flow"] not in versions:
            versions[e["flow"]] = approved_version(conn, e["flow"])
        v = versions[e["flow"]]
        if v is None:
            skipped["no approved version"] += 1
            continue
        sent = conn.execute("SELECT step_index, created_at FROM outbox WHERE enrollment_id = ? ORDER BY step_index",
                            (e["id"],)).fetchall()
        done = {s["step_index"] for s in sent}
        nxt = next((i for i in range(len(v["steps"])) if i not in done), None)
        if nxt is None:
            conn.execute("UPDATE enrollments SET status = 'completed', ended_at = ? WHERE id = ? AND status = 'active'",
                         (fmt(now), e["id"]))
            continue
        due_at = parse_time(e["entered_at"]) + timedelta(hours=v["steps"][nxt]["delay_hours"])
        if sent:  # a missed tick never sends two emails in a row
            due_at = max(due_at, max(parse_time(s["created_at"]) for s in sent) + min_gap())
        if due_at <= now:
            due.append((due_at, e, v, nxt))
    due.sort(key=lambda d: (d[0], d[1]["id"]))
    return due, skipped


@app.post("/tick", dependencies=[Depends(require_key)])
def tick():
    """Send every step that is due, up to the daily cap. Nothing when paused."""
    with _tick_lock:
        return _tick()


def _tick() -> dict:
    now = now_dt()
    dry = dry_run()
    out = {"at": fmt(now), "dry_run": dry, "paused": False, "sent": 0, "failed": 0, "exited": 0,
           "capped": False, "cap": daily_cap(), "sent_today": 0, "by_flow": {}, "skipped": {}, "notices": []}
    with db() as conn:
        paused = paused_state(conn)
        if paused:
            out["paused"] = True
            key = paused.get("since") or "env"
            with write(conn):
                if meta_get(conn, "pause_notified") != key:  # say it once per pause, not every 15 minutes
                    meta_set(conn, "pause_notified", key)
                    out["notices"].append(f"Flows are paused ({paused.get('reason') or 'no reason given'}). Nothing is sent.")
            return out
        with write(conn):
            due, out["skipped"] = due_steps(conn, now)
        used = sent_today(conn, now)
        for due_at, e, v, idx in due:
            if used >= out["cap"]:
                out["capped"] = True
                break
            step = v["steps"][idx]
            with write(conn):
                cur = conn.execute("SELECT status FROM enrollments WHERE id = ?", (e["id"],)).fetchone()
                if cur["status"] != "active" or paused_state(conn):
                    continue
                again = approved_version(conn, e["flow"])
                if again is None or again["id"] != v["id"]:
                    continue  # approval changed during this tick: next tick
                why = exit_reason(conn, e, v)
                if why:
                    exit_enrollments(conn, e["email"], why, fmt(now), [e["flow"]])
                    out["exited"] += 1
                    continue
                try:
                    cursor = conn.execute(
                        "INSERT INTO outbox (enrollment_id, flow, version_id, step_index, email, arm, subject,"
                        " body_markdown, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (e["id"], e["flow"], v["id"], idx, e["email"], e["arm"], step["subject"],
                         step["body_markdown"], "dry_run" if dry else "pending", fmt(now)))
                except sqlite3.IntegrityError:
                    continue  # this step already went to this contact
                row_id = cursor.lastrowid
                if idx == len(v["steps"]) - 1:
                    conn.execute("UPDATE enrollments SET status = 'completed', ended_at = ? WHERE id = ?",
                                 (fmt(now), e["id"]))
            used += 1
            if not dry:
                # The row exists before the call: a crash or timeout here never sends it twice.
                try:
                    services.bridge_send(e["email"], step["subject"], step["body_markdown"], f"flow-outbox-{row_id}")
                    status, detail = "sent", None
                except services.ServiceError as exc:
                    status, detail = "failed", str(exc)[:300]
                    out["failed"] += 1
                with write(conn):
                    conn.execute("UPDATE outbox SET status = ?, detail = ? WHERE id = ?", (status, detail, row_id))
                if status == "failed":
                    continue
            out["sent"] += 1
            out["by_flow"][e["flow"]] = out["by_flow"].get(e["flow"], 0) + 1
        out["sent_today"] = used
        if out["capped"]:
            with write(conn):
                day = now.strftime("%Y-%m-%d")
                if meta_get(conn, "cap_notified") != day:
                    meta_set(conn, "cap_notified", day)
                    out["notices"].append(f"Daily cap of {out['cap']} emails reached; the rest wait for tomorrow.")
    return out


@app.delete("/contacts/{email}", dependencies=[Depends(require_key)])
def erase_contact(email: str, x_approver_key: str | None = Header(default=None)):
    """Erasure on request (GDPR art. 17): the contact, its events, enrollments and outbox rows are
    deleted. If they had unsubscribed, a salted hash of the address is kept so they are never
    mailed again. Results for past months change accordingly. Needs the approver key."""
    require_approver(x_approver_key)
    try:
        email = _email(email)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    with db() as conn, write(conn):
        c = conn.execute("SELECT suppressed_at FROM contacts WHERE email = ?", (email,)).fetchone()
        n = sum(conn.execute(f"DELETE FROM {t} WHERE email = ?", (email,)).rowcount
                for t in ("outbox", "enrollments", "events", "contacts"))
        kept = bool(c and c["suppressed_at"])
        if kept:
            conn.execute("INSERT OR IGNORE INTO erased_unsubscribes (hash, erased_at) VALUES (?, ?)",
                         (erased_hash(conn, email), fmt(now_dt())))
    return {"erased": n > 0, "rows": n, "kept_unsubscribe": kept}


@app.post("/pause", dependencies=[Depends(require_key)])
def pause(req: PauseIn | None = None):
    """Kill switch. Anyone with the service key can stop sending; resuming needs the approver key."""
    with db() as conn, write(conn):
        if not meta_get(conn, "paused"):
            meta_set(conn, "paused", json.dumps({"since": fmt(now_dt()), "reason": (req.reason if req else None)}))
        return {"paused": True, "state": paused_state(conn)}


@app.post("/resume", dependencies=[Depends(require_key)])
def resume(x_approver_key: str | None = Header(default=None)):
    require_approver(x_approver_key)
    with db() as conn, write(conn):
        meta_set(conn, "paused", None)
        still = paused_state(conn)
        return {"paused": still is not None, "state": still}


@app.get("/outbox", dependencies=[Depends(require_key)])
def outbox(flow: str | None = None, status: str | None = None,
           limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0)):
    where, args = [], []
    if flow:
        where.append("flow = ?")
        args.append(flow)
    if status:
        where.append("status = ?")
        args.append(status)
    sql = "SELECT * FROM outbox" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ? OFFSET ?"
    with db() as conn:
        return [dict(r) for r in conn.execute(sql, (*args, limit, offset))]


# ---------- results


@app.get("/flows/{name}/results", dependencies=[Depends(require_key)])
def results(name: str, days: int = Query(30, ge=1, le=365)):
    """Flow arm vs holdout arm for contacts who entered in the last `days` days. An outcome counts
    when it happened after the contact entered. No opens: Apple Mail Privacy Protection opens
    every email on its own, so open rates say nothing."""
    now = now_dt()
    since = fmt(now - timedelta(days=days))
    with db() as conn:
        f = flow_or_404(conn, name)
        arms = {a: {"entered": 0, "emails_sent": 0, "received_any": 0, "clicked": 0, "purchased": 0,
                    "unsubscribed": 0} for a in ("flow", "holdout")}
        base = "FROM enrollments e WHERE e.flow = ? AND e.entered_at >= ?"
        for r in conn.execute(f"SELECT e.arm, COUNT(*) AS n {base} GROUP BY e.arm", (name, since)):
            arms[r["arm"]]["entered"] = r["n"]
        for r in conn.execute("SELECT e.arm, COUNT(o.id) AS n, COUNT(DISTINCT o.enrollment_id) AS c"
                              " FROM enrollments e JOIN outbox o ON o.enrollment_id = e.id"
                              f" WHERE e.flow = ? AND e.entered_at >= ? AND o.status IN {COUNTED} GROUP BY e.arm",
                              (name, since)):
            arms[r["arm"]]["emails_sent"], arms[r["arm"]]["received_any"] = r["n"], r["c"]
        for outcome in ("clicked", "purchased", "unsubscribed"):
            for r in conn.execute(
                    f"SELECT e.arm, COUNT(*) AS n {base} AND EXISTS (SELECT 1 FROM events v WHERE v.email = e.email"
                    " AND v.type = ? AND v.at >= e.entered_at) GROUP BY e.arm", (name, since, outcome)):
                arms[r["arm"]][outcome] = r["n"]
    fl, ho = arms["flow"], arms["holdout"]
    compare = {k: stats.difference(fl[k], fl["entered"], ho[k], ho["entered"], min_n())
               for k in ("clicked", "purchased", "unsubscribed")}
    notes = ["Rates are contacts with the outcome / contacts who entered. The interval is a 95 % "
             "two-proportion interval (normal approximation). Report the interval, not a win.",
             "Opens are not measured: Apple Mail Privacy Protection opens emails by itself."]
    if f["mode"] == "aa":
        notes.insert(0, "A/A mode: both arms get the same emails. A significant difference here means "
                        "the assignment or the tracking is broken, not that the emails work.")
    return {"flow": name, "mode": f["mode"], "days": days, "since": since, "holdout_pct": f["holdout_pct"],
            "min_n": min_n(), "arms": arms, "compare": compare, "notes": notes}
