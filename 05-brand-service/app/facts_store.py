"""Scoped, versioned fact store (v2) for the brand service.

A fact is one checkable statement with a stable key, a subject, a scope (sites, regions,
channels, segments, plan tiers, variants; empty = all), a validity window, a source and a
sensitivity. Contract: `_dev/phase1-contracts.md` §1-§2.

Storage is one SQLite file (`FACTS_DB`, default `/data/facts.sqlite`):

- `facts`         one row per key: which version is served, its status, a few indexed columns.
                  Mutable (status and the served version change).
- `fact_versions` every version ever written, append-only: SQLite triggers refuse UPDATE and
                  DELETE, so history cannot be rewritten through the service or by hand.
- `changes`       append-only log (`seq` only grows) that 88 polls with `/facts/changes?since=`.
- `open_questions`, `rules` (starter-kit rules, draft until the owner confirms), `meta`.
- `disclosure_wordings` a business's own words for a fact's required disclosure, proposed when a
                  person accepts a missing_disclosure finding; served on the fact only once the
                  owner confirms it. Deciding one never writes a `changes` row.

The brand.yaml / overrides facts are NOT stored here: they are "derived" facts computed from the
brand on every read (see main.py) and passed in where needed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

log = logging.getLogger("brand-service.facts")

# --- the Fact model (contract §1) ---------------------------------------------------------

KEY_PATTERN = r"^[a-z0-9-]{2,60}$"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
SubjectKind = Literal["business", "site", "product", "variant", "plan", "service", "package",
                      "menu_item", "person", "policy", "offer"]
FactType = Literal["price", "spec", "availability", "hours", "inclusion", "policy", "certification",
                   "credential", "claim", "testimonial", "result", "event", "contact"]
Basis = Literal["per_unit", "per_person", "per_room", "per_night", "per_seat", "per_month", "per_year", "flat"]
SourceKind = Literal["doc", "url", "certificate", "owner_statement"]
ClaimClass = Literal["none", "comparative", "regulated_health", "regulated_food", "safety_cert", "origin",
                     "price_reference", "security", "ai_capability", "result"]
Sensitivity = Literal["public", "internal", "restricted"]
Status = Literal["draft", "active", "expired", "superseded", "retired"]
Risk = Literal["low", "medium", "high"]

SENS_RANK = {"public": 0, "internal": 1, "restricted": 2}
SCOPE_DIMS = ("sites", "regions", "channels", "segments", "plan_tiers", "variants")
# query parameter -> scope dimension
QUERY_DIMS = {"site": "sites", "region": "regions", "channel": "channels", "segment": "segments",
              "plan_tier": "plan_tiers", "variant": "variants"}
# Fields a GET returns that a client may send back unchanged; they are dropped, never stored.
READ_ONLY = {"version", "latest_version", "derived", "versions", "updated_at", "superseded_by",
             "created_at", "created_by", "confirmed_by", "scope_note", "id", "disclosure_wordings"}


def _s(max_len: int, min_len: int = 0):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=min_len, max_length=max_len)]


Key = Annotated[str, StringConstraints(pattern=KEY_PATTERN)]
Day = Annotated[str, StringConstraints(pattern=DATE_PATTERN)]
Scalar = _s(200) | bool | int | float | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Subject(_Strict):
    kind: SubjectKind
    ref: _s(120) = ""


class Condition(_Strict):
    key: _s(60, 1)
    op: Literal["=", "!=", "<", "<=", ">", ">=", "in", "not_in"]
    value: Scalar | list[Scalar] = Field(default=None)


class Scope(_Strict):
    sites: list[_s(80, 1)] = Field(default=[], max_length=50)
    regions: list[_s(80, 1)] = Field(default=[], max_length=50)
    channels: list[_s(80, 1)] = Field(default=[], max_length=50)
    segments: list[_s(80, 1)] = Field(default=[], max_length=50)
    plan_tiers: list[_s(80, 1)] = Field(default=[], max_length=50)
    variants: list[_s(80, 1)] = Field(default=[], max_length=50)


class Source(_Strict):
    kind: SourceKind
    ref: _s(300) = ""


def _check_day(v: str | None, name: str) -> None:
    if v is None:
        return
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"{name} is not a real date (YYYY-MM-DD): {v}")


class FactIn(_Strict):
    key: Key
    subject: Subject
    fact_type: FactType
    attribute: _s(60) | None = None
    value: Scalar = None
    unit: _s(30) | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    value_text: _s(200) | None = None
    basis: Basis | None = None
    conditions: list[Condition] = Field(default=[], max_length=20)
    scope: Scope = Field(default_factory=Scope)
    valid_from: Day | None = None
    valid_to: Day | None = None
    review_by: Day | None = None
    source: Source | None = None
    claim_class: ClaimClass = "none"
    requires_evidence: bool = False
    evidence_ref: _s(200) | None = None
    consent_ref: _s(200) | None = None
    required_disclosures: list[_s(200, 1)] = Field(default=[], max_length=10)
    allowed_phrasing: list[_s(120, 1)] = Field(default=[], max_length=20)
    forbidden_phrasing: list[_s(120, 1)] = Field(default=[], max_length=20)
    owner: _s(80) | None = None
    risk: Risk = "low"
    # Default is internal: a fact nobody classified never leaves the business as text.
    sensitivity: Sensitivity = "internal"
    # Accepted (import, round-trips) but never stored in a version; POST/PUT ignore it.
    status: Status | None = None
    supersedes_key: Key | None = None
    text: _s(500, 1)

    @model_validator(mode="before")
    @classmethod
    def _drop_read_only(cls, data):
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in READ_ONLY}
        return data

    @model_validator(mode="after")
    def _dates(self):
        for name in ("valid_from", "valid_to", "review_by"):
            _check_day(getattr(self, name), name)
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_to must not be before valid_from")
        if self.supersedes_key and self.supersedes_key == self.key:
            raise ValueError("supersedes_key must name another fact")
        return self

    def stored(self) -> dict:
        """The version payload: everything but status."""
        return self.model_dump(exclude={"status"})


BLANK_FACT = FactIn(key="xx", subject={"kind": "business"}, fact_type="claim", text="x").stored()


def canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


# --- validity and scope (contract §1) -----------------------------------------------------

def today() -> str:
    """UTC date; `FACTS_TODAY` pins it (eval runs replay a fixed day)."""
    pinned = os.environ.get("FACTS_TODAY", "").strip()
    return pinned or datetime.now(timezone.utc).date().isoformat()


def validity_reason(data: dict, status: str, at: str) -> str | None:
    """None when the fact is valid on day `at`, else the reason it is not."""
    if status != "active":
        return status
    if data.get("valid_from") and at < data["valid_from"]:
        return "not_yet_valid"
    if data.get("valid_to") and at > data["valid_to"]:
        return "expired"
    return None


def sensitivity_reason(sensitivity: str, max_sensitivity: str) -> str | None:
    return sensitivity if SENS_RANK[sensitivity] > SENS_RANK[max_sensitivity] else None


def _fold(values) -> set[str]:
    return {str(v).strip().casefold() for v in values or [] if str(v).strip()}


def scope_reason(fact_scope: dict, task_scope: dict) -> str | None:
    """For every dimension where the fact lists values, the task must name at least one value
    and every named value must be in the fact's list. Naming another value is `out_of_scope`
    (it wins over the weaker `scope_unspecified`: the task left a dimension empty)."""
    worst = None
    for dim in SCOPE_DIMS:
        allowed = _fold((fact_scope or {}).get(dim))
        if not allowed:
            continue
        named = _fold(task_scope.get(dim))
        if not named:
            worst = "scope_unspecified"
        elif not named <= allowed:
            return "out_of_scope"
    return worst


def scope_note(fact_scope: dict) -> str | None:
    parts = [f"{dim.replace('_', ' ')} {', '.join(fact_scope[dim])}"
             for dim in SCOPE_DIMS if (fact_scope or {}).get(dim)]
    return "only: " + "; ".join(parts) if parts else None


def with_scope_note(text: str, note: str | None) -> str:
    if not note:
        return text
    return f"{text[:-1]} ({note})." if text.endswith(".") else f"{text} ({note})"


# --- storage ------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    key TEXT PRIMARY KEY,
    current_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    subject_kind TEXT, subject_ref TEXT, fact_type TEXT, sensitivity TEXT,
    valid_from TEXT, valid_to TEXT, review_by TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    created_by TEXT,
    confirmed_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (key, version)
);
CREATE TABLE IF NOT EXISTS changes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    version INTEGER,
    kind TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS open_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, fact_key TEXT, task_id TEXT, text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', answer TEXT,
    created_at TEXT NOT NULL, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kit TEXT NOT NULL, kind TEXT NOT NULL, phrase TEXT NOT NULL, why TEXT,
    claim_class TEXT, needs TEXT, status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL, UNIQUE (kit, kind, phrase)
);
CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS disclosure_wordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_key TEXT NOT NULL, disclosure TEXT NOT NULL, wording TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', proposed_by TEXT, task_id TEXT,
    created_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT
);
CREATE INDEX IF NOT EXISTS disclosure_wordings_key ON disclosure_wordings (fact_key, status);
CREATE TRIGGER IF NOT EXISTS fact_versions_no_update BEFORE UPDATE ON fact_versions
BEGIN SELECT RAISE(ABORT, 'fact_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS fact_versions_no_delete BEFORE DELETE ON fact_versions
BEGIN SELECT RAISE(ABORT, 'fact_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS changes_no_update BEFORE UPDATE ON changes
BEGIN SELECT RAISE(ABORT, 'changes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS changes_no_delete BEFORE DELETE ON changes
BEGIN SELECT RAISE(ABORT, 'changes is append-only'); END;
"""
# Columns beyond the first schema. An older database gets them via ALTER TABLE on first use.
ADDED_COLUMNS = {
    "facts": {"latest_version": "INTEGER", "supersedes_key": "TEXT"},
    "changes": {"actor": "TEXT"},
}
INDEXES = """
CREATE INDEX IF NOT EXISTS fact_versions_key ON fact_versions (key, version);
CREATE INDEX IF NOT EXISTS changes_key ON changes (key, version, kind);
CREATE INDEX IF NOT EXISTS facts_supersedes ON facts (supersedes_key);
"""

