"""Learning service: turns a reviewer's approvals, edits and rejections into examples and rules.

Every review decision is recorded as an event. Edited finals become few-shot examples
("the reviewer's own words"). Edits and rejections are reflected on by the LLM (via the
gateway) into short candidate rules; a human activates or rejects them, and the active
ones are served as a summary the gateway appends to the brand text.
"""
import hmac
import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger("learning-service")
app = FastAPI(title="learning-service")

DECISIONS = ("approved", "edited", "rejected")
REFLECTED_DECISIONS = ("edited", "rejected")
RULE_STATUSES = ("pending", "active", "rejected")
RULE_MAX_CHARS = 200
EVENT_COLUMNS = (
    "id", "item_id", "channel", "campaign_id", "decision", "draft", "final", "reason",
    "reviewer", "created_at", "reflected_at",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    campaign_id INTEGER,
    decision TEXT NOT NULL,
    draft TEXT NOT NULL,
    final TEXT,
    reason TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL,
    reflected_at TEXT
);
CREATE INDEX IF NOT EXISTS events_item ON events (item_id, decision);
CREATE INDEX IF NOT EXISTS events_decision_created ON events (decision, created_at);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    norm TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    source_event_ids TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rules_norm ON rules (norm);
"""


# ---------- time: stored as "YYYY-MM-DDTHH:MM:SSZ" so strings sort correctly


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    return fmt(datetime.now(timezone.utc))


def parse_since(value: str) -> str:
    # An unencoded "+02:00" in a query string arrives as " 02:00"; put the plus back.
    value = re.sub(r"(\d) (\d{2}:?\d{2})$", r"\1+\2", value.strip())
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"since: not an ISO 8601 date/time: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return fmt(dt)


# ---------- storage


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/learning.sqlite")


def gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://llm-gateway:8000").rstrip("/")


def gateway_timeout() -> float:
    return float(os.environ.get("GATEWAY_TIMEOUT", "300"))


@contextmanager
def db():
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_event(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in EVENT_COLUMNS}


def row_to_rule(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "text": row["text"],
        "scope": row["scope"],
        "status": row["status"],
        "source_event_ids": json.loads(row["source_event_ids"]),
        "created_at": row["created_at"],
    }


def normalize_rule(text: str) -> str:
    """Dedupe key: whitespace collapsed, case folded."""
    return " ".join(text.split()).casefold()


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- models


def _blank_to_none(v):
    if v is None:
        return None
    v = v.strip()
    return v or None


class NewEvent(BaseModel):
    item_id: int
    channel: str
    campaign_id: int | None = None
    decision: str
    draft: str
    final: str | None = None
    reason: str | None = None
    reviewer: str | None = None

    @field_validator("channel", "draft")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("decision")
    @classmethod
    def known_decision(cls, v: str) -> str:
        if v not in DECISIONS:
            raise ValueError(f"must be one of {list(DECISIONS)}")
        return v

    @field_validator("final", "reason", "reviewer")
    @classmethod
    def blank_none(cls, v):
        return _blank_to_none(v)


class ReflectRequest(BaseModel):
    since_days: int = Field(7, ge=1, le=3650)
    max_events: int = Field(20, ge=1, le=200)


class RuleStatus(BaseModel):
    status: str


# ---------- reflection


class ReflectError(Exception):
    pass


def reflect_one(event: dict) -> dict:
    """Ask the gateway for a rule. Returns {"generalizable","rule","scope"} or raises ReflectError."""
    variables = {k: (event[k] or None) for k in ("draft", "final", "reason", "channel")}
    try:
        r = httpx.post(
            f"{gateway_url()}/v1/run",
            json={"prompt": "reflect_rule", "vars": variables},
            headers={"X-API-Key": os.environ["INTERNAL_API_KEY"]} if os.getenv("INTERNAL_API_KEY") else {},
            timeout=gateway_timeout(),
        )
    except httpx.HTTPError as exc:
        raise ReflectError(f"gateway unreachable: {exc}") from exc
    if r.status_code != 200:
        raise ReflectError(f"gateway {r.status_code}: {r.text[:300]}")
    try:
        output = r.json().get("output")
    except ValueError as exc:
        raise ReflectError("gateway returned non-JSON") from exc
    if not isinstance(output, dict) or not isinstance(output.get("generalizable"), bool):
        raise ReflectError(f"unexpected gateway output: {str(output)[:300]}")
    return output


def rule_from_output(output: dict, event: dict) -> tuple[str, str] | None:
    """(text, scope) for a usable rule, or None."""
    if not output.get("generalizable"):
        return None
    text = output.get("rule")
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())[:RULE_MAX_CHARS].strip()
    if not text:
        return None
    scope = output.get("scope")
    scope = scope.strip().lower() if isinstance(scope, str) and scope.strip() else "all"
    return text, scope


# ---------- endpoints


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/events", status_code=201, dependencies=[Depends(require_key)])
def create_event(req: NewEvent):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO events (item_id, channel, campaign_id, decision, draft, final, reason,"
            " reviewer, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (req.item_id, req.channel, req.campaign_id, req.decision, req.draft, req.final,
             req.reason, req.reviewer, now_utc()),
        )
        row = conn.execute("SELECT * FROM events WHERE id = ?", (cur.lastrowid,)).fetchone()
        return row_to_event(row)


@app.get("/events")
def list_events(decision: str | None = None, since: str | None = None):
    where, args = [], []
    if decision:
        if decision not in DECISIONS:
            raise HTTPException(422, f"unknown decision {decision!r}; use {list(DECISIONS)}")
        where.append("decision = ?")
        args.append(decision)
    if since:
        where.append("created_at >= ?")
        args.append(parse_since(since))
    sql = "SELECT * FROM events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    with db() as conn:
        return [row_to_event(r) for r in conn.execute(sql, args)]


@app.get("/items/{item_id}/attempts")
def attempts(item_id: int):
    with db() as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM events WHERE item_id = ? AND decision = 'rejected'", (item_id,)
        ).fetchone()
    return {"item_id": item_id, "rejections": n}


@app.get("/examples")
def examples(channel: str | None = None, k: int = Query(3, ge=1, le=10)):
    where = ["((decision = 'edited' AND final IS NOT NULL) OR decision = 'approved')"]
    args: list = []
    if channel and channel.strip():
        where.append("lower(channel) = lower(?)")
        args.append(channel.strip())
    sql = (
        "SELECT decision, channel, COALESCE(final, draft) AS text FROM events WHERE "
        + " AND ".join(where)
        + " ORDER BY CASE decision WHEN 'edited' THEN 0 ELSE 1 END, id DESC LIMIT ?"
    )
    with db() as conn:
        rows = conn.execute(sql, (*args, k)).fetchall()
    return [{"text": r["text"], "channel": r["channel"], "decision": r["decision"]} for r in rows]


@app.post("/reflect", dependencies=[Depends(require_key)])
def reflect(req: ReflectRequest | None = None):
    req = req or ReflectRequest()
    cutoff = fmt(datetime.now(timezone.utc) - timedelta(days=req.since_days))
    with db() as conn:
        events = [row_to_event(r) for r in conn.execute(
            "SELECT * FROM events WHERE decision IN ('edited', 'rejected')"
            " AND reflected_at IS NULL AND created_at >= ? ORDER BY id LIMIT ?",
            (cutoff, req.max_events),
        )]

    # Gateway calls are slow (seconds each); don't hold a DB connection across them.
    results, errors = [], []
    for event in events:
        try:
            results.append((event, reflect_one(event)))
        except ReflectError as exc:
            log.warning("reflect event %s failed: %s", event["id"], exc)
            errors.append({"event_id": event["id"], "error": str(exc)})

    created_ids: list[int] = []
    with db() as conn:
        for event, output in results:
            conn.execute("UPDATE events SET reflected_at = ? WHERE id = ?", (now_utc(), event["id"]))
            rule = rule_from_output(output, event)
            if rule is None:
                continue
            text, scope = rule
            norm = normalize_rule(text)
            existing = conn.execute(
                "SELECT * FROM rules WHERE norm = ? ORDER BY id LIMIT 1", (norm,)
            ).fetchone()
            if existing is not None:
                # Already known. A pending one records another source; active/rejected
                # rules are left alone so a rejected rule is never proposed again.
                if existing["status"] == "pending":
                    sources = json.loads(existing["source_event_ids"])
                    if event["id"] not in sources:
                        sources.append(event["id"])
                        conn.execute("UPDATE rules SET source_event_ids = ? WHERE id = ?",
                                     (json.dumps(sources), existing["id"]))
                continue
            cur = conn.execute(
                "INSERT INTO rules (text, norm, scope, status, source_event_ids, created_at)"
                " VALUES (?, ?, ?, 'pending', ?, ?)",
                (text, norm, scope, json.dumps([event["id"]]), now_utc()),
            )
            created_ids.append(cur.lastrowid)
        created = [row_to_rule(conn.execute("SELECT * FROM rules WHERE id = ?", (i,)).fetchone())
                   for i in created_ids]
    return {"created": created, "reflected": len(results), "errors": errors}


@app.get("/rules")
def list_rules(status: str | None = None):
    sql, args = "SELECT * FROM rules", []
    if status:
        if status not in RULE_STATUSES:
            raise HTTPException(422, f"unknown status {status!r}; use {list(RULE_STATUSES)}")
        sql += " WHERE status = ?"
        args.append(status)
    with db() as conn:
        return [row_to_rule(r) for r in conn.execute(sql + " ORDER BY id", args)]


@app.get("/rules/summary")
def rules_summary():
    with db() as conn:
        rows = conn.execute("SELECT * FROM rules WHERE status = 'active' ORDER BY id").fetchall()
    if not rows:
        return {"summary": "", "count": 0}
    lines = ["Rules learned from your edits:"]
    for r in rows:
        prefix = "" if r["scope"] == "all" else f"[{r['scope']}] "
        lines.append(f"- {prefix}{r['text']}")
    return {"summary": "\n".join(lines), "count": len(rows)}


@app.post("/rules/{rule_id}/status", dependencies=[Depends(require_key)])
def set_rule_status(rule_id: int, req: RuleStatus):
    if req.status not in ("active", "rejected"):
        raise HTTPException(422, "status must be 'active' or 'rejected'")
    with db() as conn:
        if conn.execute("SELECT 1 FROM rules WHERE id = ?", (rule_id,)).fetchone() is None:
            raise HTTPException(404, f"rule {rule_id} not found")
        conn.execute("UPDATE rules SET status = ? WHERE id = ?", (req.status, rule_id))
        return row_to_rule(conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone())
