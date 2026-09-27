"""Customer language ("voice of customer"): the words customers use, mined from real text.

It stores what customers wrote or searched (reviews, search queries, surveys, support
messages, sales-call notes, comments), finds the phrases they repeat (in code, word
n-grams) and the themes they talk about (the LLM proposes, code keeps only verbatim
quotes), and serves the phrases most relevant to a topic so writers can reuse them.

Everything it returns traces to source text: a phrase is shown in a form copied from a
source, a theme quote is an exact substring of its source, a persona attribute cites
themes or quotes. Nothing here invents a customer's words.
"""
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import threading
from array import array
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="customer-language")

MAX_IMPORT = 5000
KINDS = ("review", "search_query", "survey", "support", "sales_call", "comment")
THEME_KINDS = ("pain", "desire", "objection", "trigger", "outcome", "word_choice")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    text TEXT NOT NULL,
    weight REAL NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (kind, source_ref)
);
CREATE TABLE IF NOT EXISTS phrases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase TEXT NOT NULL,
    normalized TEXT NOT NULL UNIQUE,
    words INTEGER NOT NULL,
    sources INTEGER NOT NULL,
    occurrences INTEGER NOT NULL,
    weight REAL NOT NULL,
    source_ids TEXT NOT NULL,
    kinds TEXT NOT NULL,
    examples TEXT NOT NULL,
    mined_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    mined_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS theme_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id INTEGER NOT NULL REFERENCES themes (id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    quote TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS personas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS embeddings (
    key TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    vec BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""
_migrate_lock = threading.Lock()
_migrated: set[str] = set()


# ---------- config


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def key_headers() -> dict:
    key = os.environ.get("INTERNAL_API_KEY")
    return {"X-API-Key": key} if key else {}


# ---------- storage (same pattern as 58: WAL, schema once, BEGIN IMMEDIATE for writes)


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/voc.sqlite")


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


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))


def get_meta(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- text


def normalize(text: str) -> str:
    """Comparison form: curly apostrophes straight, whitespace runs one space, casefolded."""
    return " ".join(text.replace("’", "'").replace("‘", "'").split()).casefold()


WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")
# A gap between two words that ends a phrase: sentence punctuation, brackets, quotes,
# a line break, or a dash between spaces. A hyphen inside a word ("single-origin") does not.
BREAK_RE = re.compile(r"[.!?;:,()\[\]{}\"“”…/|\n]|\s[-–—]\s|[–—]")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
QUOTE_CHARS = "\"“”„«»"

STOPWORDS = frozenset("""
a about above after again against all am an and any are aren't as at be because been before
being below between both but by can could couldn't did do does doing down during each few for
from further get got had has have having he her here hers herself him himself his how i i'd
i'll i'm i've if in into is it it's its itself just let's me more most my myself of off on once
only or other our ours ourselves out over own same she should so some such than that that's the
their theirs them themselves then there there's these they they're this those through to too
under until up very was we we're were what when where which while who whom why will with would
you you're your yours yourself yourselves also really one ones much many even still well like
""".split())
# Particles that finish a phrasal verb ("never run out", "it turns up"): a phrase may end with them.
PARTICLES = frozenset("out up off down over away back".split())
# Words that make a phrase mean the opposite. A phrase may start with them
# ("doesn't taste like decaf"), but not end with them.
NEGATIONS = frozenset("""
no not never don't doesn't didn't isn't wasn't aren't weren't can't cannot won't wouldn't
couldn't shouldn't haven't hasn't hadn't nothing without
""".split())


def tokens(text: str) -> list[tuple[str, int, int, int]]:
    """(word, start, end, segment) for every word; a segment never crosses sentence punctuation."""
    low = text.replace("’", "'").replace("‘", "'").lower()
    if len(low) != len(text):  # a rare character changed length when lowercased
        low = "".join(c.lower() if len(c.lower()) == 1 else c for c in text.replace("’", "'"))
    out, seg, prev_end = [], 0, 0
    for m in WORD_RE.finditer(low):
        if out and BREAK_RE.search(text[prev_end:m.start()]):
            seg += 1
        out.append((m.group(0), m.start(), m.end(), seg))
        prev_end = m.end()
    return out


def ngrams(text: str, min_n: int, max_n: int):
    """Yield (normalized phrase, verbatim span) for every stopword-trimmed n-gram."""
    toks = tokens(text)
    for i in range(len(toks)):
        for n in range(min_n, max_n + 1):
            j = i + n
            if j > len(toks) or toks[j - 1][3] != toks[i][3]:
                break
            words = [t[0] for t in toks[i:j]]
            first, last = words[0], words[-1]
            if (first in STOPWORDS and first not in NEGATIONS) or last in NEGATIONS or (
                    last in STOPWORDS and last not in PARTICLES):
                continue
            if all(w.isdigit() for w in words):
                continue
            yield " ".join(words), text[toks[i][1]:toks[j - 1][2]]


def find_verbatim(quote: str, text: str) -> str | None:
    """The exact span of `text` that `quote` copies (case, whitespace and apostrophe style
    may differ; words and punctuation may not). None when it is not a verbatim part."""
    q = quote.strip().strip(QUOTE_CHARS + "'").strip()
    q = re.sub(r"(\.\.\.|…)$", "", q).strip()
    parts = q.split()
    if not parts:
        return None
    pattern = r"\s+".join(re.escape(p.replace("’", "'").replace("‘", "'")).replace("'", "['’‘]") for p in parts)
    m = re.search(pattern, text, re.I)
    return text[m.start():m.end()] if m else None


def contains_phrase(text: str, phrase: str) -> bool:
    """Case-insensitive, whitespace-normalized, on word boundaries."""
    t, p = f" {normalize(text)} ", normalize(phrase)
    return bool(p) and re.search(r"(?<![a-z0-9'])" + re.escape(p) + r"(?![a-z0-9'])", t) is not None


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER_RE.findall(text)}