_lock = threading.RLock()        # one writer per process; SQLite locks across processes
_migrate_lock = threading.Lock()
_migrated: set[str] = set()


class StoreError(Exception):
    """The store file cannot be opened or written (bad FACTS_DB path, read-only volume)."""


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class Invalid(Exception):
    """A reference that the model alone cannot check (supersedes_key naming no fact)."""

    def __init__(self, loc: tuple, msg: str):
        super().__init__(msg)
        self.loc, self.msg = loc, msg


def db_path() -> str:
    return os.environ.get("FACTS_DB", "/data/facts.sqlite")


def exists() -> bool:
    return os.path.exists(db_path())


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def migrate(conn: sqlite3.Connection) -> None:
    path = db_path()
    if path in _migrated:
        return
    with _migrate_lock:
        if path in _migrated:
            return
        conn.execute("PRAGMA journal_mode=WAL")
        for table, cols in ADDED_COLUMNS.items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, sqltype in cols.items():
                if name not in have:
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}")
                    except sqlite3.OperationalError as exc:
                        if "duplicate column" not in str(exc):
                            raise
        conn.execute("UPDATE facts SET latest_version = current_version WHERE latest_version IS NULL")
        conn.executescript(INDEXES)
        conn.commit()
        _migrated.add(path)


@contextmanager
def connect():
    """A connection with the schema in place (creates the file). Commits on success."""
    path = db_path()
    try:
        conn = sqlite3.connect(path, timeout=10)
    except sqlite3.OperationalError as exc:
        raise StoreError(f"cannot open FACTS_DB {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        try:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.executescript(SCHEMA)
            migrate(conn)
        except sqlite3.OperationalError as exc:
            raise StoreError(f"cannot open FACTS_DB {path}: {exc}") from exc
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def reading():
    """A connection only when the store file exists (reads never create it), else None."""
    if not exists():
        yield None
        return
    with _lock, connect() as conn:
        note_expired(conn, today())
        note_superseded(conn, today())
        yield conn


def add_change(conn, key: str, version: int | None, kind: str, actor: str | None = None) -> int:
    cur = conn.execute("INSERT INTO changes (key, version, kind, at, actor) VALUES (?,?,?,?,?)",
                       (key, version, kind, now_iso(), actor))
    seq = cur.lastrowid
    conn.execute("INSERT INTO meta (name, value) VALUES ('fact_seq', ?) "
                 "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (str(seq),))
    return seq


def note_expired(conn, day: str) -> None:
    """Record a `changes` row (kind expired) the first time an active fact is seen past its
    valid_to. History is not touched: the fact stays `active` in storage and is reported
    `expired` by the date check."""
    rows = conn.execute(
        "SELECT f.key, f.current_version FROM facts f WHERE f.status = 'active' AND f.valid_to IS NOT NULL "
        "AND f.valid_to < ? AND NOT EXISTS (SELECT 1 FROM changes c WHERE c.key = f.key "
        "AND c.version = f.current_version AND c.kind = 'expired')", (day,)).fetchall()
    for r in rows:
        add_change(conn, r["key"], r["current_version"], "expired")


def successor_map(conn) -> dict[str, list[str | None]]:
    """superseded key -> valid_from of every confirmed fact that names it in supersedes_key.
    Superseding takes effect on the successor's start date, not when it was confirmed."""
    out: dict[str, list[str | None]] = {}
    if conn is None:
        return out
    for r in conn.execute("SELECT supersedes_key, valid_from FROM facts WHERE supersedes_key IS NOT NULL "
                          "AND status NOT IN ('draft', 'retired')"):
        out.setdefault(r["supersedes_key"], []).append(r["valid_from"])
    return out


def superseded_on(starts: list[str | None] | None, day: str) -> bool:
    """True when some successor has started on `day` (a successor with no valid_from starts at once)."""
    return any(v is None or v <= day for v in starts or [])


def note_superseded(conn, day: str) -> None:
    """A fact whose successor was confirmed with a future valid_from stays active until that day.
    The first time that day is reached, store it as superseded and tell change pollers."""
    succ = successor_map(conn)
    for key, starts in succ.items():
        row = _row(conn, key)
        if row is not None and row["status"] == "active" and superseded_on(starts, day):
            conn.execute("UPDATE facts SET status = 'superseded', updated_at = ? WHERE key = ?", (now_iso(), key))
            add_change(conn, key, row["current_version"], "superseded")


def status_on(row, day: str, succ: dict[str, list[str | None]] | None = None) -> str:
    """The stored status as it stands on `day`: a fact replaced by a successor that has not
    started yet is still active that day (and superseded from the successor's valid_from)."""
    status = row["status"]
    starts = (succ or {}).get(row["key"])
    if starts is None:
        return status
    if status == "superseded" and not superseded_on(starts, day):
        return "active"
    if status == "active" and superseded_on(starts, day):
        return "superseded"
    return status


def _version_data(conn, key: str, version: int) -> dict:
    row = conn.execute("SELECT data FROM fact_versions WHERE key = ? AND version = ?", (key, version)).fetchone()
    return json.loads(row["data"])


def _row(conn, key: str):
    return conn.execute("SELECT * FROM facts WHERE key = ?", (key,)).fetchone()


def effective_status(row, data: dict, day: str, succ: dict | None = None) -> str:
    status = status_on(row, day, succ)
    if status == "active" and data.get("valid_to") and data["valid_to"] < day:
        return "expired"
    return status


def render(row, data: dict, day: str, succ: dict | None = None, wordings: dict | None = None) -> dict:
    out = dict(data)
    out.update(status=effective_status(row, data, day, succ), version=row["current_version"],
               latest_version=row["latest_version"], derived=False, updated_at=row["updated_at"],
               disclosure_wordings=list((wordings or {}).get(row["key"], [])))
    return out


def stored_facts(conn) -> list[tuple[sqlite3.Row, dict]]:
    """(row, served version data) for every stored key, in creation order."""
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT f.*, v.data AS vdata FROM facts f JOIN fact_versions v "
        "ON v.key = f.key AND v.version = f.current_version ORDER BY f.rowid").fetchall()
    return [(r, json.loads(r["vdata"])) for r in rows]


def hidden_derived(conn, day: str | None = None) -> dict[str, str]:
    """derived key -> the key of the confirmed fact that replaced it (on `day`, default today:
    a successor that starts later hides nothing yet)."""
    if conn is None:
        return {}
    day = day or today()
    rows = conn.execute("SELECT key, supersedes_key, valid_from FROM facts WHERE status NOT IN ('draft', 'retired') "
                        "AND supersedes_key IS NOT NULL ORDER BY rowid").fetchall()
    return {r["supersedes_key"]: r["key"] for r in rows if superseded_on([r["valid_from"]], day)}


def fact_set_version(conn, derived_texts: list[str]) -> str:
    """fs-<seq>-<sha8 of the sorted active key:version pairs and the derived facts' texts>."""
    seq, active = 0, []
    if conn is not None:
        m = conn.execute("SELECT value FROM meta WHERE name = 'fact_seq'").fetchone()
        seq = int(m["value"]) if m else 0
        active = [f"{r['key']}:{r['current_version']}" for r in
                  conn.execute("SELECT key, current_version FROM facts WHERE status = 'active'")]
    blob = "\n".join(sorted(active)) + "\n--derived--\n" + "\n".join(derived_texts)
    return f"fs-{seq}-{sha8(blob)}"


def versions_of(conn, key: str) -> list[dict]:
    row = _row(conn, key)
    confirmed = {r["version"]: r["actor"] for r in conn.execute(
        "SELECT version, actor FROM changes WHERE key = ? AND kind = 'confirmed'", (key,))}
    out = []
    for v in conn.execute("SELECT * FROM fact_versions WHERE key = ? ORDER BY version", (key,)):
        n = v["version"]
        status = "superseded" if n < row["current_version"] else "draft" if n > row["current_version"] \
            else row["status"]
        out.append({"version": n, "status": status, "data": json.loads(v["data"]),
                    "created_by": v["created_by"], "created_at": v["created_at"],
                    "confirmed_by": v["confirmed_by"] or confirmed.get(n)})
    return out


def _sync_columns(conn, key: str, data: dict, **extra) -> None:
    cols = dict(subject_kind=data["subject"]["kind"], subject_ref=data["subject"]["ref"],
                fact_type=data["fact_type"], sensitivity=data["sensitivity"], valid_from=data["valid_from"],
                valid_to=data["valid_to"], review_by=data["review_by"], updated_at=now_iso(), **extra)
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"UPDATE facts SET {sets} WHERE key = ?", (*cols.values(), key))


