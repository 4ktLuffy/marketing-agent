"""Content calendar: the one place every draft, approval and publication is recorded."""
import hashlib
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
    # idea -> rejected: an idea that will not be written (e.g. the content engine re-planned
    # its slot away), kept with a note instead of deleted.
    "idea": ["draft", "rejected"],
    "draft": ["in_review", "rejected"],
    "in_review": ["approved", "draft", "rejected"],
    "approved": ["published", "draft"],
    "rejected": ["draft"],
    "published": [],
}
# Once a human approved an item, its content is frozen: move it back to draft to edit.
CONTENT_FIELDS = {"title", "channel", "body", "link", "hook_style", "image_url", "video_url"}
FROZEN_STATUSES = {"approved", "published"}
COLUMNS = (
    "id", "title", "channel", "body", "status", "scheduled_at", "published_at",
    "campaign", "link", "external_url", "notes", "created_at", "updated_at",
    "campaign_id", "short_url", "hook_style", "image_url", "video_url", "origin",
)
# A new version is written when one of these changes (what a reviewer approves).
VERSIONED_FIELDS = ("body", "image_url", "video_url")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
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
    short_url TEXT,
    hook_style TEXT,
    image_url TEXT,
    video_url TEXT
);
CREATE INDEX IF NOT EXISTS items_status_scheduled ON items (status, scheduled_at);
-- Every body/media version of an item, append-only. n starts at 1.
CREATE TABLE IF NOT EXISTS item_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    n INTEGER NOT NULL,
    body TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    image_url TEXT,
    video_url TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (item_id, n)
);
-- Who did what to an item and when: creation, every edit, every status change.
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    actor TEXT,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    body_sha256 TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_item ON audit (item_id, id);
-- Client approval links (app/client_links.py): only hashes of the token and PIN are kept.
CREATE TABLE IF NOT EXISTS client_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_sha256 TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL DEFAULT '',
    item_ids TEXT NOT NULL,
    pin_sha256 TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    uses INTEGER NOT NULL DEFAULT 0,
    wrong_pins INTEGER NOT NULL DEFAULT 0
);
"""
# An item with the number of its latest version.
ITEM_SELECT = ("SELECT items.*, (SELECT MAX(n) FROM item_versions v WHERE v.item_id = items.id) AS version"
               " FROM items")
# Columns added after the first release. A database created by an older version gets
# them via ALTER TABLE ADD COLUMN on first use; existing rows read them as NULL.
ADDED_COLUMNS = {"campaign_id": "INTEGER", "short_url": "TEXT", "hook_style": "TEXT", "image_url": "TEXT",
                 "video_url": "TEXT", "origin": "TEXT", "require_bound": "INTEGER DEFAULT 0"}
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
        backfill_versions(conn)
        conn.commit()
        _migrated.add(path)


def backfill_versions(conn: sqlite3.Connection) -> None:
    """Items from before versions existed get their current body as version 1 (once: an item
    that has a version is skipped, and another process doing the same is ignored)."""
    rows = conn.execute(
        "SELECT id, body, image_url, video_url, updated_at FROM items"
        " WHERE NOT EXISTS (SELECT 1 FROM item_versions v WHERE v.item_id = items.id)").fetchall()
    for r in rows:
        conn.execute(
            "INSERT OR IGNORE INTO item_versions (item_id, n, body, body_sha256, image_url, video_url,"
            " created_by, created_at) VALUES (?, 1, ?, ?, ?, ?, 'migration', ?)",
            (r["id"], r["body"], body_hash(r["body"]), r["image_url"], r["video_url"], r["updated_at"]))


def body_hash(body: str) -> str:
    """Canonical hash of a body: CRLF -> LF, outer whitespace stripped, UTF-8, sha256 hex."""
    return hashlib.sha256(body.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def row_to_item(row: sqlite3.Row) -> dict:
    item = {k: row[k] for k in COLUMNS}
    item["require_bound"] = bool(row["require_bound"])
    item["body_sha256"] = body_hash(row["body"])
    item["version"] = row["version"]
    return item


def get_or_404(conn: sqlite3.Connection, item_id: int) -> dict:
    if not 0 < item_id < 2**63:  # SQLite integers are 64-bit; a larger id raised OverflowError (500)
        raise HTTPException(404, f"item {item_id} not found")
    row = conn.execute(f"{ITEM_SELECT} WHERE id = ?", (item_id,)).fetchone()
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


def add_version(conn: sqlite3.Connection, item: dict, actor: str) -> None:
    n = conn.execute("SELECT COALESCE(MAX(n), 0) + 1 FROM item_versions WHERE item_id = ?",
                     (item["id"],)).fetchone()[0]
    conn.execute(
        "INSERT INTO item_versions (item_id, n, body, body_sha256, image_url, video_url, created_by,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item["id"], n, item["body"], item["body_sha256"], item["image_url"], item["video_url"], actor,
         item["updated_at"]))


def add_audit(conn: sqlite3.Connection, item: dict, actor: str, action: str, from_status: str | None,
              detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO audit (item_id, at, actor, action, from_status, to_status, body_sha256, detail)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item["id"], now_utc(), actor, action, from_status, item["status"], item["body_sha256"], detail))


def clean_actor(value: str | None) -> str | None:
    value = " ".join((value or "").split())[:80]
    return value or None


def actor_of(x_actor: str | None, note: str | None = None) -> str:
    """Who acted: the X-Actor header, else "by X" in the note (n8n writes "approved by Alex"),
    else "unknown"."""
    if clean_actor(x_actor):
        return clean_actor(x_actor)
    m = re.search(r"\bby ([^\s:;,()]+)", note or "")
    return clean_actor(m.group(1)) if m else "unknown"


def changed_since_you_looked(item: dict) -> HTTPException:
    return HTTPException(409, {"message": "changed since you looked", "current_sha256": item["body_sha256"]})


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def require_approver(x_approver_key: str | None) -> None:
    """Approving and publishing need APPROVER_KEY too; unset refuses (503), there is no open mode.

    Only n8n's approval form and publisher hold it. Every service that can write drafts holds
    INTERNAL_API_KEY; without this, any of them could approve its own copy.
    """
    expected = os.environ.get("APPROVER_KEY")
    if not expected:
        raise HTTPException(503, "APPROVER_KEY is not configured on this service; approving and publishing are refused")
    if not (x_approver_key and hmac.compare_digest(x_approver_key, expected)):
        raise HTTPException(403, "approving or publishing needs the approver key (X-Approver-Key)")


# ---------- models


def _utc_or_none(v):
    return None if v is None or v == "" else to_utc_iso(v)


def _hook_or_none(v):
    """Hook style label (e.g. "question"): trimmed, lower-case; blank means none."""
    if v is None:
        return None
    v = v.strip().lower()
    if len(v) > 40:
        raise ValueError("hook_style is at most 40 characters")
    return v or None


def _http_url_or_none(v, field: str):
    """An http(s) URL of at most 2000 characters; blank means none."""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if len(v) > 2000:
        raise ValueError(f"{field} is at most 2000 characters")
    if not re.match(r"^https?://[^\s/]+[^\s]*$", v):
        raise ValueError(f"{field} must be an http(s) URL")
    return v


def _image_url_or_none(v):
    """Image for the post (e.g. a card from 17-image-cards)."""
    return _http_url_or_none(v, "image_url")


def _video_url_or_none(v):
    """Video for the post (e.g. an MP4 from 71-video-assembly)."""
    return _http_url_or_none(v, "video_url")


def _hex64_or_none(v):
    if v is None:
        return None
    if not isinstance(v, str) or not HEX64.match(v):
        raise ValueError("must be a sha256 hex digest: 64 characters 0-9a-f")
    return v


def _origin_or_none(v):
    """Where the item came from, e.g. "task-bridge": trimmed, lower-case, at most 40; blank = none."""
    if v is None:
        return None
    v = v.strip().lower()
    if len(v) > 40 or not re.match(r"^[a-z0-9._-]*$", v):
        raise ValueError("origin is at most 40 characters of a-z 0-9 . _ -")
    return v or None


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
    hook_style: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    origin: str | None = None
    # True: approving needs the hash of the text the reviewer saw (expected_sha256/expected_body).
    require_bound_approval: bool = False

    @field_validator("hook_style")
    @classmethod
    def hook(cls, v):
        return _hook_or_none(v)

    @field_validator("image_url")
    @classmethod
    def image(cls, v):
        return _image_url_or_none(v)

    @field_validator("video_url")
    @classmethod
    def video(cls, v):
        return _video_url_or_none(v)

    @field_validator("origin")
    @classmethod
    def origin_label(cls, v):
        return _origin_or_none(v)

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
    hook_style: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    # Not stored: the body_sha256 the editor started from; a different current body is a 409.
    if_match_sha256: str | None = None

    @field_validator("if_match_sha256", mode="before")
    @classmethod
    def if_match(cls, v):
        return _hex64_or_none(v)

    @field_validator("scheduled_at")
    @classmethod
    def sched(cls, v):
        return _utc_or_none(v)

    @field_validator("hook_style")
    @classmethod
    def hook(cls, v):
        return _hook_or_none(v)

    @field_validator("image_url")
    @classmethod
    def image(cls, v):
        return _image_url_or_none(v)

    @field_validator("video_url")
    @classmethod
    def video(cls, v):
        return _video_url_or_none(v)


class StatusChange(BaseModel):
    status: str
    note: str | None = None
    # Approving: the body the reviewer saw, as its hash or as the text itself (hashed here).
    expected_sha256: str | None = None
    expected_body: str | None = None

    @field_validator("expected_sha256", mode="before")
    @classmethod
    def expected(cls, v):
        return _hex64_or_none(v)


class Published(BaseModel):
    external_url: str | None = None
    short_url: str | None = None


class NoteLine(BaseModel):
    """One line appended to `notes`, in any status (e.g. "sent to CMS as draft: <url>")."""
    note: str
    external_url: str | None = None

    @field_validator("note")
    @classmethod
    def note_text(cls, v: str) -> str:
        v = " ".join(v.split())  # one line: notes are read line by line
        if not v:
            raise ValueError("must not be empty")
        if len(v) > 1000:
            raise ValueError("note is at most 1000 characters")
        return v

    @field_validator("external_url")
    @classmethod
    def ext_url(cls, v):
        if v is None or not v.strip():
            return None
        v = v.strip()
        if len(v) > 2000 or not re.match(r"^https?://[^\s/]+[^\s]*$", v):
            raise ValueError("external_url must be an http(s) URL of at most 2000 characters")
        return v


# ---------- endpoints


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/items", status_code=201, dependencies=[Depends(require_key)])
def create_item(req: NewItem, x_actor: str | None = Header(default=None)):
    ts = now_utc()
    actor = actor_of(x_actor)
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO items (title, channel, body, status, scheduled_at, campaign,"
            " campaign_id, link, notes, hook_style, image_url, video_url, origin, require_bound,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (req.title, req.channel, req.body, req.status, req.scheduled_at, req.campaign,
             req.campaign_id, req.link, req.notes, req.hook_style, req.image_url, req.video_url,
             req.origin, int(req.require_bound_approval), ts, ts),
        )
        item = get_or_404(conn, cur.lastrowid)
        add_version(conn, item, actor)
        add_audit(conn, item, actor, "created", None, f"origin {req.origin}" if req.origin else None)
        return get_or_404(conn, item["id"])


@app.get("/items")
def list_items(
    status: str | None = Query(None, description="one status or a comma list"),
    channel: str | None = None,
    campaign_id: int | None = Query(None, description="only items of this campaign"),
    hook_style: str | None = Query(None, description="only items with this hook style"),
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
    if hook_style and hook_style.strip():
        where.append("hook_style = ?")
        args.append(hook_style.strip().lower())
    if from_:
        where.append("scheduled_at >= ?")
        args.append(parse_query_time(from_, "from"))
    if to:
        where.append("scheduled_at <= ?")
        args.append(parse_query_time(to, "to", end_of_day=True))
    sql = ITEM_SELECT
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
def patch_item(item_id: int, req: ItemPatch, x_actor: str | None = Header(default=None)):
    changes = {k: getattr(req, k) for k in req.model_fields_set if k != "if_match_sha256"}
    for k in ("title", "channel", "body"):
        if k in changes and (changes[k] is None or not changes[k].strip()):
            raise HTTPException(422, f"{k} must not be empty")
    with db() as conn:
        item = get_or_404(conn, item_id)
        if req.if_match_sha256 and req.if_match_sha256 != item["body_sha256"]:
            raise changed_since_you_looked(item)
        if not changes:
            return item
        frozen = CONTENT_FIELDS & changes.keys()
        if item["status"] in FROZEN_STATUSES and frozen:
            raise HTTPException(409, {
                "message": f"item is {item['status']}; move it back to draft to edit {sorted(frozen)}",
                "current": item["status"],
                "allowed": TRANSITIONS[item["status"]],
            })
        actor = actor_of(x_actor)
        new = update(conn, item_id, changes)
        if any(new[k] != item[k] for k in VERSIONED_FIELDS):
            add_version(conn, new, actor)
        add_audit(conn, new, actor, "edit", item["status"], "changed " + ", ".join(sorted(changes)))
        return get_or_404(conn, item_id)


@app.post("/items/{item_id}/status", dependencies=[Depends(require_key)])
def change_status(item_id: int, req: StatusChange, x_approver_key: str | None = Header(default=None),
                  x_actor: str | None = Header(default=None)):
    if req.status not in STATUSES:
        raise HTTPException(422, f"unknown status {req.status!r}; use {list(STATUSES)}")
    if req.status in ("approved", "published"):
        require_approver(x_approver_key)
    with db() as conn:
        item = get_or_404(conn, item_id)
        current, allowed = item["status"], TRANSITIONS[item["status"]]
        if req.status not in allowed:
            raise HTTPException(409, {
                "message": f"cannot move from {current} to {req.status}",
                "current": current,
                "allowed": allowed,
            })
        bound = False
        if req.status == "approved":
            bound = check_bound(item, req)
        ts = now_utc()
        fields = {"status": req.status}
        if req.status == "published":
            fields["published_at"] = ts
        note = req.note.strip() if req.note and req.note.strip() else None
        if note:
            line = f"[{ts}] {current} -> {req.status}: {note}"
            fields["notes"] = append_note(item["notes"], line)
        new = update(conn, item_id, fields)
        detail = "; ".join(p for p in (f"bound to {new['body_sha256']}" if bound else None, note) if p) or None
        add_audit(conn, new, actor_of(x_actor, note), "status", current, detail)
        return new


def check_bound(item: dict, req: StatusChange) -> bool:
    """Approving: the text the reviewer saw must be the current text. True when a hash was checked.

    An item created with require_bound_approval must send one (428 otherwise); older items may
    approve without, and are still checked when one is sent."""
    seen = [x for x in (req.expected_sha256,
                        body_hash(req.expected_body) if req.expected_body is not None else None) if x]
    if not seen:
        if item["require_bound"]:
            raise HTTPException(428, "this item needs a bound approval: send expected_sha256 (the body_sha256 "
                                     "the reviewer saw) or expected_body (the text the reviewer approved)")
        return False
    if any(s != item["body_sha256"] for s in seen):
        raise changed_since_you_looked(item)
    return True


@app.post("/items/{item_id}/published", dependencies=[Depends(require_key)])
def mark_published(item_id: int, req: Published | None = None, x_approver_key: str | None = Header(default=None),
                   x_actor: str | None = Header(default=None)):
    require_approver(x_approver_key)
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
        new = update(conn, item_id, fields)
        add_audit(conn, new, actor_of(x_actor), "status", item["status"], "published")
        return new


@app.post("/items/{item_id}/notes", dependencies=[Depends(require_key)])
def add_note(item_id: int, req: NoteLine):
    """Append a timestamped line to notes without changing the status (the publisher records
    "sent to CMS as draft" here, so an approved blog item is not sent again). Optionally sets
    external_url. Content stays frozen; notes and external_url are not content."""
    with db() as conn:
        item = get_or_404(conn, item_id)
        fields = {"notes": append_note(item["notes"], f"[{now_utc()}] {req.note}")}
        if req.external_url:
            fields["external_url"] = req.external_url
        return update(conn, item_id, fields)


@app.get("/items/{item_id}/versions", dependencies=[Depends(require_key)])
def item_versions(item_id: int):
    with db() as conn:
        get_or_404(conn, item_id)
        rows = conn.execute(
            "SELECT n, body, body_sha256, image_url, video_url, created_by, created_at FROM item_versions"
            " WHERE item_id = ? ORDER BY n", (item_id,))
        return [dict(r) for r in rows]


@app.get("/items/{item_id}/audit", dependencies=[Depends(require_key)])
def item_audit(item_id: int):
    with db() as conn:
        get_or_404(conn, item_id)
        rows = conn.execute(
            "SELECT at, actor, action, from_status, to_status, body_sha256, detail FROM audit"
            " WHERE item_id = ? ORDER BY id", (item_id,))
        return [dict(r) for r in rows]


@app.get("/due")
def due(now: str | None = None):
    cutoff = parse_query_time(now, "now") if now else now_utc()
    with db() as conn:
        rows = conn.execute(
            f"{ITEM_SELECT} WHERE status = 'approved' AND scheduled_at IS NOT NULL"
            " AND scheduled_at <= ? ORDER BY scheduled_at, id",
            (cutoff,),
        )
        return [row_to_item(r) for r in rows]


# ---------- client approval links (Phase 2): registered last, they use the helpers above
from . import client_links  # noqa: E402

app.include_router(client_links.router)