# ---------- mining, part 1: n-grams (no LLM)


def mine_phrases(sources: list[dict], min_n: int = 2, max_n: int = 5, min_sources: int = 2,
                 max_phrases: int = 300, ignore: list[str] | None = None) -> list[dict]:
    """Phrases that appear in at least `min_sources` different sources.

    Sources with the same text (after normalization) count once. A shorter phrase is dropped
    when a longer kept phrase contains it and comes from exactly the same sources.
    """
    ignore_n = {normalize(t) for t in (ignore or []) if t.strip()}
    seen_texts: dict[str, int] = {}
    by_phrase: dict[str, dict] = {}
    for s in sources:
        key = normalize(s["text"])
        if key in seen_texts:
            continue
        seen_texts[key] = s["id"]
        for norm, span in ngrams(s["text"], min_n, max_n):
            if any(norm == ig or norm in ig for ig in ignore_n):
                continue
            p = by_phrase.setdefault(norm, {"sources": {}, "occurrences": 0, "surface": Counter(),
                                            "examples": {}})
            p["occurrences"] += 1
            p["surface"][span] += 1
            if s["id"] not in p["sources"]:
                p["sources"][s["id"]] = s
                p["examples"][s["id"]] = span
    kept = [(norm, p) for norm, p in by_phrase.items() if len(p["sources"]) >= min_sources]
    kept.sort(key=lambda x: (-len(x[0].split()), x[0]))
    final: list[tuple[str, dict]] = []
    for norm, p in kept:
        ids = set(p["sources"])
        if any(f" {norm} " in f" {q} " and ids == set(qp["sources"]) for q, qp in final):
            continue
        final.append((norm, p))
    out = []
    for norm, p in final:
        srcs = list(p["sources"].values())
        # Most common verbatim form; ties go to the lowercase one (reads naturally mid-sentence).
        surface = sorted(p["surface"].items(), key=lambda kv: (-kv[1], kv[0] != kv[0].lower(), kv[0]))[0][0]
        out.append({
            "phrase": " ".join(surface.split()),
            "normalized": norm,
            "words": len(norm.split()),
            "sources": len(srcs),
            "occurrences": p["occurrences"],
            "weight": round(sum(s.get("weight", 1.0) for s in srcs), 3),
            "source_ids": sorted(s["id"] for s in srcs),
            "kinds": sorted({s["kind"] for s in srcs}),
            "examples": [{"source_id": sid, "text": span} for sid, span in sorted(p["examples"].items())][:5],
        })
    out.sort(key=lambda x: (-x["sources"], -x["weight"], -x["words"], x["normalized"]))
    return out[:max_phrases]


# ---------- mining, part 2: themes (LLM proposes, code checks every quote)


def gateway_url() -> str:
    return env("GATEWAY_URL", "http://llm-gateway:8000").rstrip("/")


async def run_prompt(prompt: str, vars_: dict) -> dict:
    """Gateway /v1/run; raises HTTPException(502) with the reason when it fails."""
    try:
        async with httpx.AsyncClient(timeout=env_num("GATEWAY_TIMEOUT", 300)) as client:
            r = await client.post(f"{gateway_url()}/v1/run", json={"prompt": prompt, "vars": vars_},
                                  headers=key_headers())
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"gateway unreachable for {prompt}: {type(exc).__name__}")
    if r.status_code != 200:
        raise HTTPException(502, f"gateway {r.status_code} for {prompt}: {r.text[:300]}")
    body = r.json()
    if not isinstance(body.get("output"), dict):
        raise HTTPException(502, f"gateway returned no JSON object for {prompt}")
    return body


def snippet_block(sources: list[dict], max_chars: int) -> str:
    lines = []
    for s in sources:
        text = " ".join(s["text"].split())
        if len(text) > max_chars:
            text = text[:max_chars].rsplit(" ", 1)[0] + " ..."
        lines.append(f"[s{s['id']}] ({s['kind']}) {text}")
    return "\n".join(lines)


def parse_source_id(value) -> int | None:
    m = re.fullmatch(r"\s*\[?s?(\d+)\]?\s*", str(value), re.I)
    return int(m.group(1)) if m else None