def _check_refs(conn, fact: FactIn, derived_keys: set[str], extra_keys: set[str] = frozenset(),
                loc: tuple = ("body",)) -> None:
    if fact.key in derived_keys:
        raise Conflict(f"'{fact.key}' is a derived (brand profile) fact and read-only; "
                       "POST a new key with supersedes_key to replace it")
    target = fact.supersedes_key
    if target and target not in derived_keys and target not in extra_keys and _row(conn, target) is None:
        raise Invalid((*loc, "supersedes_key"), f"no fact with key '{target}'")


def _append_version(conn, key: str, data: dict, actor: str, confirmed_by: str | None = None) -> int:
    row = _row(conn, key)
    n = (row["latest_version"] or row["current_version"]) + 1 if row else 1
    conn.execute("INSERT INTO fact_versions (key, version, data, created_by, confirmed_by, created_at) "
                 "VALUES (?,?,?,?,?,?)", (key, n, canonical(data), actor, confirmed_by, now_iso()))
    if row is None:
        conn.execute("INSERT INTO facts (key, current_version, latest_version, status, updated_at) "
                     "VALUES (?, 1, 1, 'draft', ?)", (key, now_iso()))
        _sync_columns(conn, key, data)
        add_change(conn, key, 1, "created", actor)
    elif row["status"] == "draft":  # never confirmed: the new draft is what the key shows
        conn.execute("UPDATE facts SET current_version = ?, latest_version = ? WHERE key = ?", (n, n, key))
        _sync_columns(conn, key, data)
        add_change(conn, key, n, "updated", actor)
    else:  # served version stays until the owner confirms the new one
        conn.execute("UPDATE facts SET latest_version = ?, updated_at = ? WHERE key = ?", (n, now_iso(), key))
        add_change(conn, key, n, "updated", actor)
    return n


