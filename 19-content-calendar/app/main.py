"""Content calendar: the one place every draft, approval and publication is recorded."""
import hmac
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

app = FastAPI(title="content-calendar")

STATUSES = ("idea", "draft", "in_review", "approved", "rejected", "published")
INITIAL_STATUSES = ("idea", "draft", "in_review")
TRANSITIONS = {
    "idea": ["draft"],
    "draft": ["in_review", "rejected"],
    "in_review": ["approved", "draft", "rejected"],
    "approved": ["published", "draft"],
    "rejected": ["draft"],
    "published": [],
}
# Once a human approved an item, its content is frozen: move it back to draft to edit.
CONTENT_FIELDS = {"title", "channel", "body", "link"}
FROZEN_STATUSES = {"approved", "published"}
COLUMNS = (
    "id", "title", "channel", "body", "status", "scheduled_at", "published_at",
    "campaign", "link", "external_url", "notes", "created_at", "updated_at",
    "campaign_id", "short_url",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    channel TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    scheduled_at TEXT,
    published_at TEXT,
    campaign TEXT,
    link TEXT,
    external_url TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    campaign_id INTEGER,
    short_url TEXT
);
CREATE INDEX IF NOT EXISTS items_status_scheduled ON items (status, scheduled_at);
"""
# Columns added after the first release. A database created by an older version gets
# them via ALTER TABLE ADD COLUMN on first use; existing rows read them as NULL.
ADDED_COLUMNS = {"campaign_id": "INTEGER", "short_url": "TEXT"}
INDEXES = "CREATE INDEX IF NOT EXISTS items_campaign_id ON items (campaign_id);"
_migrate_lock = threading.Lock()
_migrated: set[str] = set()


# ---------- time: everything is stored as "YYYY-MM-DDTHH:MM:SSZ" so strings sort correctly


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    return fmt(datetime.now(timezone.utc))


def to_utc_iso(value: str, end_of_day: bool = False) -> str:
    """Parse ISO 8601 (with or without offset; naive means UTC) into the stored form."""
    # An unencoded "+02:00" in a query string arrives as " 02:00"; put the plus back.
    value = re.sub(r"(\d) (\d{2}:?\d{2})$", r"\1+\2", value.strip())
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"not an ISO 8601 date/time: {value!r}")
    if len(value) == 10 and end_of_day:  # a bare date used as an upper bound
        dt = dt.replace(hour=23, minute=59, second=59)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return fmt(dt)


def parse_query_time(value: str, name: str, end_of_day: bool = False) -> str:
    try:
        return to_utc_iso(value, end_of_day)
    except ValueError as e:
        raise HTTPException(422, f"{name}: {e}")


# ---------- storage


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/calendar.sqlite")


@contextmanager
def db():
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection) -> None:
    """Add columns missing from an older database. Runs once per DB path per process.

    Requests arrive in parallel (threads, or several workers), so two can race to add the
    same column: the lock covers threads, and "duplicate column" from another process is
    treated as done.
    """
    path = db_path()
    if path in _migrated:
        return
    with _migrate_lock:
        if path in _migrated:
            return
        have = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
        for name, sqltype in ADDED_COLUMNS.items():
            if name not in have:
                try:
                    conn.execute(f"ALTER TABLE items ADD COLUMN {name} {sqltype}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc):
                        raise
        conn.executescript(INDEXES)
        conn.commit()
        _migrated.add(path)


def row_to_item(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in COLUMNS}


def get_or_404(conn: sqlite3.Connection, item_id: int) -> dict:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"item {item_id} not found")
    return row_to_item(row)


def update(conn: sqlite3.Connection, item_id: int, fields: dict) -> dict:
    fields = {**fields, "updated_at": now_utc()}
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE items SET {sets} WHERE id = ?", (*fields.values(), item_id))
    return get_or_404(conn, item_id)


def append_note(existing: str | None, line: str) -> str:
    return f"{existing}\n{line}" if existing else line


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- models


def _utc_or_none(v):
    return None if v is None or v == "" else to_utc_iso(v)


class NewItem(BaseModel):
    title: str
    channel: str
    body: str
    status: str = "draft"
    scheduled_at: str | None = None
    campaign: str | None = None
    campaign_id: StrictInt | None = Field(default=None, ge=1)
    link: str | None = None
    notes: str | None = None

    @field_validator("title", "channel", "body")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("status")
    @classmethod
    def initial_status(cls, v: str) -> str:
        if v not in INITIAL_STATUSES:
            raise ValueError(f"new items must start as one of {list(INITIAL_STATUSES)}")
        return v

    @field_validator("scheduled_at")
    @classmethod
    def sched(cls, v):
        return _utc_or_none(v)


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")  # status is changed only via /status

    title: str | None = None
    channel: str | None = None
    body: str | None = None
    scheduled_at: str | None = None
    campaign: str | None = None
    campaign_id: StrictInt | None = Field(default=None, ge=1)
    link: str | None = None
    notes: str | None = None

    @field_validator("scheduled_at")
    @classmethod
    def sched(cls, v):
        return _utc_or_none(v)


class StatusChange(BaseModel):
    status: str
    note: str | None = None


class Published(BaseModel):
    external_url: str | None = None
    short_url: str | None = None


# ---------- endpoints


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/items", status_code=201, dependencies=[Depends(require_key)])
def create_item(req: NewItem):
    ts = now_utc()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO items (title, channel, body, status, scheduled_at, campaign,"
            " campaign_id, link, notes, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (req.title, req.channel, req.body, req.status, req.scheduled_at, req.campaign,
             req.campaign_id, req.link, req.notes, ts, ts),
        )
        return get_or_404(conn, cur.lastrowid)


@app.get("/items")
def list_items(
    status: str | None = Query(None, description="one status or a comma list"),
    channel: str | None = None,
    campaign_id: int | None = Query(None, description="only items of this campaign"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
):
    where, args = [], []
    wanted = [s.strip() for s in (status or "").split(",") if s.strip()]
    if wanted:
        unknown = [s for s in wanted if s not in STATUSES]
        if unknown:
            raise HTTPException(422, f"unknown status {unknown}; use {list(STATUSES)}")
        where.append(f"status IN ({','.join('?' * len(wanted))})")
        args += wanted
    if channel:
        where.append("channel = ?")
        args.append(channel.strip())
    if campaign_id is not None:
        where.append("campaign_id = ?")
        args.append(campaign_id)
    if from_:
        where.append("scheduled_at >= ?")
        args.append(parse_query_time(from_, "from"))
    if to:
        where.append("scheduled_at <= ?")
        args.append(parse_query_time(to, "to", end_of_day=True))
    sql = "SELECT * FROM items"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY scheduled_at IS NULL, scheduled_at, id"
    with db() as conn:
        return [row_to_item(r) for r in conn.execute(sql, args)]


@app.get("/items/{item_id}")
def get_item(item_id: int):
    with db() as conn:
        return get_or_404(conn, item_id)


@app.patch("/items/{item_id}", dependencies=[Depends(require_key)])
def patch_item(item_id: int, req: ItemPatch):
    changes = {k: getattr(req, k) for k in req.model_fields_set}
    for k in ("title", "channel", "body"):
        if k in changes and (changes[k] is None or not changes[k].strip()):
            raise HTTPException(422, f"{k} must not be empty")
    with db() as conn:
        item = get_or_404(conn, item_id)
        if not changes:
            return item
        frozen = CONTENT_FIELDS & changes.keys()
        if item["status"] in FROZEN_STATUSES and frozen:
            raise HTTPException(409, {
                "message": f"item is {item['status']}; move it back to draft to edit {sorted(frozen)}",
                "current": item["status"],
                "allowed": TRANSITIONS[item["status"]],
            })
        return update(conn, item_id, changes)


@app.post("/items/{item_id}/status", dependencies=[Depends(require_key)])
def change_status(item_id: int, req: StatusChange):
    if req.status not in STATUSES:
        raise HTTPException(422, f"unknown status {req.status!r}; use {list(STATUSES)}")
    with db() as conn:
        item = get_or_404(conn, item_id)
        current, allowed = item["status"], TRANSITIONS[item["status"]]
        if req.status not in allowed:
            raise HTTPException(409, {
                "message": f"cannot move from {current} to {req.status}",
                "current": current,
                "allowed": allowed,
            })
        ts = now_utc()
        fields = {"status": req.status}
        if req.status == "published":
            fields["published_at"] = ts
        if req.note and req.note.strip():
            line = f"[{ts}] {current} -> {req.status}: {req.note.strip()}"
            fields["notes"] = append_note(item["notes"], line)
        return update(conn, item_id, fields)


@app.post("/items/{item_id}/published", dependencies=[Depends(require_key)])
def mark_published(item_id: int, req: Published | None = None):
    with db() as conn:
        item = get_or_404(conn, item_id)
        if item["status"] != "approved":
            raise HTTPException(409, {
                "message": f"only approved items can be published; this one is {item['status']}",
                "current": item["status"],
                "allowed": TRANSITIONS[item["status"]],
            })
        fields = {"status": "published", "published_at": now_utc()}
        if req and req.external_url:
            fields["external_url"] = req.external_url
        if req and req.short_url:
            fields["short_url"] = req.short_url
        return update(conn, item_id, fields)


@app.get("/due")
def due(now: str | None = None):
    cutoff = parse_query_time(now, "now") if now else now_utc()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE status = 'approved' AND scheduled_at IS NOT NULL"
            " AND scheduled_at <= ? ORDER BY scheduled_at, id",
            (cutoff,),
        )
        return [row_to_item(r) for r in rows]
