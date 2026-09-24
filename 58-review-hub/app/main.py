"""Review hub: a reviews inbox, a proof bank of consented testimonials, and reply context.

It stores reviews a person imports (Google, Trustpilot, or typed in by hand), keeps
testimonials that are verbatim quotes from those reviews, and checks drafts so that every
quotation in them is a real, consented testimonial. It never scrapes a platform and never
posts a reply: a person posts replies. It uses no LLM.
"""
import hmac
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator

app = FastAPI(title="review-hub")

MAX_IMPORT = 1000
# Words that mean the reviewer reports a concrete problem. Whole words only ("late" must
# not match "latest"), so the forms are listed explicitly.
PROBLEM_WORDS = (
    "late", "delayed", "delay", "broken", "broke", "damaged", "refund", "refunds",
    "refunded", "missing", "wrong", "never arrived", "stale", "leaking", "leaked",
    "overcharged", "charged twice", "cancel", "cancelled", "canceled",
)
PROBLEM_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in PROBLEM_WORDS) + r")\b", re.I)
# Reviews a person must handle, decided in code: the model missed 1 of 3 health cases in the
# eval (night 5). Health/safety and legal words -> needs_human, whatever the model says.
ESCALATE_WORDS = (
    "sick", "ill", "illness", "nausea", "nauseous", "vomit", "vomited", "vomiting", "allergic",
    "allergy", "reaction", "hospital", "doctor", "poisoning", "burn", "burned", "burnt", "injury",
    "injured", "choked", "glass", "mold", "mould", "lawyer", "attorney", "lawsuit", "sue", "suing",
    "legal action", "court", "chargeback", "fraud", "scam", "police", "trading standards",
)
ESCALATE_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in ESCALATE_WORDS) + r")\b", re.I)
# A quotation: straight or curly double quotes, in any pairing ("…", “…”, „…“, "…”).
QUOTE_RE = re.compile(r"[\"“„]([^\"“”„]+)[\"”“]")