def create(fact: FactIn, derived_keys: set[str], actor: str) -> dict:
    with _lock, connect() as conn:
        _check_refs(conn, fact, derived_keys)
        row = _row(conn, fact.key)
        if row is not None and row["status"] != "draft":
            raise Conflict(f"fact '{fact.key}' exists and is {row['status']}; use PUT /facts/v2/{fact.key}")
        _append_version(conn, fact.key, fact.stored(), actor)
        return get(conn, fact.key)


def update(key: str, fact: FactIn, derived_keys: set[str], actor: str) -> dict:
    with _lock, connect() as conn:
        if key in derived_keys:
            raise Conflict(f"'{key}' is a derived (brand profile) fact and read-only")
        if _row(conn, key) is None:
            raise NotFound(key)
        _check_refs(conn, fact, derived_keys)
        _append_version(conn, key, fact.stored(), actor)
        return get(conn, key)


def _set_status(conn, key: str, status: str, actor: str) -> bool:
    """Serve the latest version with `status`. False when nothing changed."""
    row = _row(conn, key)
    latest = row["latest_version"] or row["current_version"]
    if row["status"] == status and row["current_version"] == latest:
        return False
    data = _version_data(conn, key, latest)
    conn.execute("UPDATE facts SET current_version = ?, status = ? WHERE key = ?", (latest, status, key))
    _sync_columns(conn, key, data, supersedes_key=data.get("supersedes_key"))
    add_change(conn, key, latest, "confirmed" if status == "active" else status, actor)
    target = data.get("supersedes_key") if status == "active" else None
    if target and not superseded_on([data.get("valid_from")], today()):
        target = None  # takes effect on data["valid_from"] (note_superseded / status_on)
    if target:
        trow = _row(conn, target)
        if trow is None:  # a derived fact: hidden from now on (computed); tell change pollers
            add_change(conn, target, 1, "superseded", actor)
        elif trow["status"] != "superseded":
            conn.execute("UPDATE facts SET status = 'superseded', updated_at = ? WHERE key = ?",
                         (now_iso(), target))
            add_change(conn, target, trow["current_version"], "superseded", actor)
    return True


