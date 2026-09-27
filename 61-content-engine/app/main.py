"""Content engine: one pillar -> a month of planned, varied, non-duplicate slots.

It stores a pillar (the one substantial source), the atoms a model extracted from it
(claims, stories, FAQs, ...), and plans the month IN CODE: atoms x hook styles x formats x
channels, with caps per channel and per atom. It also answers "is this draft too close to
something we already published on this channel?" (word 5-grams, same opening, embeddings)
and applies the batch stop rule from the design (_dev/research/content-volume.md).

It never writes a post, never calls the LLM gateway and never publishes. n8n does the
drafting against this API; a person approves every piece.
"""
import hashlib
import hmac
import math
import os
import random
import re
import sqlite3
import threading
from array import array
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator

app = FastAPI(title="content-engine")

# ---------- planning constants

CHANNELS = ("linkedin", "x", "instagram", "facebook", "threads", "mastodon", "blog", "email", "video")
Channel = Literal["linkedin", "x", "instagram", "facebook", "threads", "mastodon", "blog", "email", "video"]

# Posts per week per channel when the plan request gives no cadence. Each value sits inside
# the platform guidance collected in _dev/research/content-volume.md section 2: LinkedIn 2-5 a
# week (Buffer, 2M posts), Instagram 3-5 a week, X "start at 1 to 2 a day" (the 3-4 a day
# figure rests on 30 accounts), TikTok/Reels 2-5 a week, newsletter weekly, one blog post a
# week (Google: value per page, not volume). Facebook/Threads/Mastodon have no cadence study in
# the research; 3 a week mirrors Instagram. At most 7: never two posts on one channel a day.
DEFAULT_CADENCE = {
    "linkedin": 3, "x": 7, "instagram": 3, "facebook": 3, "threads": 3, "mastodon": 3,
    "blog": 1, "email": 1, "video": 2,
}
# Format of the n-th slot on a channel (rotates). X gets one thread a week at 7 posts a week,
# LinkedIn one carousel in three, Instagram alternates carousel copy and a single post.
FORMAT_ROTATION = {
    "linkedin": ("post", "post", "carousel_text"),
    "x": ("post", "post", "post", "post", "post", "post", "thread"),
    "instagram": ("carousel_text", "post"),
    "facebook": ("post",),
    "threads": ("post",),
    "mastodon": ("post",),
    "blog": ("blog",),
    "email": ("email",),
    "video": ("video_script",),
}
# Candidate posting hours (UTC) per channel. Starting points only: tune them from 45 /insights.
POST_HOURS_UTC = {
    "linkedin": (7, 8, 12), "x": (9, 12, 15, 17), "instagram": (11, 15, 18),
    "facebook": (9, 13, 16), "threads": (10, 14, 18), "mastodon": (9, 14, 17),
    "blog": (7, 8), "email": (7, 8, 9), "video": (15, 17, 19),
}
POST_MINUTES = (0, 15, 30, 45)
# The enum of 04 prompts/social_posts.yaml and 45 /insights/hooks.
HOOK_STYLES = ("question", "fact_led", "story", "how_to", "benefit", "contrarian")
ATOM_KINDS = ("claim", "story", "faq", "tip", "stat", "objection", "quote")
MAX_CHANNELS_PER_ATOM = 3
# An atom may come back on the SAME channel with a different hook after REUSE_GAP_DAYS, up to
# REUSE_PER_CHANNEL uses in total. Without it a channel could never have more slots than
# verified atoms (x at 7/week needs 28 atoms; one pillar gave 20-23 in the smoke test).
# Fresh atoms are always preferred; the novelty check (/novelty/check) still screens the text.
REUSE_PER_CHANNEL = int(os.getenv("REUSE_PER_CHANNEL", "2"))
REUSE_GAP_DAYS = int(os.getenv("REUSE_GAP_DAYS", "14"))
# Weekdays (Mon=0) used for k posts a week: weekdays first, spread out.
SPREAD = {
    1: (1,), 2: (1, 3), 3: (0, 2, 4), 4: (0, 1, 3, 4), 5: (0, 1, 2, 3, 4),
    6: (0, 1, 2, 3, 4, 5), 7: (0, 1, 2, 3, 4, 5, 6),
}
MAX_ATOMS_PER_CALL = 200

PILLAR_COLUMNS = (
    "id", "title", "brief", "audience", "source_text", "source_url", "channels", "month",
    "promo_max", "status", "paused_reason", "window_start", "created_at", "updated_at",
)
ATOM_COLUMNS = ("id", "pillar_id", "kind", "text", "verified", "evidence", "promo", "created_at")
SLOT_COLUMNS = (
    "id", "pillar_id", "date", "time_utc", "channel", "format", "atom_id", "hook_style",
    "status", "calendar_item_id", "reason", "created_at", "updated_at", "experiment_id", "arm",
)
# Experiments (45): a slot of a running experiment carries its arm. hook_style, format and
# time are forced on the slot; cta and length are instructions the drafter (65) follows.
EXP_VARIABLES = ("hook_style", "format", "cta", "length", "time")
MAX_EXPERIMENTS_PER_CHANNEL = 2
# Columns added after the first release; old databases get them in migrate().
SLOT_EXTRA_COLUMNS = {"experiment_id": "INTEGER", "arm": "TEXT", "exp_variable": "TEXT",
                      "arm_value": "TEXT", "arm_brief": "TEXT"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS pillars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    brief TEXT NOT NULL,
    audience TEXT NOT NULL,
    source_text TEXT,
    source_url TEXT,
    channels TEXT NOT NULL,
    month TEXT NOT NULL,
    promo_max REAL NOT NULL,
    status TEXT NOT NULL,
    paused_reason TEXT,
    window_start TEXT NOT NULL,
    window_seq INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS atoms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pillar_id INTEGER NOT NULL REFERENCES pillars (id),
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    norm TEXT NOT NULL,
    verified INTEGER NOT NULL,
    evidence TEXT,
    promo INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (pillar_id, norm)
);
CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pillar_id INTEGER NOT NULL REFERENCES pillars (id),
    date TEXT NOT NULL,
    time_utc TEXT NOT NULL,
    channel TEXT NOT NULL,
    format TEXT NOT NULL,
    atom_id INTEGER NOT NULL REFERENCES atoms (id),
    hook_style TEXT NOT NULL,
    status TEXT NOT NULL,
    calendar_item_id INTEGER,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS slots_pillar_status ON slots (pillar_id, status);
