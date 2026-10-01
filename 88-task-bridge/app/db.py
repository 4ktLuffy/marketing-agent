"""SQLite storage. Drafts and events are append-only (triggers refuse UPDATE and DELETE)."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    audience TEXT,
    notes TEXT,
    pieces TEXT NOT NULL,
    scope TEXT NOT NULL,
    publish_on TEXT NOT NULL,
    pack TEXT NOT NULL,
    pack_sha256 TEXT NOT NULL,
    fact_set_version TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    snapshot_facts TEXT NOT NULL,
    share_preview TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks (id),
    parent_id INTEGER,
    kind TEXT NOT NULL,
    provider TEXT,
    raw_text TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    raw_chars INTEGER NOT NULL,
    split TEXT NOT NULL,
    problems TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pieces (
    task_id TEXT NOT NULL REFERENCES tasks (id),
    piece_key TEXT NOT NULL,
    n INTEGER NOT NULL,
    channel TEXT NOT NULL,
    draft_id INTEGER,
    filled_text TEXT,
    filled_sha256 TEXT,
    blocked INTEGER NOT NULL DEFAULT 0,
    findings TEXT NOT NULL DEFAULT '[]',
    checks TEXT NOT NULL DEFAULT '{}',
    used_facts TEXT NOT NULL DEFAULT '[]',
    item_id INTEGER,
    state TEXT NOT NULL DEFAULT 'waiting',
    stale TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, piece_key)
);
CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks (id),
    format TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    manifest TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    task_id TEXT,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS drafts_task ON drafts (task_id, id);
CREATE INDEX IF NOT EXISTS events_task ON events (task_id, id);
CREATE INDEX IF NOT EXISTS tasks_created ON tasks (created_at);
CREATE TRIGGER IF NOT EXISTS drafts_no_update BEFORE UPDATE ON drafts
BEGIN SELECT RAISE(ABORT, 'drafts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS drafts_no_delete BEFORE DELETE ON drafts
BEGIN SELECT RAISE(ABORT, 'drafts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END
"""
_migrate_lock = threading.Lock()
_migrated: set[str] = set()


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now() -> str:
    return fmt(datetime.now(timezone.utc))


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/tasks.sqlite")


def migrate(conn: sqlite3.Connection) -> None:
    path = db_path()
    if path in _migrated:
        return
    with _migrate_lock:
        if path in _migrated:
            return
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
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


def event(conn, task_id: str | None, kind: str, **detail) -> None:
    """Audit line. Never the pasted text: lengths, hashes, ids and keys only."""
    conn.execute("INSERT INTO events (at, task_id, kind, detail) VALUES (?, ?, ?, ?)",
                 (now(), task_id, kind, json.dumps(detail, ensure_ascii=False, sort_keys=True)))


def meta_get(conn, k: str, default: str | None = None) -> str | None:
    r = conn.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return r["v"] if r else default


def meta_set(conn, k: str, v: str) -> None:
    conn.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v", (k, v))


def approved_examples(conn, exclude_task: str | None = None, limit: int = 60) -> list[dict]:
    """Pieces that were approved and exported, newest export first, from this service's own records:
    the export manifest names each piece and the hash of the approved text; the piece row carries
    that text (filled_text) only while its hash still matches. Never the task `exclude_task`."""
    out, seen = [], set()
    rows = conn.execute("SELECT id, task_id, manifest FROM exports ORDER BY id DESC LIMIT 200").fetchall()
    for r in rows:
        if r["task_id"] == exclude_task:
            continue
        try:
            listed = json.loads(r["manifest"]).get("pieces") or []
        except (ValueError, AttributeError):
            continue
        for mp in listed:
            key, sha = mp.get("piece_key"), mp.get("sha256")
            if not key or not sha or (r["task_id"], key) in seen:
                continue
            p = conn.execute("SELECT channel, filled_text, filled_sha256 FROM pieces WHERE task_id = ? AND piece_key = ?",
                             (r["task_id"], key)).fetchone()
            if not p or not p["filled_text"] or p["filled_sha256"] != sha:
                continue
            seen.add((r["task_id"], key))
            out.append({"task_id": r["task_id"], "piece_key": key, "channel": p["channel"],
                        "text": p["filled_text"], "export_id": r["id"]})
        if len(out) >= limit:
            break
    return out