def confirm(key: str, derived_keys: set[str], actor: str) -> dict:
    with _lock, connect() as conn:
        if key in derived_keys:
            raise Conflict(f"'{key}' is a derived (brand profile) fact; it is already in use")
        if _row(conn, key) is None:
            raise NotFound(key)
        _set_status(conn, key, "active", actor)
        return get(conn, key)


def retire(key: str, derived_keys: set[str], actor: str) -> dict:
    with _lock, connect() as conn:
        if key in derived_keys:
            raise Conflict(f"'{key}' is a derived (brand profile) fact; edit the brand profile to remove it")
        row = _row(conn, key)
        if row is None:
            raise NotFound(key)
        if row["status"] != "retired":
            conn.execute("UPDATE facts SET status = 'retired', updated_at = ? WHERE key = ?", (now_iso(), key))
            add_change(conn, key, row["current_version"], "retired", actor)
        return get(conn, key)


def import_facts(facts: list[FactIn], confirm_all: bool, derived_keys: set[str], actor: str) -> dict:
    """All or nothing: one transaction; any bad reference rolls the whole batch back."""
    counts = {"created": 0, "updated": 0, "unchanged": 0, "confirmed": 0}
    batch = {f.key for f in facts}
    with _lock, connect() as conn:
        try:
            for i, fact in enumerate(facts):
                _check_refs(conn, fact, derived_keys, batch, ("body", "facts", i))
                data = fact.stored()
                row = _row(conn, fact.key)
                changed = row is None or _version_data(
                    conn, fact.key, row["latest_version"] or row["current_version"]) != json.loads(canonical(data))
                if changed:
                    _append_version(conn, fact.key, data, actor)
                status_changed = False
                if confirm_all and fact.status != "draft":
                    target = fact.status if fact.status in ("expired", "retired", "superseded") else "active"
                    status_changed = _set_status(conn, fact.key, target, actor)
                    counts["confirmed"] += status_changed and target == "active"
                kind = "created" if row is None else "updated" if changed or status_changed else "unchanged"
                counts[kind] += 1
        except Exception:
            conn.rollback()
            raise
    return counts | {"keys": [f.key for f in facts]}