CREATE TABLE IF NOT EXISTS novelty_items (
    item_id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS novelty_channel_created ON novelty_items (channel, created_at);
CREATE TABLE IF NOT EXISTS embeddings (
    key TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    vec BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
    pillar_id INTEGER NOT NULL REFERENCES pillars (id),
    item_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (pillar_id, item_id)
);
"""
_migrate_lock = threading.Lock()
_migrated: set[str] = set()


def env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def env_num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


# ---------- time: stored as "YYYY-MM-DDTHH:MM:SSZ" so strings sort correctly


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    return fmt(datetime.now(timezone.utc))


def to_utc_iso(value: str) -> str:
    """ISO 8601 with or without offset (naive means UTC; a bare date is midnight UTC)."""
    value = value.strip()
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"not an ISO 8601 date/time: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return fmt(dt)


# ---------- storage


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/engine.sqlite")


def migrate(conn: sqlite3.Connection) -> None:
    """Create the schema once per DB path per process.

    WAL lets readers work while a write is in progress. The lock covers threads, and
    BEGIN IMMEDIATE (one writer at a time) covers other processes.
    """
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
            have = {r[1] for r in conn.execute("PRAGMA table_info(slots)")}
            for col, decl in SLOT_EXTRA_COLUMNS.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE slots ADD COLUMN {col} {decl}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        _migrated.add(path)


@contextmanager
def db():
    """A connection in autocommit mode; use `write(conn)` around statements that change data."""
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
    """One write transaction, taken before the first read so check-then-write can't race."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def row_to_pillar(row: sqlite3.Row) -> dict:
    p = {k: row[k] for k in PILLAR_COLUMNS}
    p["channels"] = p["channels"].split(",")
    return p


def row_to_atom(row: sqlite3.Row) -> dict:
    a = {k: row[k] for k in ATOM_COLUMNS}
    a["verified"], a["promo"] = bool(a["verified"]), bool(a["promo"])
    return a


def row_to_slot(row: sqlite3.Row) -> dict:
    s = {k: row[k] for k in SLOT_COLUMNS}
    s["experiment"] = ({"id": row["experiment_id"], "variable": row["exp_variable"], "arm": row["arm"],
                        "value": row["arm_value"], "brief": row["arm_brief"]}
                       if row["experiment_id"] is not None else None)
    return s


def valid_id(value: int) -> bool:
    return 0 < value < 2**63  # SQLite integers are 64-bit


def pillar_or_404(conn: sqlite3.Connection, pillar_id: int) -> dict:
    row = conn.execute("SELECT * FROM pillars WHERE id = ?", (pillar_id,)).fetchone() if valid_id(pillar_id) else None
    if row is None:
        raise HTTPException(404, f"pillar {pillar_id} not found")
    return row_to_pillar(row)


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- text


def words(text: str) -> list[str]:
    """Lowercased words, punctuation stripped ("Don't!" -> ["dont"])."""
    text = text.lower().replace("’", "").replace("'", "")
    return re.findall(r"\w+", text)


def ngrams(ws: list[str], n: int = 5) -> set[tuple[str, ...]]:
    if len(ws) < n:  # a short text is one gram of itself, so identical short texts still match
        return {tuple(ws)} if ws else set()
    return {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def opening(ws: list[str], n: int = 8) -> tuple[str, ...]:
    return tuple(ws[:n])


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------- embeddings (Ollama /api/embed), cached in SQLite


def embed_model() -> str:
    return env("EMBED_MODEL", "qwen3-embedding:0.6b")


def embed_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\0{text}".encode()).hexdigest()


async def ollama_embed(texts: list[str]) -> list[list[float]] | None:
    """Vectors for `texts`, or None when Ollama is unreachable or answers nonsense."""
    url = env("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/") + "/api/embed"
    try:
        async with httpx.AsyncClient(timeout=env_num("EMBED_TIMEOUT", 60)) as client:
            r = await client.post(url, json={"model": embed_model(), "input": texts})
            r.raise_for_status()
            got = r.json()["embeddings"]
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return None
    if not isinstance(got, list) or len(got) != len(texts):
        return None
    return got


async def embeddings_for(texts: list[str]) -> dict[str, array] | None:
    """{text: vector} for every text, from the cache or from one Ollama call for the rest."""
    model = embed_model()
    out: dict[str, array] = {}
    with db() as conn:
        for t in set(texts):
            row = conn.execute("SELECT vec FROM embeddings WHERE key = ?", (embed_key(model, t),)).fetchone()
            if row is not None:
                v = array("f")
                v.frombytes(row["vec"])
                out[t] = v
    missing = [t for t in dict.fromkeys(texts) if t not in out]
    if missing:
        got = await ollama_embed(missing)
        if got is None:
            return None
        with db() as conn, write(conn):
            for t, vec in zip(missing, got):
                v = array("f", vec)
                out[t] = v
                conn.execute("INSERT OR REPLACE INTO embeddings (key, model, vec) VALUES (?, ?, ?)",
                             (embed_key(model, t), model, v.tobytes()))
    return out


# ---------- models


def _not_blank(v: str) -> str:
    if not v.strip():
        raise ValueError("must not be empty")
    return v.strip()


class NewPillar(BaseModel):
    title: str = Field(max_length=300)
    brief: str = Field(max_length=5000)
    audience: str = Field(max_length=1000)
    source_text: str | None = Field(default=None, max_length=200000)
    source_url: str | None = Field(default=None, max_length=2000)
    channels: list[Channel] = Field(min_length=1)
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    promo_max: float = Field(default=0.2, ge=0, le=1)

    @field_validator("title", "brief", "audience")
    @classmethod
    def not_blank(cls, v: str) -> str:
        return _not_blank(v)

    @field_validator("source_url")
    @classmethod
    def http_url(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^https?://\S+$", v.strip()):
            raise ValueError("must be an http(s) URL")
        return v.strip() if v else v

    @field_validator("channels")
    @classmethod
    def unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("channels must not repeat")
        return v


class NewAtom(BaseModel):
    kind: Literal["claim", "story", "faq", "tip", "stat", "objection", "quote"]
    text: str = Field(max_length=1000)
    verified: StrictBool
    evidence: str | None = Field(default=None, max_length=2000)
    promo: StrictBool = False

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        return _not_blank(v)


class AtomBatch(BaseModel):
    atoms: list[NewAtom] = Field(min_length=1, max_length=MAX_ATOMS_PER_CALL)


class PlanRequest(BaseModel):
    start_date: date | None = None
    weeks: StrictInt = Field(default=4, ge=1, le=8)
    cadence: dict[Channel, StrictInt] | None = None
    seed: StrictInt | None = None

    @field_validator("cadence")
    @classmethod
    def per_week(cls, v: dict | None) -> dict | None:
        for ch, n in (v or {}).items():
            if not 0 <= n <= 7:
                raise ValueError(f"cadence for {ch} must be 0-7 posts a week (at most one a day)")
        return v


class SlotStatus(BaseModel):
    # "planned" is only accepted on a planned slot (the same-status update below): it records
    # the calendar idea item the plan tool (64) created, before anything is drafted.
    status: Literal["planned", "drafted", "dropped"]
    calendar_item_id: StrictInt | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=2000)


class NoveltyItem(BaseModel):
    item_id: StrictInt = Field(ge=1)
    channel: str = Field(min_length=1, max_length=50)
    text: str = Field(max_length=100000)
    created_at: str | None = None

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        return _not_blank(v)

    @field_validator("channel")
    @classmethod
    def lower(cls, v: str) -> str:
        return _not_blank(v).lower()

    @field_validator("created_at")
    @classmethod
    def when(cls, v: str | None) -> str | None:
        return to_utc_iso(v) if v is not None else None


class NoveltyCheck(BaseModel):
    text: str = Field(max_length=100000)
    channel: str = Field(min_length=1, max_length=50)
    exclude_item_id: StrictInt | None = None
    any_channel: StrictBool = False

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        return _not_blank(v)

    @field_validator("channel")
    @classmethod
    def lower(cls, v: str) -> str:
        return _not_blank(v).lower()


class Outcome(BaseModel):
    item_id: StrictInt = Field(ge=1)
    decision: Literal["approved", "edited", "rejected"]


# ---------- endpoints: pillars and atoms


@app.get("/health")
def health():
    return {"status": "ok"}


def pillar_counts(conn: sqlite3.Connection, p: dict) -> dict:
    a = conn.execute("SELECT COUNT(*), COALESCE(SUM(verified), 0) FROM atoms WHERE pillar_id = ?",
                     (p["id"],)).fetchone()
    s = conn.execute("SELECT status, COUNT(*) FROM slots WHERE pillar_id = ? GROUP BY status",
                     (p["id"],)).fetchall()
    p["atoms"], p["atoms_verified"] = a[0], a[1]
    p["slots"] = {st: 0 for st in ("planned", "drafted", "dropped")} | {r[0]: r[1] for r in s}
    return p


@app.post("/pillars", status_code=201, dependencies=[Depends(require_key)])
def create_pillar(req: NewPillar):
    ts = now_utc()
    with db() as conn, write(conn):
        cur = conn.execute(
            "INSERT INTO pillars (title, brief, audience, source_text, source_url, channels, month,"
            " promo_max, status, paused_reason, window_start, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?, ?)",
            (req.title, req.brief, req.audience, req.source_text, req.source_url,
             ",".join(req.channels), req.month, req.promo_max, ts, ts, ts),
        )
        return pillar_counts(conn, pillar_or_404(conn, cur.lastrowid))


@app.get("/pillars")
def list_pillars(month: str | None = None, status: Literal["active", "paused"] | None = None):
    sql, args = "SELECT * FROM pillars WHERE 1=1", []
    if month:
        sql, args = sql + " AND month = ?", args + [month]
    if status:
        sql, args = sql + " AND status = ?", args + [status]
    with db() as conn:
        rows = conn.execute(sql + " ORDER BY id DESC", args).fetchall()
        out = []
        for r in rows:
            p = row_to_pillar(r)
            p.pop("source_text")  # can be long; GET /pillars/{id} has it
            out.append(pillar_counts(conn, p))
        return out


@app.get("/pillars/{pillar_id}")
def get_pillar(pillar_id: int):
    with db() as conn:
        return pillar_counts(conn, pillar_or_404(conn, pillar_id))


@app.post("/pillars/{pillar_id}/atoms", status_code=201, dependencies=[Depends(require_key)])
def add_atoms(pillar_id: int, req: AtomBatch):
    """Append atoms. An atom whose text (case and whitespace aside) is already stored is skipped."""
    ts = now_utc()
    ids, skipped = [], []
    with db() as conn, write(conn):
        pillar_or_404(conn, pillar_id)
        for a in req.atoms:
            norm = " ".join(a.text.lower().split())
            cur = conn.execute(
                "INSERT OR IGNORE INTO atoms (pillar_id, kind, text, norm, verified, evidence, promo, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pillar_id, a.kind, a.text, norm, int(a.verified), a.evidence, int(a.promo), ts),
            )
            (ids if cur.rowcount else skipped).append(cur.lastrowid if cur.rowcount else a.text)
        verified = conn.execute("SELECT COUNT(*) FROM atoms WHERE pillar_id = ? AND verified = 1",
                                (pillar_id,)).fetchone()[0]
    return {"added": len(ids), "ids": ids, "skipped": skipped, "verified_total": verified,
            "min_atoms": int(env_num("MIN_ATOMS", 12))}


@app.get("/pillars/{pillar_id}/atoms")
def list_atoms(pillar_id: int, verified: bool | None = None):
    sql, args = "SELECT * FROM atoms WHERE pillar_id = ?", [pillar_id]
    if verified is not None:
        sql, args = sql + " AND verified = ?", args + [int(verified)]
    with db() as conn:
        pillar_or_404(conn, pillar_id)
        return [row_to_atom(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]


# ---------- the matrix planner


def week_days(week_start: date, k: int, rotation: int) -> list[date]:
    """Preferred dates for k posts in the 7 days from week_start, best first.

    Weekdays first, spread out (SPREAD), rotated per channel so channels don't all post on
    the same weekday; then the other weekdays, then the weekend.
    """
    base = SPREAD.get(k, SPREAD[7])
    if k <= 5:
        base = tuple(sorted((d + rotation) % 5 for d in base))
    order = list(base) + [(d + rotation) % 5 for d in range(5)] + [5, 6]
    order = list(dict.fromkeys(order))
    by_weekday = {(week_start + timedelta(i)).weekday(): week_start + timedelta(i) for i in range(7)}
    return [by_weekday[wd] for wd in order]


def hour_bucket(time_utc: str) -> str:
    h = int(time_utc[:2])
    return "am" if h < 12 else "pm" if h < 17 else "eve"


def assign_arms(slots: list[dict], experiments: list[dict], kept: list[dict], seed: int) -> None:
    """Give each slot on a channel with a running experiment one of its two arms, in place.

    Slots of a channel are shared round-robin by its experiments (at most 2, oldest first).
    Inside one experiment and channel the arms are balanced by weekday first, then by hour
    bucket (morning / afternoon / evening) and overall: each slot, in date order, gets the
    arm with fewer slots on that weekday, then in that bucket, then in total; a tie is broken
    at random (seeded), so the order alternates at random. Drafted slots (`kept`) that already
    carry an arm count too. For a `time` experiment the arm sets the time, so only weekday
    and total are balanced. The arm's value is forced on the slot: `format` and `time_utc`
    here, `forced_hook` for the atom/hook step; cta and length go to the drafter.
    """
    by_channel: dict[str, list[dict]] = {}
    for e in sorted(experiments, key=lambda e: e["id"]):
        for ch in e["channels"]:
            if len(by_channel.setdefault(ch, [])) < MAX_EXPERIMENTS_PER_CHANNEL:
                by_channel[ch].append(e)
    for ch, exps in by_channel.items():
        mine = [s for s in slots if s["channel"] == ch]
        for i, s in enumerate(mine):
            s["_exp"] = exps[i % len(exps)]
        for e in exps:
            rng = random.Random(f"{seed}/{e['id']}/{ch}")
            arms = {a["label"]: a for a in e["arms"]}
            wd, bk, total = Counter(), Counter(), Counter()
            for k in kept:
                if k.get("experiment_id") == e["id"] and k["channel"] == ch and k.get("arm") in arms:
                    d = date.fromisoformat(k["date"]).weekday()
                    wd[(d, k["arm"])] += 1
                    bk[(d, hour_bucket(k["time_utc"]), k["arm"])] += 1
                    total[k["arm"]] += 1
            for s in (x for x in mine if x["_exp"] is e):
                d, b = date.fromisoformat(s["date"]).weekday(), hour_bucket(s["time_utc"])
                timed = e["variable"] == "time"
                label = min(sorted(arms), key=lambda lab: (wd[(d, lab)], 0 if timed else bk[(d, b, lab)],
                                                         total[lab], rng.random()))
                wd[(d, label)] += 1
                bk[(d, b, label)] += 1
                total[label] += 1
                arm = arms[label]
                s["experiment"] = {"id": e["id"], "variable": e["variable"], "arm": label,
                                   "value": arm["value"], "brief": arm.get("brief")}
                if e["variable"] == "format":
                    s["format"] = arm["value"]
                elif e["variable"] == "time":
                    s["time_utc"] = arm["value"]
                elif e["variable"] == "hook_style" and arm["value"] in HOOK_STYLES:
                    s["forced_hook"] = arm["value"]
        for s in mine:
            s.pop("_exp", None)


def plan_slots(*, channels: list[str], cadence: dict[str, int], start: date, weeks: int,
               atoms: list[dict], kept: list[dict], promo_max: float, seed: int,
               experiments: list[dict] | None = None) -> tuple[list[dict], dict]:
    """Deterministic plan. Returns (new slots, unfilled count per channel).

    `kept` are slots already drafted: they hold their date on their channel and count
    against each atom's channel and hook limits, so a re-plan never repeats them.
    """
    rng = random.Random(seed)
    end = start + timedelta(days=7 * weeks)
    kept_days = {(s["channel"], s["date"]) for s in kept}

    # 1. skeleton: (date, time, channel, format), channel by channel in a fixed order
    skeleton = []
    for ch in (c for c in CHANNELS if c in channels):
        k = cadence[ch]
        if k == 0:
            continue
        rotation = CHANNELS.index(ch)
        n = 0
        for w in range(weeks):
            ws = start + timedelta(days=7 * w)
            taken = [d for d in (ws + timedelta(i) for i in range(7)) if (ch, d.isoformat()) in kept_days]
            need = max(0, k - len(taken))
            picked = [d for d in week_days(ws, k, rotation) if d not in taken][:need]
            for d in sorted(picked):
                hour, minute = rng.choice(POST_HOURS_UTC[ch]), rng.choice(POST_MINUTES)
                rot = FORMAT_ROTATION[ch]
                skeleton.append({"date": d.isoformat(), "time_utc": f"{hour:02d}:{minute:02d}",
                                 "channel": ch, "format": rot[n % len(rot)]})
                n += 1
    skeleton.sort(key=lambda s: (s["date"], s["time_utc"], CHANNELS.index(s["channel"])))
    if experiments:   # arms of running experiments (45), balanced by weekday and hour
        assign_arms(skeleton, experiments, kept, seed)
        skeleton.sort(key=lambda s: (s["date"], s["time_utc"], CHANNELS.index(s["channel"])))

    # 2. state from kept slots
    order = atoms[:]
    rng.shuffle(order)
    rank = {a["id"]: i for i, a in enumerate(order)}
    hook_order = list(HOOK_STYLES)
    rng.shuffle(hook_order)
    used_channels: dict[int, set] = {a["id"]: set() for a in atoms}
    used_hooks: dict[int, set] = {a["id"]: set() for a in atoms}
    used_dates: dict[int, list[date]] = {a["id"]: [] for a in atoms}
    used_on: dict[tuple, list[date]] = {}   # (atom id, channel) -> dates used there
    hook_count: dict[str, Counter] = {ch: Counter() for ch in CHANNELS}
    promo_ids = {a["id"] for a in atoms if a["promo"]}
    promo_used: Counter = Counter()
    total_on: Counter = Counter(s["channel"] for s in skeleton)
    for s in kept:
        aid = s["atom_id"]
        used_channels.setdefault(aid, set()).add(s["channel"])
        used_hooks.setdefault(aid, set()).add(s["hook_style"])
        used_dates.setdefault(aid, []).append(date.fromisoformat(s["date"]))
        used_on.setdefault((aid, s["channel"]), []).append(date.fromisoformat(s["date"]))
        hook_count[s["channel"]][s["hook_style"]] += 1
        if s.get("promo"):
            promo_used[s["channel"]] += 1
        if start <= date.fromisoformat(s["date"]) < end:
            total_on[s["channel"]] += 1
    # promo <= promo_max of the channel's slots (rounded down), per channel and so overall
    promo_cap = {ch: math.floor(promo_max * total_on[ch] + 1e-9) for ch in total_on}

    # 3. assign atom + hook to each slot, earliest first
    out, unfilled = [], Counter()
    for s in skeleton:
        ch, d = s["channel"], date.fromisoformat(s["date"])
        def channel_ok(a):
            prev = used_on.get((a["id"], ch), [])
            if not prev:
                return len(used_channels[a["id"]]) < MAX_CHANNELS_PER_ATOM
            return len(prev) < REUSE_PER_CHANNEL and all((d - u).days >= REUSE_GAP_DAYS for u in prev)

        forced = s.get("forced_hook")
        candidates = [
            a for a in order
            if channel_ok(a)
            and len(used_hooks[a["id"]]) < len(HOOK_STYLES)
            and (forced is None or forced not in used_hooks[a["id"]])
            and (a["id"] not in promo_ids or promo_used[ch] < promo_cap.get(ch, 0))
        ]
        if not candidates:
            unfilled[ch] += 1
            continue

        def score(a):
            near = any(abs((d - u).days) < 2 for u in used_dates[a["id"]])  # 48 h across channels
            reuse = (a["id"], ch) in used_on          # fresh atoms first, reuse only to fill
            return (reuse, near, len(used_channels[a["id"]]), rank[a["id"]])

        atom = min(candidates, key=score)
        aid = atom["id"]
        hook = forced or min((h for h in hook_order if h not in used_hooks[aid]),
                             key=lambda h: (hook_count[ch][h], hook_order.index(h)))
        used_channels[aid].add(ch)
        used_hooks[aid].add(hook)
        used_dates[aid].append(d)
        used_on.setdefault((aid, ch), []).append(d)
        hook_count[ch][hook] += 1
        if aid in promo_ids:
            promo_used[ch] += 1
        out.append({k: v for k, v in s.items() if k != "forced_hook"} | {"atom_id": aid, "hook_style": hook})

    # 4. when atoms ran out, the promo share of what WAS filled can exceed the cap: drop the
    # latest promo slots of that channel until it holds again
    for ch in list(unfilled):
        kept_on = [s for s in kept if s["channel"] == ch and start <= date.fromisoformat(s["date"]) < end]
        while True:
            on = [s for s in out if s["channel"] == ch]
            promo = [s for s in on if s["atom_id"] in promo_ids] + [s for s in kept_on if s.get("promo")]
            if len(promo) <= promo_max * (len(on) + len(kept_on)) + 1e-9:
                break
            new_promo = [s for s in on if s["atom_id"] in promo_ids]
            if not new_promo:
                break
            out.remove(new_promo[-1])
            unfilled[ch] += 1
    return out, dict(unfilled)


def slots_with_atoms(conn: sqlite3.Connection, rows) -> list[dict]:
    out = []
    for r in rows:
        s = row_to_slot(r)
        a = conn.execute("SELECT * FROM atoms WHERE id = ?", (s["atom_id"],)).fetchone()
        s["atom"] = {k: row_to_atom(a)[k] for k in ("kind", "text", "evidence", "promo")} if a else None
        out.append(s)
    return out


# ---------- experiments (45 campaign-service)


def campaigns_url() -> str:
    """Empty (the default outside the stack) = no experiments: plans ignore them."""
    return (os.environ.get("CAMPAIGNS_URL") or "").rstrip("/")


def key_headers() -> dict:
    key = os.environ.get("INTERNAL_API_KEY")
    return {"X-API-Key": key} if key else {}


def fetch_experiments(channels: list[str]) -> tuple[list[dict], str | None]:
    """Approved and running experiments on these channels, or ([], why not)."""
    base = campaigns_url()
    if not base:
        return [], None
    try:
        r = httpx.get(f"{base}/experiments", params={"status": "approved,running"}, timeout=10)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return [], f"experiments not read from {base}: {type(e).__name__}"
    out = []
    for e in data if isinstance(data, list) else []:
        arms = e.get("arms") if isinstance(e, dict) else None
        if (not isinstance(arms, list) or len(arms) != 2 or e.get("variable") not in EXP_VARIABLES
                or not isinstance(e.get("id"), int)):
            continue
        chs = [c for c in e.get("channels") or [] if c in channels]
        if chs:
            out.append({"id": e["id"], "variable": e["variable"], "channels": chs,
                        "arms": [{"label": a["label"], "value": a["value"], "brief": a.get("brief")} for a in arms]})
    return out, None


def report_assignments(slots: list[dict], removed: dict[int, list[int]]) -> tuple[dict[int, dict], list[int]]:
    """POST each experiment's slots to 45 (/experiments/{id}/assign). Returns (per experiment
    the response or the error, slot ids whose experiment refused or could not be reached)."""
    per_exp: dict[int, list[dict]] = {}
    for sl in slots:
        if sl.get("experiment_id"):
            per_exp.setdefault(sl["experiment_id"], []).append(
                {"slot_id": sl["id"], "arm": sl["arm"], "channel": sl["channel"], "date": sl["date"],
                 "time_utc": sl["time_utc"]})
    results, failed = {}, []
    for eid in sorted(set(per_exp) | set(removed)):
        body = {"slots": per_exp.get(eid, []), "remove_slot_ids": removed.get(eid, [])}
        try:
            r = httpx.post(f"{campaigns_url()}/experiments/{eid}/assign", json=body, headers=key_headers(), timeout=10)
            ok = r.status_code < 300
            detail = r.json() if ok else {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except (httpx.HTTPError, ValueError) as e:
            ok, detail = False, {"error": f"{type(e).__name__}"}
        results[eid] = detail
        if not ok:
            failed += [x["slot_id"] for x in per_exp.get(eid, [])]
    return results, failed


@app.post("/pillars/{pillar_id}/plan", dependencies=[Depends(require_key)])
def plan(pillar_id: int, req: PlanRequest | None = None):
    req = req or PlanRequest()
    min_atoms = int(env_num("MIN_ATOMS", 12))
    ts = now_utc()
    with db() as conn:
        experiments, exp_error = fetch_experiments(pillar_or_404(conn, pillar_id)["channels"])
    with db() as conn, write(conn):
        p = pillar_or_404(conn, pillar_id)
        if p["status"] == "paused":
            raise HTTPException(409, f"pillar {pillar_id} is paused ({p['paused_reason']}); "
                                     f"POST /pillars/{pillar_id}/resume after a person has looked at it")
        extra = sorted(set(req.cadence or {}) - set(p["channels"]))
        if extra:
            raise HTTPException(422, f"cadence names channels this pillar doesn't have: {', '.join(extra)} "
                                     f"(pillar channels: {', '.join(p['channels'])})")
        atoms = [row_to_atom(r) for r in conn.execute(
            "SELECT * FROM atoms WHERE pillar_id = ? AND verified = 1 ORDER BY id", (pillar_id,)).fetchall()]
        if len(atoms) < min_atoms:
            raise HTTPException(422, f"pillar {pillar_id} has {len(atoms)} verified atoms; planning needs at "
                                     f"least {min_atoms} (MIN_ATOMS). The pillar is too thin: use a more "
                                     f"substantial source or verify more atoms.")
        start = req.start_date or date.fromisoformat(p["month"] + "-01")
        cadence = {ch: DEFAULT_CADENCE[ch] for ch in p["channels"]} | (req.cadence or {})
        seed = req.seed if req.seed is not None else pillar_id
        kept = [row_to_slot(r) | {"promo": bool(r["promo"])} for r in conn.execute(
            "SELECT s.*, a.promo FROM slots s JOIN atoms a ON a.id = s.atom_id"
            " WHERE s.pillar_id = ? AND s.status = 'drafted'", (pillar_id,)).fetchall()]
        new, unfilled = plan_slots(channels=p["channels"], cadence=cadence, start=start, weeks=req.weeks,
                                   atoms=atoms, kept=kept, promo_max=p["promo_max"], seed=seed,
                                   experiments=experiments)
        # A re-plan replaces the still-planned slots. Their calendar `idea` items would stay behind
        # as orphans, so the caller (64) gets their ids back to reject them.
        removed_items = sorted({r[0] for r in conn.execute(
            "SELECT calendar_item_id FROM slots WHERE pillar_id = ? AND status = 'planned'"
            " AND calendar_item_id IS NOT NULL", (pillar_id,)).fetchall()})
        removed_exp: dict[int, list[int]] = {}
        for r in conn.execute("SELECT id, experiment_id FROM slots WHERE pillar_id = ? AND status = 'planned'"
                              " AND experiment_id IS NOT NULL", (pillar_id,)).fetchall():
            removed_exp.setdefault(r["experiment_id"], []).append(r["id"])
        conn.execute("DELETE FROM slots WHERE pillar_id = ? AND status = 'planned'", (pillar_id,))
        for s in new:
            x = s.get("experiment") or {}
            conn.execute(
                "INSERT INTO slots (pillar_id, date, time_utc, channel, format, atom_id, hook_style, status,"
                " calendar_item_id, reason, created_at, updated_at, experiment_id, arm, exp_variable, arm_value,"
                " arm_brief) VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', NULL, NULL, ?, ?, ?, ?, ?, ?, ?)",
                (pillar_id, s["date"], s["time_utc"], s["channel"], s["format"], s["atom_id"], s["hook_style"], ts, ts,
                 x.get("id"), x.get("arm"), x.get("variable"), x.get("value"), x.get("brief")),
            )
        rows = conn.execute("SELECT * FROM slots WHERE pillar_id = ? AND status = 'planned'"
                            " ORDER BY date, time_utc, id", (pillar_id,)).fetchall()
        slots = slots_with_atoms(conn, rows)
    # Tell 45 which slot got which arm (after the commit: no write lock held over HTTP).
    exp_results: dict[int, dict] = {}
    if any(s["experiment_id"] for s in slots) or removed_exp:
        exp_results, failed = report_assignments(slots, removed_exp)
        if failed:
            # 45 refused (e.g. the experiment was stopped) or is down: these slots are not
            # part of the experiment, so they must not look like it. The forced hook stays.
            with db() as conn, write(conn):
                conn.executemany("UPDATE slots SET experiment_id = NULL, arm = NULL, exp_variable = NULL,"
                                 " arm_value = NULL, arm_brief = NULL WHERE id = ?", [(i,) for i in failed])
                rows = conn.execute("SELECT * FROM slots WHERE pillar_id = ? AND status = 'planned'"
                                    " ORDER BY date, time_utc, id", (pillar_id,)).fetchall()
                slots = slots_with_atoms(conn, rows)
    end = start + timedelta(days=7 * req.weeks)
    kept_in_window = Counter(s["channel"] for s in kept if start.isoformat() <= s["date"] < end.isoformat())
    requested = {ch: cadence[ch] * req.weeks for ch in p["channels"]}
    planned = Counter(s["channel"] for s in slots)
    return {
        "pillar_id": pillar_id, "seed": seed, "start_date": start.isoformat(),
        "end_date": (end - timedelta(days=1)).isoformat(), "weeks": req.weeks, "cadence": cadence,
        "requested": requested, "planned": {ch: planned.get(ch, 0) for ch in p["channels"]},
        "kept_drafted": {ch: kept_in_window.get(ch, 0) for ch in p["channels"]},
        "unfilled": {ch: unfilled.get(ch, 0) for ch in p["channels"]},
        "atoms_verified": len(atoms), "removed_calendar_item_ids": removed_items,
        "experiments": [{"id": e["id"], "variable": e["variable"], "channels": e["channels"],
                         "arms": {a["label"]: a["value"] for a in e["arms"]},
                         "assigned": dict(Counter(s["arm"] for s in slots if s["experiment_id"] == e["id"])),
                         "error": (exp_results.get(e["id"]) or {}).get("error")} for e in experiments],
        "experiments_error": exp_error, "slots": slots,
    }


@app.get("/pillars/{pillar_id}/slots")
def list_slots(pillar_id: int, status: Literal["planned", "drafted", "dropped"] | None = None,
               channel: str | None = None):
    sql, args = "SELECT * FROM slots WHERE pillar_id = ?", [pillar_id]
    if status:
        sql, args = sql + " AND status = ?", args + [status]
    if channel:
        sql, args = sql + " AND channel = ?", args + [channel.lower()]
    with db() as conn:
        pillar_or_404(conn, pillar_id)
        return slots_with_atoms(conn, conn.execute(sql + " ORDER BY date, time_utc, id", args).fetchall())


def slot_or_404(conn: sqlite3.Connection, slot_id: int) -> dict:
    row = conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone() if valid_id(slot_id) else None
    if row is None:
        raise HTTPException(404, f"slot {slot_id} not found")
    return row_to_slot(row)


@app.get("/slots/{slot_id}")
def get_slot(slot_id: int):
    with db() as conn:
        s = slot_or_404(conn, slot_id)
        return slots_with_atoms(conn, [conn.execute("SELECT * FROM slots WHERE id = ?", (s["id"],)).fetchone()])[0]


# planned -> drafted | dropped; drafted -> dropped; repeating the current status updates the fields.
ALLOWED = {("planned", "drafted"), ("planned", "dropped"), ("drafted", "dropped")}


@app.post("/slots/{slot_id}/status", dependencies=[Depends(require_key)])
def set_slot_status(slot_id: int, req: SlotStatus):
    with db() as conn, write(conn):
        s = slot_or_404(conn, slot_id)
        if s["status"] != req.status and (s["status"], req.status) not in ALLOWED:
            raise HTTPException(409, f"slot {slot_id} is {s['status']}; it can't become {req.status}")
        conn.execute(
            "UPDATE slots SET status = ?, calendar_item_id = COALESCE(?, calendar_item_id),"
            " reason = COALESCE(?, reason), updated_at = ? WHERE id = ?",
            (req.status, req.calendar_item_id, req.reason, now_utc(), slot_id),
        )
        return slots_with_atoms(conn, [conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()])[0]


# ---------- novelty


@app.post("/novelty/register", dependencies=[Depends(require_key)])
async def novelty_register(req: NoveltyItem):
    """Upsert on item_id (a revised draft replaces its text). Embeds now if Ollama is up."""
    with db() as conn, write(conn):
        conn.execute(
            "INSERT INTO novelty_items (item_id, channel, text, created_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (item_id) DO UPDATE SET channel = excluded.channel, text = excluded.text,"
            " created_at = excluded.created_at",
            (req.item_id, req.channel, req.text, req.created_at or now_utc()),
        )
    embedded = await embeddings_for([req.text]) is not None
    return {"item_id": req.item_id, "channel": req.channel, "embedded": embedded}


@app.post("/novelty/check")
async def novelty_check(req: NoveltyCheck):
    ngram_max = env_num("NGRAM_MAX", 0.30)
    embed_max = env_num("EMBED_MAX", 0.85)
    since = fmt(datetime.now(timezone.utc) - timedelta(days=env_num("NOVELTY_DAYS", 90)))
    sql, args = "SELECT * FROM novelty_items WHERE created_at >= ?", [since]
    if not req.any_channel:
        sql, args = sql + " AND channel = ?", args + [req.channel]
    if req.exclude_item_id is not None:
        sql, args = sql + " AND item_id != ?", args + [req.exclude_item_id]
    with db() as conn:
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY created_at DESC", args).fetchall()]

    qw = words(req.text)
    qgrams, qopen = ngrams(qw), opening(qw)
    scores = []
    for r in rows:
        rw = words(r["text"])
        scores.append({"item_id": r["item_id"], "channel": r["channel"], "text": r["text"],
                       "score_ngram": round(jaccard(qgrams, ngrams(rw)), 4),
                       "same_opening": bool(qopen) and opening(rw) == qopen, "score_embed": None})

    embedding_checked = False
    if rows:
        vecs = await embeddings_for([req.text] + [r["text"] for r in rows])
        if vecs is not None:
            embedding_checked = True
            q = vecs[req.text]
            for s in scores:
                v = vecs[s["text"]]
                s["score_embed"] = round(cosine(q, v), 4) if len(v) == len(q) else None
    else:
        embedding_checked = True  # nothing to compare against: trivially checked

    reasons = []
    for s in scores:
        if s["score_ngram"] >= ngram_max:
            reasons.append(f"5-gram overlap {s['score_ngram']:.2f} with item {s['item_id']} (max {ngram_max:.2f})")
        if s["same_opening"]:
            reasons.append(f"same first 8 words as item {s['item_id']}")
        if s["score_embed"] is not None and s["score_embed"] >= embed_max:
            reasons.append(f"embedding cosine {s['score_embed']:.2f} with item {s['item_id']} (max {embed_max:.2f})")

    closest = None
    if scores:
        best = max(scores, key=lambda s: (s["score_embed"] if s["score_embed"] is not None else -1,
                                          s["score_ngram"]))
        if best["score_embed"] is None:
            best = max(scores, key=lambda s: s["score_ngram"])
        closest = {k: best[k] for k in ("item_id", "channel", "score_ngram", "score_embed")}
    return {"novel": not reasons, "reasons": reasons, "closest": closest, "compared": len(rows),
            "embedding_checked": embedding_checked, "embed_model": embed_model() if embedding_checked else None,
            "thresholds": {"ngram_max": ngram_max, "embed_max": embed_max,
                           "days": env_num("NOVELTY_DAYS", 90)}}


# ---------- outcomes and the stop rule


def health_of(conn: sqlite3.Connection, p: dict) -> dict:
    # the window: decisions recorded since the last resume (rowid only grows)
    seq = conn.execute("SELECT window_seq FROM pillars WHERE id = ?", (p["id"],)).fetchone()[0]
    rows = conn.execute("SELECT decision, COUNT(*) FROM outcomes WHERE pillar_id = ? AND rowid > ?"
                        " GROUP BY decision", (p["id"], seq)).fetchall()
    c = Counter({r[0]: r[1] for r in rows})
    decided = sum(c.values())
    min_decisions = int(env_num("MIN_DECISIONS", 10))
    reject_max, clean_min = env_num("REJECT_MAX", 0.25), env_num("CLEAN_MIN", 0.5)
    clean = c["approved"] / decided if decided else None
    rejected = c["rejected"] / decided if decided else None
    trip = None
    if decided >= min_decisions:
        if rejected > reject_max:
            trip = f"rejected {rejected:.0%} of {decided} decisions (max {reject_max:.0%})"
        elif clean < clean_min:
            trip = f"approved unedited {clean:.0%} of {decided} decisions (min {clean_min:.0%})"
    return {"pillar_id": p["id"], "since": p["window_start"], "decided": decided,
            "approved": c["approved"], "edited": c["edited"], "rejected": c["rejected"],
            "approved_clean_rate": round(clean, 4) if clean is not None else None,
            "rejected_rate": round(rejected, 4) if rejected is not None else None,
            "min_decisions": min_decisions, "trip": trip}


def apply_stop_rule(conn: sqlite3.Connection, pillar_id: int) -> dict:
    """Pause the pillar when the rule trips. Only a person (POST /resume) un-pauses it."""
    p = pillar_or_404(conn, pillar_id)
    h = health_of(conn, p)
    if h["trip"] and p["status"] != "paused":
        conn.execute("UPDATE pillars SET status = 'paused', paused_reason = ?, updated_at = ? WHERE id = ?",
                     (h["trip"], now_utc(), pillar_id))
        p = pillar_or_404(conn, pillar_id)
    h.pop("trip")
    return h | {"paused": p["status"] == "paused", "reason": p["paused_reason"]}


@app.post("/pillars/{pillar_id}/outcomes", dependencies=[Depends(require_key)])
def add_outcome(pillar_id: int, req: Outcome):
    """The FIRST decision per item counts; a draft rewritten after a rejection stays a rejection."""
    with db() as conn, write(conn):
        pillar_or_404(conn, pillar_id)
        cur = conn.execute("INSERT OR IGNORE INTO outcomes (pillar_id, item_id, decision, created_at)"
                           " VALUES (?, ?, ?, ?)", (pillar_id, req.item_id, req.decision, now_utc()))
        counted = conn.execute("SELECT decision FROM outcomes WHERE pillar_id = ? AND item_id = ?",
                               (pillar_id, req.item_id)).fetchone()[0]
        return {"recorded": bool(cur.rowcount), "counted_decision": counted} | apply_stop_rule(conn, pillar_id)


@app.get("/pillars/{pillar_id}/health")
def pillar_health(pillar_id: int):
    with db() as conn, write(conn):
        return apply_stop_rule(conn, pillar_id)


@app.post("/pillars/{pillar_id}/resume", dependencies=[Depends(require_key)])
def resume(pillar_id: int):
    """Un-pause and start a new window: decisions before now no longer count."""
    ts = now_utc()
    with db() as conn, write(conn):
        pillar_or_404(conn, pillar_id)
        seq = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM outcomes").fetchone()[0]
        conn.execute("UPDATE pillars SET status = 'active', paused_reason = NULL, window_start = ?,"
                     " window_seq = ?, updated_at = ? WHERE id = ?", (ts, seq, ts, pillar_id))
        p = pillar_or_404(conn, pillar_id)
        h = health_of(conn, p)
        h.pop("trip")
        return h | {"paused": False, "reason": None}