def check_themes(proposed: list, by_id: dict[int, dict], allowed_ids: set[int],
                 stats: dict, min_quote_words: int = 2) -> list[dict]:
    """Keep only quotes that are verbatim parts of the source they cite (and in this batch)."""
    out = []
    for t in proposed or []:
        if not isinstance(t, dict):
            continue
        name = " ".join(str(t.get("name", "")).split())[:80]
        kind = str(t.get("kind", "")).strip().lower()
        if not name or kind not in THEME_KINDS:
            stats["themes_invalid"] += 1
            continue
        quotes = []
        for q in t.get("quotes") or []:
            stats["quotes_proposed"] += 1
            sid = parse_source_id(q.get("source_id")) if isinstance(q, dict) else None
            text = str(q.get("quote", "")) if isinstance(q, dict) else ""
            if sid is None or sid not in allowed_ids or sid not in by_id:
                stats["quotes_dropped"]["unknown_source"] += 1
                continue
            span = find_verbatim(text, by_id[sid]["text"])
            if span is None:
                stats["quotes_dropped"]["not_verbatim"] += 1
                continue
            if len(WORD_RE.findall(span.lower())) < min_quote_words:
                stats["quotes_dropped"]["too_short"] += 1
                continue
            quotes.append({"source_id": sid, "quote": span})
        out.append({"name": name, "kind": kind, "quotes": quotes})
    return out


def theme_key(name: str) -> str:
    return " ".join(re.sub(r"[_\-]+", " ", name).split()).casefold()


def merge_themes(themes: list[dict], min_quotes: int, min_theme_sources: int, stats: dict) -> list[dict]:
    """Batches name the same theme separately. Two themes are merged when their names match
    (ignoring case, "_" and "-") or when they have the same kind and share a quote. The
    first theme's name and kind are kept."""
    merged: list[dict] = []
    for t in themes:
        qkeys = {(q["source_id"], normalize(q["quote"])) for q in t["quotes"]}
        m = next((m for m in merged if m["key"] == theme_key(t["name"])
                  or (m["kind"] == t["kind"] and m["qkeys"] & qkeys)), None)
        if m is None:
            m = {"name": " ".join(re.sub(r"_+", " ", t["name"]).split()), "kind": t["kind"],
                 "key": theme_key(t["name"]), "quotes": [], "qkeys": set()}
            merged.append(m)
        elif theme_key(t["name"]) != m["key"]:
            stats["themes_merged"] += 1
        for q in t["quotes"]:
            k = (q["source_id"], normalize(q["quote"]))
            if k in m["qkeys"] or any(k[0] == o[0] and (k[1] in o[1] or o[1] in k[1]) for o in m["qkeys"]):
                stats["quotes_dropped"]["duplicate"] += 1
                continue
            m["qkeys"].add(k)
            m["quotes"].append(q)
    kept = []
    for m in merged:
        if len(m["quotes"]) < min_quotes or len({q["source_id"] for q in m["quotes"]}) < min_theme_sources:
            stats["themes_dropped"].append({"name": m["name"], "kind": m["kind"], "valid_quotes": len(m["quotes"])})
            continue
        kept.append({"name": m["name"], "kind": m["kind"], "quotes": m["quotes"]})
    return kept


def mixed_order(sources: list[dict]) -> list[dict]:
    """A fixed pseudo-random order, so every LLM batch mixes reviews, searches and messages."""
    return sorted(sources, key=lambda s: hashlib.sha256(f"{s['kind']}\0{s['source_ref']}".encode()).hexdigest())


# ---------- embeddings (Ollama /api/embed, cached in SQLite, same as 61)


def embed_model() -> str:
    return env("EMBED_MODEL", "qwen3-embedding:0.6b")


def embed_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\0{text}".encode()).hexdigest()