def get(conn, key: str) -> dict:
    row = _row(conn, key)
    if row is None:
        raise NotFound(key)
    data = _version_data(conn, key, row["current_version"])
    return render(row, data, today(), successor_map(conn), active_wordings(conn)) | {"superseded_by": _superseded_by(conn, key),
                                          "versions": versions_of(conn, key)}


def _superseded_by(conn, key: str) -> str | None:
    r = conn.execute("SELECT key FROM facts WHERE supersedes_key = ? AND status != 'draft' ORDER BY rowid DESC",
                     (key,)).fetchone()
    return r["key"] if r else None


def changes_since(conn, since: int, limit: int = 1000) -> dict:
    if conn is None:
        return {"seq": 0, "changes": []}
    m = conn.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM changes").fetchone()["s"]
    rows = conn.execute("SELECT seq, key, version, kind, at FROM changes WHERE seq > ? ORDER BY seq LIMIT ?",
                        (since, limit)).fetchall()
    return {"seq": m, "changes": [dict(r) for r in rows]}


# --- disclosure wordings -----------------------------------------------------------------
# A person accepted a missing_disclosure finding because the text says the disclosure in other
# words. Those exact words are proposed here (draft) and count as the disclosure only after the
# owner confirms them. They are kept apart from allowed_phrasing and from the fact's versions:
# confirming or dismissing one writes no `changes` row, so approved posts never go back to draft.

WORDING_STATUSES = ("draft", "active", "dismissed")
WORDING_COLS = ("id", "fact_key", "disclosure", "wording", "status", "proposed_by", "task_id",
                "created_at", "decided_by", "decided_at")