REVIEW_COLUMNS = (
    "id", "source", "external_id", "author", "rating", "text", "created_at", "status",
    "imported_at", "updated_at",
)
TESTIMONIAL_COLUMNS = (
    "id", "quote", "author_display", "source_review_id", "consent", "consent_note", "created_at",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    author TEXT NOT NULL,
    rating INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS reviews_status_rating ON reviews (status, rating);
CREATE TABLE IF NOT EXISTS testimonials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote TEXT NOT NULL,
    author_display TEXT NOT NULL,
    source_review_id INTEGER REFERENCES reviews (id),
    consent INTEGER NOT NULL,
    consent_note TEXT,
    created_at TEXT NOT NULL
);
"""
_migrate_lock = threading.Lock()
_migrated: set[str] = set()


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


# ---------- text


def normalize(text: str) -> str:
    """Comparison form: whitespace runs collapsed to one space, curly apostrophes made straight.

    Only typography changes; words, punctuation and case stay exactly as written, so a
    paraphrase never matches.
    """
    return " ".join(text.replace("’", "'").replace("‘", "'").split())


TITLES = {"dr", "mr", "mrs", "ms", "mx", "prof"}
ANONYMOUS_AUTHORS = {"a google user", "anonymous", "customer", "verified buyer"}


def first_name(author: str) -> str | None:
    """First name for the greeting: "Maya Chen" -> "Maya", "Dr. Ann Lee" -> "Ann".

    None for anonymous or initial-only authors ("A Google user", "J.").
    """
    if author.strip().casefold() in ANONYMOUS_AUTHORS:
        return None
    parts = [p.strip(",;:.") for p in author.strip().split()]
    parts = [p for p in parts if p.casefold() not in TITLES]
    name = parts[0] if parts else ""
    return name if len(name) >= 2 else None


# ---------- storage


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/reviews.sqlite")


def migrate(conn: sqlite3.Connection) -> None:
    """Create the schema once per DB path per process.

    WAL lets readers work while a write is in progress. Parallel first requests race here:
    the lock covers threads, and BEGIN IMMEDIATE (one writer at a time) covers other
    processes, so the schema is created exactly once.
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
    """One write transaction, taken before the first read so check-then-insert can't race."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def row_to_review(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in REVIEW_COLUMNS}


def row_to_testimonial(row: sqlite3.Row) -> dict:
    t = {k: row[k] for k in TESTIMONIAL_COLUMNS}
    t["consent"] = bool(t["consent"])
    return t


def review_or_404(conn: sqlite3.Connection, review_id: int) -> dict:
    if not 0 < review_id < 2**63:  # SQLite integers are 64-bit; larger ids raised OverflowError
        raise HTTPException(404, f"review {review_id} not found")
    row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"review {review_id} not found")
    return row_to_review(row)


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- models


def _not_blank(v: str) -> str:
    if not v.strip():
        raise ValueError("must not be empty")
    return v.strip()


class NewReview(BaseModel):
    source: Literal["google", "trustpilot", "manual"]
    external_id: str = Field(max_length=200)
    author: str = Field(max_length=200)
    rating: StrictInt = Field(ge=1, le=5)
    text: str = Field(max_length=20000)
    created_at: str

    @field_validator("external_id", "author", "text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        return _not_blank(v)

    @field_validator("created_at")
    @classmethod
    def when(cls, v: str) -> str:
        return to_utc_iso(v)


class StatusChange(BaseModel):
    status: Literal["new", "drafted", "replied", "ignored"]


class NewTestimonial(BaseModel):
    quote: str = Field(max_length=2000)
    author_display: str = Field(max_length=200)
    source_review_id: StrictInt | None = Field(default=None, ge=1)
    consent: StrictBool
    consent_note: str | None = Field(default=None, max_length=1000)

    @field_validator("quote", "author_display")
    @classmethod
    def not_blank(cls, v: str) -> str:
        return _not_blank(v)


class CheckRequest(BaseModel):
    text: str = Field(max_length=100000)


# ---------- endpoints


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/reviews/import", dependencies=[Depends(require_key)])
def import_reviews(reviews: list[NewReview]):
    """Idempotent on (source, external_id): a review already stored is left untouched."""
    if len(reviews) > MAX_IMPORT:
        raise HTTPException(422, f"at most {MAX_IMPORT} reviews per import")
    ts = now_utc()
    imported, duplicates = [], []
    with db() as conn, write(conn):
        for r in reviews:
            cur = conn.execute(
                "INSERT OR IGNORE INTO reviews (source, external_id, author, rating, text,"
                " created_at, status, imported_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'new', ?, ?)",
                (r.source, r.external_id, r.author, r.rating, r.text, r.created_at, ts, ts),
            )
            key = {"source": r.source, "external_id": r.external_id}
            if cur.rowcount:
                imported.append({**key, "id": cur.lastrowid})
            else:
                duplicates.append(key)
    return {"imported": len(imported), "duplicates": len(duplicates),
            "ids": [i["id"] for i in imported], "skipped": duplicates}


@app.get("/reviews")
def list_reviews(
    status: Literal["new", "drafted", "replied", "ignored"] | None = None,
    min_rating: int | None = Query(None, ge=1, le=5),
    max_rating: int | None = Query(None, ge=1, le=5),
):
    if min_rating is not None and max_rating is not None and min_rating > max_rating:
        raise HTTPException(422, "min_rating is greater than max_rating")
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if min_rating is not None:
        where.append("rating >= ?")
        args.append(min_rating)
    if max_rating is not None:
        where.append("rating <= ?")
        args.append(max_rating)
    sql = "SELECT * FROM reviews"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC"
    with db() as conn:
        return [row_to_review(r) for r in conn.execute(sql, args)]


@app.get("/reviews/{review_id}")
def get_review(review_id: int):
    with db() as conn:
        return review_or_404(conn, review_id)


@app.post("/reviews/{review_id}/status", dependencies=[Depends(require_key)])
def change_status(review_id: int, req: StatusChange):
    with db() as conn, write(conn):
        review_or_404(conn, review_id)
        conn.execute("UPDATE reviews SET status = ?, updated_at = ? WHERE id = ?",
                     (req.status, now_utc(), review_id))
        return review_or_404(conn, review_id)


@app.get("/reviews/{review_id}/reply-context")
def reply_context(review_id: int):
    """What the `review_reply` prompt needs, plus flags for routing the draft."""
    with db() as conn:
        r = review_or_404(conn, review_id)
    problems = sorted({m.lower() for m in PROBLEM_RE.findall(r["text"])})
    name = first_name(r["author"])
    negative = r["rating"] <= 2
    return {
        "review_id": r["id"],
        "source": r["source"],
        "review": r["text"],
        "rating": r["rating"],
        "author_first_name": name,
        "negative": negative,
        "mentions_problem": bool(problems),
        "problem_words": problems,
        # Workflows must route these to a person even if the model's needs_human is false.
        "needs_human": bool(escalate := sorted({m.lower() for m in ESCALATE_RE.findall(r["text"])})),
        "escalate_words": escalate,
        # Ready to pass as the gateway's `vars` for prompt review_reply.
        "vars": {"review": r["text"], "rating": r["rating"],
                 "author_first_name": name or "there", "negative": negative},
    }


@app.post("/testimonials", status_code=201, dependencies=[Depends(require_key)])
def create_testimonial(req: NewTestimonial):
    quote = normalize(req.quote)
    with db() as conn, write(conn):
        if req.source_review_id is not None:
            review = review_or_404(conn, req.source_review_id)
            if quote not in normalize(review["text"]):
                raise HTTPException(422, {
                    "message": "quote is not a verbatim part of the review; copy it exactly "
                               "(testimonials may not be paraphrased, shortened mid-sentence "
                               "with ellipses, or combined)",
                    "review_id": review["id"],
                })
        cur = conn.execute(
            "INSERT INTO testimonials (quote, author_display, source_review_id, consent,"
            " consent_note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (quote, req.author_display, req.source_review_id, int(req.consent),
             (req.consent_note or "").strip() or None, now_utc()),
        )
        row = conn.execute("SELECT * FROM testimonials WHERE id = ?", (cur.lastrowid,)).fetchone()
        return row_to_testimonial(row)


class ConsentChange(BaseModel):
    consent: bool
    note: str | None = None


@app.post("/testimonials/{testimonial_id}/consent", dependencies=[Depends(require_key)])
def set_consent(testimonial_id: int, req: ConsentChange):
    """Record consent given or withdrawn. Withdrawn quotes stop being usable at once."""
    with db() as conn, write(conn):
        row = conn.execute("SELECT * FROM testimonials WHERE id = ?", (testimonial_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"testimonial {testimonial_id} not found")
        note = f"[{now_utc()}] consent {'given' if req.consent else 'withdrawn'}" + (
            f": {req.note.strip()}" if req.note and req.note.strip() else "")
        old = row["consent_note"]
        conn.execute("UPDATE testimonials SET consent = ?, consent_note = ? WHERE id = ?",
                     (int(req.consent), f"{old}\n{note}" if old else note, testimonial_id))
        return row_to_testimonial(conn.execute("SELECT * FROM testimonials WHERE id = ?",
                                               (testimonial_id,)).fetchone())


@app.get("/testimonials")
def list_testimonials(usable: bool | None = Query(None, description="true: only consent=true")):
    sql = "SELECT * FROM testimonials"
    if usable is True:
        sql += " WHERE consent = 1"
    elif usable is False:
        sql += " WHERE consent = 0"
    with db() as conn:
        return [row_to_testimonial(r) for r in conn.execute(sql + " ORDER BY id")]


@app.post("/testimonials/check")
def check_quotes(req: CheckRequest):
    """Every quotation in `text` must be (part of) a consented testimonial, word for word."""
    with db() as conn:
        bank = [row_to_testimonial(r) for r in conn.execute("SELECT * FROM testimonials ORDER BY id")]
    quotes = []
    for m in QUOTE_RE.finditer(req.text):
        span = normalize(m.group(1))
        if not span:
            continue
        usable = next((t for t in bank if t["consent"] and span in t["quote"]), None)
        no_consent = next((t for t in bank if not t["consent"] and span in t["quote"]), None)
        if usable:
            entry = {"ok": True, "testimonial_id": usable["id"], "reason": None}
        elif no_consent:
            entry = {"ok": False, "testimonial_id": no_consent["id"],
                     "reason": "testimonial has no consent to quote"}
        else:
            entry = {"ok": False, "testimonial_id": None,
                     "reason": "not a verbatim consented testimonial (invented or paraphrased)"}
        quotes.append({"text": m.group(1), "start": m.start(), "end": m.end(), **entry})
    bad = [q for q in quotes if not q["ok"]]
    return {"ok": not bad, "quotes": quotes, "violations": bad}