async def ollama_embed(texts: list[str]) -> list[list[float]] | None:
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


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def stem(word: str) -> str:
    """Crude suffix stripping, the same on both sides: running/run, arrived/arrive, bags/bag."""
    for suf in ("ing", "ies", "es", "ed", "s"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            word = word[: -len(suf)] + ("y" if suf == "ies" else "")
            if suf in ("ing", "ed") and len(word) >= 3 and word[-1] == word[-2] and word[-1] not in "aeiouslz":
                word = word[:-1]  # running -> runn -> run
            break
    if len(word) >= 4 and word.endswith("e") and not word.endswith("ee"):
        word = word[:-1]  # arrive -> arriv, like arrived
    return word


def content_stems(text: str) -> set[str]:
    return {stem(w) for w in WORD_RE.findall(text.replace("’", "'").lower())
            if w not in STOPWORDS and w not in NEGATIONS}


def keyword_score(topic: set[str], text: str) -> float:
    c = content_stems(text)
    return len(topic & c) / math.sqrt(len(c)) if c else 0.0


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def hybrid_score(cos: float, topic: set[str], text: str) -> float:
    """HYBRID_EMBED_WEIGHT * cosine + (1 - it) * keyword Jaccard of content stems.

    qwen3-embedding:0.6b puts short phrases that share a category word ("coffee
    subscription") near the top for almost any topic; the Jaccard term rewards a phrase for
    sharing the topic's own words and penalizes it for words the topic doesn't have.
    """
    w = min(max(env_num("HYBRID_EMBED_WEIGHT", 0.6), 0.0), 1.0)
    return w * cos + (1 - w) * jaccard(topic, content_stems(text))


# ---------- models


def _not_blank(v: str) -> str:
    if not v.strip():
        raise ValueError("must not be empty")
    return v.strip()


class NewSource(BaseModel):
    kind: Literal["review", "search_query", "survey", "support", "sales_call", "comment"]
    source_ref: str = Field(max_length=300)
    text: str = Field(max_length=20000)
    weight: float = Field(default=1.0, gt=0, le=1000)

    @field_validator("source_ref", "text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        return _not_blank(v)


class MineRequest(BaseModel):
    min_sources: int = Field(default=2, ge=1, le=100)
    min_n: int = Field(default=2, ge=1, le=8)
    max_n: int = Field(default=5, ge=1, le=8)
    max_phrases: int = Field(default=300, ge=1, le=5000)
    ignore: list[str] = Field(default_factory=list, max_length=200)
    llm: bool = True
    batch_size: int = Field(default=30, ge=2, le=100)
    max_snippet_chars: int = Field(default=600, ge=80, le=4000)
    max_themes_per_batch: int = Field(default=8, ge=1, le=20)
    min_quotes: int = Field(default=2, ge=1, le=10)
    min_theme_sources: int = Field(default=2, ge=1, le=10)


class HeadlineRequest(BaseModel):
    topic: str = Field(max_length=500)
    channel: str = Field(default="any", max_length=40)
    n: int = Field(default=8, ge=1, le=20)
    k: int = Field(default=8, ge=1, le=20)
    phrases: list[str] | None = Field(default=None, max_length=20)
    facts: str = Field(default="", max_length=4000)
    max_chars: int = Field(default=140, ge=20, le=400)

    @field_validator("topic")
    @classmethod
    def not_blank(cls, v: str) -> str:
        return _not_blank(v)


class PersonaRequest(BaseModel):
    n: int = Field(default=3, ge=2, le=4)
    max_quotes_per_theme: int = Field(default=4, ge=1, le=10)


# ---------- endpoints: health, sources


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    with db() as conn:
        kinds = {r["kind"]: r["n"] for r in conn.execute("SELECT kind, COUNT(*) n FROM sources GROUP BY kind")}
        return {
            "sources": sum(kinds.values()), "sources_by_kind": kinds,
            "phrases": conn.execute("SELECT COUNT(*) FROM phrases").fetchone()[0],
            "themes": conn.execute("SELECT COUNT(*) FROM themes").fetchone()[0],
            "quotes": conn.execute("SELECT COUNT(*) FROM theme_quotes").fetchone()[0],
            "last_mine": get_meta(conn, "last_mine"),
        }


def upsert_sources(conn: sqlite3.Connection, items: list[NewSource], origin: str) -> dict:
    ts = now_utc()
    added = updated = unchanged = 0
    for s in items:
        row = conn.execute("SELECT id, text, weight FROM sources WHERE kind = ? AND source_ref = ?",
                           (s.kind, s.source_ref)).fetchone()
        if row is None:
            conn.execute("INSERT INTO sources (kind, source_ref, text, weight, origin, created_at, updated_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)", (s.kind, s.source_ref, s.text, s.weight, origin, ts, ts))
            added += 1
        elif row["text"] != s.text or row["weight"] != s.weight:
            conn.execute("UPDATE sources SET text = ?, weight = ?, updated_at = ? WHERE id = ?",
                         (s.text, s.weight, ts, row["id"]))
            updated += 1
        else:
            unchanged += 1
    return {"added": added, "updated": updated, "unchanged": unchanged}


@app.post("/sources/import", dependencies=[Depends(require_key)])
def import_sources(items: list[NewSource]):
    """Idempotent on (kind, source_ref): the same ref again updates the text if it changed."""
    if len(items) > MAX_IMPORT:
        raise HTTPException(422, f"at most {MAX_IMPORT} sources per import")
    with db() as conn, write(conn):
        return upsert_sources(conn, items, "import")


@app.get("/sources")
def list_sources(kind: Literal["review", "search_query", "survey", "support", "sales_call", "comment"] | None = None,
                 limit: int = Query(100, ge=1, le=5000)):
    sql, args = "SELECT * FROM sources", []
    if kind:
        sql += " WHERE kind = ?"
        args.append(kind)
    with db() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id LIMIT ?", (*args, limit))]


async def fetch_json(url: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=env_num("PULL_TIMEOUT", 30)) as client:
        r = await client.get(url, params=params, headers=key_headers())
    return r


@app.post("/sources/pull", dependencies=[Depends(require_key)])
async def pull_sources():
    """Pull reviews from 58 and search queries from 67 when their URLs are set. A source that
    is down, unset or not synced is reported and skipped; the other one is still imported."""
    report: dict[str, dict] = {}
    batches: list[tuple[str, list[NewSource]]] = []

    reviews_url = env("REVIEWS_URL").rstrip("/")
    if not reviews_url:
        report["reviews"] = {"ok": False, "skipped": "REVIEWS_URL is not set"}
    else:
        try:
            r = await fetch_json(f"{reviews_url}/reviews")
            if r.status_code != 200 or not isinstance(r.json(), list):
                report["reviews"] = {"ok": False, "error": f"{r.status_code}: {r.text[:200]}"}
            else:
                items = [NewSource(kind="review", source_ref=f"review-hub:{x['id']}", text=x["text"])
                         for x in r.json() if isinstance(x, dict) and str(x.get("text", "")).strip()]
                batches.append(("reviews", items))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            report["reviews"] = {"ok": False, "error": type(exc).__name__}

    gsc_url = env("GSC_URL").rstrip("/")
    if not gsc_url:
        report["search_queries"] = {"ok": False, "skipped": "GSC_URL is not set"}
    else:
        try:
            # Wide filter = the site's top queries by impressions, not only striking distance.
            r = await fetch_json(f"{gsc_url}/queries/opportunities", {
                "min_impressions": int(env_num("PULL_GSC_MIN_IMPRESSIONS", 10)),
                "min_position": 1, "max_position": 100, "limit": int(env_num("PULL_GSC_LIMIT", 500))})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code != 200 or not isinstance(body.get("queries"), list):
                report["search_queries"] = {"ok": False, "error": f"{r.status_code}: {r.text[:200]}"}
            else:
                items = [NewSource(kind="search_query", source_ref=f"gsc:{q['query']}", text=q["query"],
                                   weight=max(1.0, round(math.log10(max(1, q.get("impressions") or 1)), 3)))
                         for q in body["queries"] if isinstance(q, dict) and str(q.get("query", "")).strip()]
                batches.append(("search_queries", items))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            report["search_queries"] = {"ok": False, "error": type(exc).__name__}

    if batches:
        with db() as conn, write(conn):
            for name, items in batches:
                report[name] = {"ok": True, "fetched": len(items), **upsert_sources(conn, items, "pull")}
    return report


# ---------- endpoints: mining


@app.post("/mine", dependencies=[Depends(require_key)])
async def mine(req: MineRequest | None = None):
    """Phrases in code; themes from the LLM with every quote checked in code. Replaces the
    previous phrases, and the previous themes when the LLM step ran."""
    req = req or MineRequest()
    if req.min_n > req.max_n:
        raise HTTPException(422, "min_n is greater than max_n")
    with db() as conn:
        sources = [dict(r) for r in conn.execute("SELECT * FROM sources ORDER BY id")]
    if not sources:
        raise HTTPException(409, "no sources yet: POST /sources/import or /sources/pull first")
    ts = now_utc()
    phrases = mine_phrases(sources, req.min_n, req.max_n, req.min_sources, req.max_phrases, req.ignore)

    st = {"batches": 0, "llm_errors": [], "themes_proposed": 0, "themes_invalid": 0, "themes_dropped": [], "themes_merged": 0,
          "quotes_proposed": 0, "quotes_dropped": Counter()}
    themes: list[dict] | None = None
    if req.llm:
        by_id = {s["id"]: s for s in sources}
        checked: list[dict] = []
        ordered = mixed_order(sources)
        for i in range(0, len(ordered), req.batch_size):
            batch = ordered[i:i + req.batch_size]
            st["batches"] += 1
            try:
                out = await run_prompt("customer_themes", {
                    "snippets": snippet_block(batch, req.max_snippet_chars),
                    "max_themes": req.max_themes_per_batch})
            except HTTPException as exc:
                st["llm_errors"].append(f"batch {st['batches']}: {exc.detail}")
                continue
            proposed = out["output"].get("themes") or []
            st["themes_proposed"] += len(proposed)
            checked += check_themes(proposed, by_id, {s["id"] for s in batch}, st)
        if st["batches"] and len(st["llm_errors"]) == st["batches"]:
            themes = None  # every batch failed: keep the previous themes
        else:
            themes = merge_themes(checked, req.min_quotes, req.min_theme_sources, st)

    with db() as conn, write(conn):
        conn.execute("DELETE FROM phrases")
        for p in phrases:
            conn.execute("INSERT INTO phrases (phrase, normalized, words, sources, occurrences, weight,"
                         " source_ids, kinds, examples, mined_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (p["phrase"], p["normalized"], p["words"], p["sources"], p["occurrences"], p["weight"],
                          json.dumps(p["source_ids"]), json.dumps(p["kinds"]), json.dumps(p["examples"]), ts))
        if themes is not None:
            conn.execute("DELETE FROM theme_quotes")
            conn.execute("DELETE FROM themes")
            for t in themes:
                tid = conn.execute("INSERT INTO themes (name, kind, mined_at) VALUES (?, ?, ?)",
                                   (t["name"], t["kind"], ts)).lastrowid
                for q in t["quotes"]:
                    conn.execute("INSERT INTO theme_quotes (theme_id, source_id, quote) VALUES (?, ?, ?)",
                                 (tid, q["source_id"], q["quote"]))
        dropped = dict(st["quotes_dropped"])
        valid = sum(len(t["quotes"]) for t in themes or [])
        result = {
            "mined_at": ts, "sources": len(sources),
            "phrases": {"kept": len(phrases), "min_sources": req.min_sources, "n": [req.min_n, req.max_n]},
            "themes": None if not req.llm else {
                "ran": themes is not None, "batches": st["batches"], "llm_errors": st["llm_errors"],
                "proposed": st["themes_proposed"], "invalid": st["themes_invalid"],
                "kept": len(themes or []), "merged": st["themes_merged"], "dropped": st["themes_dropped"],
                "quotes_proposed": st["quotes_proposed"],
                "quotes_valid_in_kept_themes": valid,
                "quotes_dropped": dropped, "quotes_dropped_total": sum(dropped.values()),
            },
        }
        set_meta(conn, "last_mine", result)
    return result


# ---------- endpoints: read


def load_themes(conn: sqlite3.Connection, kind: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM themes", []
    if kind:
        sql += " WHERE kind = ?"
        args.append(kind)
    themes = []
    for t in conn.execute(sql + " ORDER BY id", args):
        quotes = [{"id": f"q{q['id']}", "source_id": q["source_id"], "source_kind": q["kind"],
                   "source_ref": q["source_ref"], "quote": q["quote"]}
                  for q in conn.execute(
                      "SELECT tq.id, tq.source_id, tq.quote, s.kind, s.source_ref FROM theme_quotes tq"
                      " JOIN sources s ON s.id = tq.source_id WHERE tq.theme_id = ? ORDER BY tq.id", (t["id"],))]
        themes.append({"id": f"t{t['id']}", "name": t["name"], "kind": t["kind"],
                       "sources": len({q["source_id"] for q in quotes}), "quotes": quotes,
                       "mined_at": t["mined_at"]})
    return themes


@app.get("/themes")
def get_themes(kind: Literal["pain", "desire", "objection", "trigger", "outcome", "word_choice"] | None = None):
    with db() as conn:
        themes = load_themes(conn, kind)
    return sorted(themes, key=lambda t: (-t["sources"], t["id"]))


def row_to_phrase(r: sqlite3.Row) -> dict:
    return {"id": f"p{r['id']}", "phrase": r["phrase"], "words": r["words"], "sources": r["sources"],
            "occurrences": r["occurrences"], "weight": r["weight"], "source_ids": json.loads(r["source_ids"]),
            "kinds": json.loads(r["kinds"]), "examples": json.loads(r["examples"])}


@app.get("/phrases")
def get_phrases(min_sources: int = Query(2, ge=1), kind: str | None = None,
                limit: int = Query(100, ge=1, le=5000)):
    with db() as conn:
        rows = [row_to_phrase(r) for r in conn.execute(
            "SELECT * FROM phrases WHERE sources >= ? ORDER BY sources DESC, weight DESC, words DESC, id",
            (min_sources,))]
    if kind:
        rows = [r for r in rows if kind in r["kinds"]]
    return rows[:limit]


def candidates(conn: sqlite3.Connection, kinds: set[str], max_quote_words: int) -> list[dict]:
    out, seen = [], set()
    if "phrase" in kinds:
        for r in conn.execute("SELECT * FROM phrases ORDER BY sources DESC, weight DESC, id"):
            p = row_to_phrase(r)
            seen.add(normalize(p["phrase"]))
            out.append({"id": p["id"], "type": "phrase", "text": p["phrase"], "sources": p["sources"],
                        "source_ids": p["source_ids"], "theme": None, "theme_kind": None})
    if "quote" in kinds:
        for r in conn.execute("SELECT tq.id, tq.source_id, tq.quote, t.name, t.kind FROM theme_quotes tq"
                              " JOIN themes t ON t.id = tq.theme_id ORDER BY tq.id"):
            n = normalize(r["quote"])
            if n in seen or len(n.split()) > max_quote_words:
                continue
            seen.add(n)
            out.append({"id": f"q{r['id']}", "type": "quote", "text": " ".join(r["quote"].split()), "sources": 1,
                        "source_ids": [r["source_id"]], "theme": r["name"], "theme_kind": r["kind"]})
    return out


async def relevant_items(topic: str, k: int, kinds: set[str], max_quote_words: int = 12) -> dict:
    with db() as conn:
        cands = candidates(conn, kinds, max_quote_words)
    method = "none"
    if cands:
        vecs = await embeddings_for([topic] + [c["text"] for c in cands])
        if vecs is not None:
            method = "hybrid"
            q, t = vecs[topic], content_stems(topic)
            for c in cands:
                cos = cosine(q, vecs[c["text"]])
                c["embedding_score"] = round(cos, 4)
                c["keyword_jaccard"] = round(jaccard(t, content_stems(c["text"])), 4)
                c["score"] = round(hybrid_score(cos, t, c["text"]), 4)
        else:
            method = "keyword"
            t = content_stems(topic)
            for c in cands:
                c["score"] = round(keyword_score(t, c["text"]), 4)
            cands = [c for c in cands if c["score"] > 0]
    cands.sort(key=lambda c: (-c["score"], -c["sources"], c["id"]))
    items = cands[:k]
    return {"topic": topic, "method": method, "embed_model": embed_model() if method == "hybrid" else None,
            "items": items, "customer_phrases": "\n".join(f"- {c['text']}" for c in items)}


@app.get("/relevant")
async def relevant(topic: str = Query(min_length=1, max_length=500), k: int = Query(8, ge=1, le=50),
                   kinds: str = Query("phrase,quote", description="phrase, quote or both, comma-separated"),
                   max_quote_words: int = Query(12, ge=2, le=60)):
    want = {x.strip() for x in kinds.split(",") if x.strip()}
    if not want or not want <= {"phrase", "quote"}:
        raise HTTPException(422, "kinds must be phrase, quote or phrase,quote")
    return await relevant_items(topic.strip(), k, want, max_quote_words)


# ---------- endpoints: headline bank


def check_headlines(proposed: list, offered: list[dict], allowed_numbers: set[str], max_chars: int) -> tuple[list, list]:
    kept, dropped, seen = [], [], set()
    for h in proposed or []:
        text = " ".join(str(h.get("text", "") if isinstance(h, dict) else h).split())
        if not text:
            continue
        if normalize(text) in seen:
            dropped.append({"text": text, "reason": "duplicate"})
            continue
        seen.add(normalize(text))
        used = [p for p in offered if contains_phrase(text, p["text"])]
        if not used:
            dropped.append({"text": text, "reason": "does not contain any given customer phrase verbatim"})
            continue
        extra = numbers_in(text) - allowed_numbers
        if extra:
            dropped.append({"text": text, "reason": f"number(s) not in the phrases or facts: {sorted(extra)}"})
            continue
        if any(c in text for c in QUOTE_CHARS):
            dropped.append({"text": text, "reason": "quotation marks: customer words must not be presented as a quote"})
            continue
        if len(text) > max_chars:
            dropped.append({"text": text, "reason": f"{len(text)} chars, max {max_chars}"})
            continue
        p = max(used, key=lambda p: len(p["text"]))
        kept.append({"text": text, "phrase": p["text"], "phrase_id": p["id"], "phrase_type": p["type"],
                     "source_ids": p["source_ids"], "sources": p["sources"]})
    return kept, dropped


@app.post("/headlines", dependencies=[Depends(require_key)])
async def headlines(req: HeadlineRequest):
    if req.phrases:
        with db() as conn:
            bank = candidates(conn, {"phrase", "quote"}, 60)
        offered = []
        for i, ph in enumerate(req.phrases, 1):
            match = next((c for c in bank if normalize(c["text"]) == normalize(ph)), None)
            if match is None:
                raise HTTPException(422, f"not a mined phrase or quote: {ph!r} (GET /phrases, /themes)")
            offered.append(match)
    else:
        offered = (await relevant_items(req.topic, req.k, {"phrase", "quote"}))["items"]
    if not offered:
        raise HTTPException(409, "no customer phrases yet: import sources and POST /mine first")
    block = "\n".join(f"[p{i}] {c['text']}" for i, c in enumerate(offered, 1))
    out = await run_prompt("customer_headlines", {"topic": req.topic, "channel": req.channel, "n": req.n,
                                                  "phrases": block, "facts": req.facts})
    allowed = numbers_in(" ".join(c["text"] for c in offered) + " " + req.facts + " " + req.topic)
    kept, dropped = check_headlines(out["output"].get("headlines") or [], offered, allowed, req.max_chars)
    return {"topic": req.topic, "channel": req.channel, "model": out.get("model"),
            "phrases_offered": [{"label": f"p{i}", **c} for i, c in enumerate(offered, 1)],
            "headlines": kept, "dropped": dropped}


# ---------- endpoints: personas


ATTRS = ("goals", "pains", "objections", "words_they_use")
AGE_RE = re.compile(r"\b(\d{1,3}\s*(?:-|\s)?(?:year|yr)s?[\s-]*old|aged?\s+\d+|in (?:her|his|their) \d0s|\d0s)\b", re.I)


def themes_block(themes: list[dict], max_quotes: int) -> str:
    lines = []
    for t in themes:
        lines.append(f"[{t['id']}] {t['name']} ({t['kind']}, {t['sources']} customers)")
        for q in t["quotes"][:max_quotes]:
            lines.append(f"    [{q['id']}] {' '.join(q['quote'].split())}")
    return "\n".join(lines)


def check_personas(proposed: list, themes: list[dict], source_text: str) -> tuple[list, dict]:
    theme_by = {t["id"]: t for t in themes}
    quote_by = {q["id"]: q for t in themes for q in t["quotes"]}
    known_words = set(WORD_RE.findall(normalize(source_text + " " + " ".join(t["name"] for t in themes))))
    report = {"attributes_dropped": [], "personas_dropped": [], "labels_replaced": []}
    kept = []
    for idx, p in enumerate(proposed or [], 1):
        if not isinstance(p, dict):
            continue
        persona = {"label": "", "attributes": {a: [] for a in ATTRS}}
        for attr in ATTRS:
            for item in p.get(attr) or []:
                if not isinstance(item, dict):
                    continue
                text = " ".join(str(item.get("text", "")).split())
                cites = [str(c).strip().strip("[]").lower() for c in item.get("cites") or []]
                valid = [c for c in cites if c in theme_by or c in quote_by]
                evidence = []
                for c in valid:
                    evidence += [quote_by[c]] if c in quote_by else theme_by[c]["quotes"][:2]
                reason = None
                if not text:
                    reason = "empty"
                elif not valid:
                    reason = f"no valid citation (cited {cites or 'nothing'})"
                elif attr == "words_they_use":
                    span = next((s for s in (find_verbatim(text, q["quote"]) for q in evidence) if s), None)
                    if span is None:
                        reason = "words_they_use is not a verbatim part of a cited quote"
                    else:
                        text = span
                if reason is None and numbers_in(text) - numbers_in(" ".join(q["quote"] for q in evidence)):
                    reason = "number not in the cited quotes"
                if reason is None and AGE_RE.search(text) and not AGE_RE.search(source_text):
                    reason = "age or demographic not in the sources"
                if reason:
                    report["attributes_dropped"].append({"persona": idx, "attribute": attr, "text": text,
                                                         "reason": reason})
                    continue
                uniq = {q["id"]: q for q in evidence}
                persona["attributes"][attr].append({
                    "text": text, "cites": valid,
                    "evidence": [{"quote_id": q["id"], "source_id": q["source_id"], "quote": q["quote"]}
                                 for q in list(uniq.values())[:4]]})
        label = " ".join(str(p.get("label", "")).split())[:80]
        caps = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", label)[1:] if w[0].isupper()]
        bad = None
        if not label:
            bad = "empty"
        elif NUMBER_RE.search(label) and numbers_in(label) - numbers_in(source_text):
            bad = "number not in the sources"
        elif AGE_RE.search(label):
            bad = "age in label"
        elif any(normalize(w).strip("'’") not in known_words for w in caps):
            bad = "name or proper noun not in the sources"
        if bad:
            report["labels_replaced"].append({"persona": idx, "label": label, "reason": bad})
            label = f"Persona {idx}"
        persona["label"] = label
        n_attrs = sum(len(v) for v in persona["attributes"].values())
        if n_attrs < 2:
            report["personas_dropped"].append({"persona": idx, "label": label,
                                               "reason": f"{n_attrs} grounded attribute(s), need 2"})
            continue
        srcs = {e["source_id"] for v in persona["attributes"].values() for a in v for e in a["evidence"]}
        persona["built_from_sources"] = len(srcs)
        kept.append(persona)
    return kept, report


@app.post("/personas", dependencies=[Depends(require_key)])
async def personas(req: PersonaRequest | None = None):
    req = req or PersonaRequest()
    with db() as conn:
        themes = load_themes(conn)
        src_ids = {q["source_id"] for t in themes for q in t["quotes"]}
        source_text = " ".join(r["text"] for r in conn.execute(
            f"SELECT text FROM sources WHERE id IN ({','.join('?' * len(src_ids))})", tuple(src_ids))) if src_ids else ""
    if len(themes) < 2:
        raise HTTPException(409, f"need at least 2 themes to build personas, have {len(themes)}: POST /mine first")
    out = await run_prompt("customer_personas", {"themes": themes_block(themes, req.max_quotes_per_theme),
                                                 "n": req.n})
    kept, report = check_personas(out["output"].get("personas") or [], themes, source_text)
    body = {
        "built_from": {"sources": len(src_ids), "themes": len(themes),
                       "quotes": sum(len(t["quotes"]) for t in themes)},
        "note": (f"Built only from {len(src_ids)} real customer sources ({len(themes)} themes). "
                 "Every attribute cites them; no ages, names or demographics were added."),
        "model": out.get("model"), "personas": kept, **report,
    }
    with db() as conn, write(conn):
        body["created_at"] = now_utc()
        body["id"] = conn.execute("INSERT INTO personas (created_at, body) VALUES (?, '{}')",
                                  (body["created_at"],)).lastrowid
        conn.execute("UPDATE personas SET body = ? WHERE id = ?", (json.dumps(body), body["id"]))
    return body


@app.get("/personas")
def last_personas():
    with db() as conn:
        row = conn.execute("SELECT body FROM personas ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        raise HTTPException(404, "no personas yet: POST /personas")
    return json.loads(row["body"])