class WordingIn(_Strict):
    fact_key: _s(60, 1)
    disclosure: _s(200, 1)
    wording: _s(200, 1)
    task_id: _s(80) | None = None
    proposed_by: _s(80) | None = None


def norm_text(s: str) -> str:
    return " ".join(str(s).split()).casefold()


def _wording(r) -> dict:
    return {k: r[k] for k in WORDING_COLS}


def active_wordings(conn) -> dict[str, list[str]]:
    """fact key -> the wordings the owner confirmed, oldest first."""
    out: dict[str, list[str]] = {}
    if conn is None:
        return out
    for r in conn.execute("SELECT fact_key, wording FROM disclosure_wordings WHERE status = 'active' ORDER BY id"):
        out.setdefault(r["fact_key"], []).append(r["wording"])
    return out


def propose_wording(w: WordingIn, disclosure: str) -> tuple[dict, bool]:
    """(row, created). `disclosure` is the fact's own text of the disclosure. The same wording for
    the same disclosure is never stored twice (a dismissed one stays dismissed)."""
    wording = " ".join(w.wording.split())
    with _lock, connect() as conn:
        for r in conn.execute("SELECT * FROM disclosure_wordings WHERE fact_key = ? ORDER BY id", (w.fact_key,)):
            if norm_text(r["disclosure"]) == norm_text(disclosure) and norm_text(r["wording"]) == norm_text(wording):
                return _wording(r), False
        cur = conn.execute(
            "INSERT INTO disclosure_wordings (fact_key, disclosure, wording, status, proposed_by, task_id, created_at) "
            "VALUES (?,?,?,'draft',?,?,?)", (w.fact_key, disclosure, wording, w.proposed_by, w.task_id, now_iso()))
        r = conn.execute("SELECT * FROM disclosure_wordings WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _wording(r), True


def list_wordings(conn, status: str | None, fact_key: str | None, limit: int = 500) -> list[dict]:
    if conn is None:
        return []
    where, args = [], []
    if status:
        where.append("status = ?"), args.append(status)
    if fact_key:
        where.append("fact_key = ?"), args.append(fact_key)
    sql = "SELECT * FROM disclosure_wordings" + (" WHERE " + " AND ".join(where) if where else "")
    return [_wording(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]


def set_wording_status(wid: int, status: str, actor: str | None) -> dict:
    with _lock, connect() as conn:
        r = conn.execute("SELECT * FROM disclosure_wordings WHERE id = ?", (wid,)).fetchone()
        if r is None:
            raise NotFound(f"disclosure wording {wid}")
        conn.execute("UPDATE disclosure_wordings SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                     (status, actor, now_iso(), wid))
        return _wording(conn.execute("SELECT * FROM disclosure_wordings WHERE id = ?", (wid,)).fetchone())


# --- open questions -----------------------------------------------------------------------

class QuestionIn(_Strict):
    kind: Literal["missing_fact", "confirm", "scope"]
    fact_key: Key | None = None
    task_id: _s(40) | None = None
    text: _s(1000, 1)


class AnswerIn(_Strict):
    answer: _s(2000) | None = None


def _question(r) -> dict:
    return {k: r[k] for k in ("id", "kind", "fact_key", "task_id", "text", "status", "answer",
                              "created_at", "closed_at")}


def list_questions(conn, status: str | None) -> list[dict]:
    if conn is None:
        return []
    sql, args = "SELECT * FROM open_questions", ()
    if status:
        sql, args = sql + " WHERE status = ?", (status,)
    return [_question(r) for r in conn.execute(sql + " ORDER BY id", args)]


def add_question(q: QuestionIn) -> dict:
    with _lock, connect() as conn:
        cur = conn.execute("INSERT INTO open_questions (kind, fact_key, task_id, text, created_at) "
                           "VALUES (?,?,?,?,?)", (q.kind, q.fact_key, q.task_id, q.text, now_iso()))
        return _question(conn.execute("SELECT * FROM open_questions WHERE id = ?", (cur.lastrowid,)).fetchone())


def close_question(qid: int, status: str, answer: str | None = None) -> dict:
    with _lock, connect() as conn:
        r = conn.execute("SELECT * FROM open_questions WHERE id = ?", (qid,)).fetchone()
        if r is None:
            raise NotFound(str(qid))
        if r["status"] != "open":
            raise Conflict(f"question {qid} is already {r['status']}")
        conn.execute("UPDATE open_questions SET status = ?, answer = ?, closed_at = ? WHERE id = ?",
                     (status, answer, now_iso(), qid))
        return _question(conn.execute("SELECT * FROM open_questions WHERE id = ?", (qid,)).fetchone())


# --- starter kits and rules ---------------------------------------------------------------

class KitFactType(_Strict):
    fact_type: FactType
    subject_kind: SubjectKind
    attributes: list[_s(60, 1)] = Field(min_length=1, max_length=20)
    example: _s(300, 1)


class KitPhrase(_Strict):
    phrase: _s(80, 1)
    why: _s(240, 1)
    claim_class: ClaimClass = "none"
    needs: _s(200) | None = None   # the fact that would make the wording acceptable


class KitDisclosure(_Strict):
    when: _s(200, 1)
    text: _s(200, 1)
    claim_class: ClaimClass = "none"


class StarterKit(_Strict):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{2,40}$")]
    label: _s(80, 1)
    description: _s(400) = ""
    fact_types: list[KitFactType] = Field(min_length=1, max_length=30)
    claim_classes: list[ClaimClass] = Field(min_length=1)
    forbidden_phrasing: list[KitPhrase] = Field(min_length=1, max_length=60)
    required_disclosures: list[KitDisclosure] = Field(min_length=1, max_length=30)


def kits_dir() -> Path:
    default = Path(__file__).resolve().parent.parent / "config" / "starter-kits"
    return Path(os.environ.get("STARTER_KITS_DIR") or default)


def load_starter_kits() -> dict[str, dict]:
    """Every kit, validated. A broken kit file is a packaging bug: it raises (500), never
    silently shows a partial rule set."""
    out = {}
    folder = kits_dir()
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"starter kit {path.name}: must be a yaml mapping")
        raw.setdefault("id", path.stem)
        if raw["id"] != path.stem:
            raise ValueError(f"starter kit {path.name}: id '{raw['id']}' must match the file name")
        try:
            out[path.stem] = StarterKit(**raw).model_dump()
        except ValidationError as exc:
            raise ValueError(f"starter kit {path.name}: {exc}") from exc
    return out


def _rule(r) -> dict:
    return {k: r[k] for k in ("id", "kit", "kind", "phrase", "why", "claim_class", "needs", "status", "created_at")}


def apply_kit(kit: dict) -> dict:
    """Store the kit's forbidden phrasings and disclosures as DRAFT rules (idempotent)."""
    rows = [("forbidden_phrase", p["phrase"], p["why"], p["claim_class"], p.get("needs"))
            for p in kit["forbidden_phrasing"]]
    rows += [("required_disclosure", d["text"], d["when"], d["claim_class"], None)
             for d in kit["required_disclosures"]]
    created = 0
    with _lock, connect() as conn:
        for kind, phrase, why, cc, needs in rows:
            cur = conn.execute("INSERT OR IGNORE INTO rules (kit, kind, phrase, why, claim_class, needs, created_at) "
                               "VALUES (?,?,?,?,?,?,?)", (kit["id"], kind, phrase, why, cc, needs, now_iso()))
            created += cur.rowcount
        rules = [_rule(r) for r in conn.execute("SELECT * FROM rules WHERE kit = ? ORDER BY id", (kit["id"],))]
    return {"kit": kit["id"], "created": created, "existing": len(rows) - created, "rules": rules,
            "suggested_fact_types": kit["fact_types"]}


def list_rules(conn, status: str | None, kit: str | None) -> list[dict]:
    if conn is None:
        return []
    where, args = [], []
    if status:
        where.append("status = ?"), args.append(status)
    if kit:
        where.append("kit = ?"), args.append(kit)
    sql = "SELECT * FROM rules" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    return [_rule(r) for r in conn.execute(sql, args)]


def set_rule_status(rid: int, status: str) -> dict:
    with _lock, connect() as conn:
        r = conn.execute("SELECT * FROM rules WHERE id = ?", (rid,)).fetchone()
        if r is None:
            raise NotFound(str(rid))
        conn.execute("UPDATE rules SET status = ? WHERE id = ?", (status, rid))
        return _rule(conn.execute("SELECT * FROM rules WHERE id = ?", (rid,)).fetchone())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:54].strip("-")


def exclusion_reason(status: str, data: dict, at: str, max_sensitivity: str, task_scope: dict) -> str | None:
    """Why a stored fact is not usable for this task on day `at` (None = usable).
    Order: status, sensitivity, validity window, scope."""
    if status != "active":
        return status
    return (sensitivity_reason(data["sensitivity"], max_sensitivity)
            or validity_reason(data, status, at)
            or scope_reason(data.get("scope") or {}, task_scope))
