"""AI visibility (GEO) tracker: is our brand mentioned, cited and described correctly when
buyers ask AI assistants, compared with our competitors?

A fixed, versioned set of buyer questions is asked to every enabled provider (official APIs
only: OpenAI web search, Perplexity Sonar, Gemini grounding, Groq model knowledge), N samples
each. Mentions, citations, list positions and share of voice are measured in code; sentences
about our brand are checked against our approved facts by the claim checker (44).

It never scrapes chatgpt.com, perplexity.ai or Google AI Overviews: their terms forbid it, and
an API answer is a proxy for what a person sees in the app, not the same thing.
"""
import hmac
import json
import logging
import os
import re
import sqlite3
import statistics
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import analysis as an
from . import providers as pv

app = FastAPI(title="ai-visibility")
log = logging.getLogger("ai-visibility")

KINDS = ("category", "comparison", "problem", "branded")
SCHEMA = """
CREATE TABLE IF NOT EXISTS question_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    parent_id INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    approved_by TEXT
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id INTEGER NOT NULL REFERENCES question_sets (id) ON DELETE CASCADE,
    pos INTEGER NOT NULL,
    text TEXT NOT NULL,
    kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS questions_set ON questions (set_id, pos);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id INTEGER NOT NULL REFERENCES question_sets (id),
    set_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    providers TEXT NOT NULL,
    samples INTEGER NOT NULL,
    question_ids TEXT,
    entities TEXT NOT NULL,
    notes TEXT,
    error TEXT,
    stats TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    sample INTEGER NOT NULL,
    model TEXT,
    text TEXT,
    citations TEXT,
    urls_in_text TEXT,
    analysis TEXT,
    error TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS answers_run ON answers (run_id, question_id, provider);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    answer_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    sentence TEXT NOT NULL,
    kind TEXT NOT NULL,
    reasons TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claims_run ON claims (run_id);
CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL,
    provider TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    searches INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, provider)
);
"""
_migrate_lock = threading.Lock()
_migrated: set[str] = set()
_run_lock = threading.Lock()  # one run at a time: caps and costs stay predictable

# "I don't know this brand" answers are counted, not sent to the claim checker as claims.
UNKNOWN_RE = re.compile(
    r"\b(?:not (?:aware of|familiar with)|(?:do not|don't|couldn't|could not|can't|cannot) (?:find|locate|verify)"
    r"|(?:do not|don't) have (?:any )?(?:specific |reliable |detailed |up-to-date |public )?(?:information|details|data)"
    r"|no (?:specific |reliable |public |widely available )?information|(?:does not|doesn't) appear to be"
    r"|unable to (?:find|verify|confirm)|not a (?:widely )?(?:known|recognized|recognised))\b", re.I)


# ---------- time and env


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    return fmt(datetime.now(timezone.utc))


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def to_utc_iso(value: str) -> str:
    v = value.strip()
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00")) if "T" in v else datetime.fromisoformat(v + "T00:00:00")
    except ValueError:
        raise HTTPException(422, f"not an ISO 8601 date or date/time: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return fmt(dt)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def env_bool(name: str, default: bool) -> bool:
    v = env(name)
    return default if not v else v.lower() not in ("false", "0", "no", "off")


def env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(env(name, str(default)))))
    except ValueError:
        return default


def key_headers() -> dict:
    k = env("INTERNAL_API_KEY")
    return {"X-API-Key": k} if k else {}


def redact(text: str | None) -> str | None:
    """Last line of defence: no provider key ever reaches the database or a response."""
    if not text:
        return text
    for name in pv.NAMES:
        k = pv.key_of(name)
        if k and len(k) >= 6:
            text = text.replace(k, "[redacted]")
    k = env("INTERNAL_API_KEY")
    return text.replace(k, "[redacted]") if k and len(k) >= 6 else text


# ---------- storage


def db_path() -> str:
    return env("DB_PATH", "/data/visibility.sqlite")


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
            # A run that was running when the service stopped will never finish.
            conn.execute("UPDATE runs SET status = 'failed', error = 'interrupted (service restarted)',"
                         " finished_at = ? WHERE status = 'running'", (now_utc(),))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        _migrated.add(path)


@contextmanager
def db():
    conn = sqlite3.connect(db_path(), timeout=30, isolation_level=None)
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


def loads(v):
    return json.loads(v) if v else None


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = env("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- other services


class UpstreamError(Exception):
    pass


def brand_profile() -> dict:
    """05 /profile. Without it, BRAND_NAME (+ BRAND_ALIASES, OWN_DOMAINS) is enough to run."""
    base = env("BRAND_URL", "http://brand-service:8000").rstrip("/")
    try:
        r = httpx.get(f"{base}/profile", timeout=15)
        if r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("name"):
            return r.json()
        why = f"HTTP {r.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        why = type(exc).__name__
    if env("BRAND_NAME"):
        return {"name": env("BRAND_NAME")}
    raise UpstreamError(f"brand service (05) unavailable ({why}) and BRAND_NAME is not set")


def brand_facts() -> list[str]:
    base = env("BRAND_URL", "http://brand-service:8000").rstrip("/")
    try:
        r = httpx.get(f"{base}/facts", timeout=15)
        return [f["text"] for f in r.json().get("facts", [])] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return []


def split_list(value: str) -> list[str]:
    return [v.strip() for v in re.split(r"[,;\n]", value or "") if v.strip()]


def competitor_aliases() -> dict[str, list[str]]:
    """COMPETITOR_ALIASES="Atlas Coffee Club=Atlas|atlascoffee; Trade=Trade Coffee"."""
    out = {}
    for part in (env("COMPETITOR_ALIASES")).split(";"):
        name, _, al = part.partition("=")
        if name.strip() and al.strip():
            out[name.strip().casefold()] = [a.strip() for a in al.split("|") if a.strip()]
    return out


def competitors_from_registry() -> tuple[list[dict], str | None]:
    """Active competitors from 78 (name, website, domains). Returns (list, note)."""
    base = env("AD_LIBRARY_URL").rstrip("/")
    if not base:
        return [], "AD_LIBRARY_URL is not set: no competitors, so no share of voice"
    try:
        r = httpx.get(f"{base}/competitors", params={"status": "active"}, timeout=15)
        if r.status_code != 200 or not isinstance(r.json(), list):
            return [], f"competitor registry (78) returned HTTP {r.status_code}"
        return r.json(), None
    except (httpx.HTTPError, ValueError) as exc:
        return [], f"competitor registry (78) unreachable ({type(exc).__name__})"


def build_entities() -> dict:
    """Snapshot of what counts as us and as each competitor, stored with every run."""
    b = brand_profile()
    aliases = [str(a) for a in b.get("aliases") or []] + split_list(env("BRAND_ALIASES"))
    products = []
    for p in b.get("products") or []:
        if isinstance(p, dict) and p.get("name"):
            products.append(str(p["name"]))
            products += [str(a) for a in p.get("aliases") or [] if len(str(a)) > 3]
    if env_bool("COUNT_PRODUCT_NAMES", False):
        aliases += [str(p["name"]) for p in b.get("products") or [] if isinstance(p, dict) and p.get("name")]
    domains = [b.get("website") or ""] + [str(d) for d in b.get("allowed_domains") or []] + split_list(env("OWN_DOMAINS"))
    brand = an.entity(b["name"], aliases, domains, kind="brand")
    comps, note = competitors_from_registry()
    extra = competitor_aliases()
    ents = []
    for c in comps:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        doms = [c.get("website") or ""] + list(c.get("domains") or [])
        ents.append(an.entity(c["name"], extra.get(c["name"].casefold(), []), doms))
    return {"brand": brand, "products": products, "competitors": ents,
            "notes": [note] if note else []}


def gateway(prompt: str, variables: dict) -> dict:
    base = env("GATEWAY_URL", "http://llm-gateway:8000").rstrip("/")
    try:
        r = httpx.post(f"{base}/v1/run", json={"prompt": prompt, "vars": variables}, headers=key_headers(),
                       timeout=float(env("GATEWAY_TIMEOUT", "300")))
    except httpx.HTTPError as exc:
        raise UpstreamError(f"gateway unreachable ({type(exc).__name__})") from None
    if r.status_code != 200:
        raise UpstreamError(f"gateway returned HTTP {r.status_code} for prompt {prompt}")
    out = r.json().get("output")
    if not isinstance(out, dict):
        raise UpstreamError(f"gateway returned no JSON object for prompt {prompt}")
    return out


def claim_check(text: str) -> dict:
    base = env("CLAIMS_URL").rstrip("/")
    try:
        r = httpx.post(f"{base}/verify", json={"text": text}, headers=key_headers(),
                       timeout=float(env("CLAIMS_TIMEOUT", "600")))
    except httpx.HTTPError as exc:
        raise UpstreamError(f"claim checker unreachable ({type(exc).__name__})") from None
    if r.status_code != 200:
        raise UpstreamError(f"claim checker returned HTTP {r.status_code}")
    return r.json()


# ---------- question sets


def norm_q(text: str) -> str:
    return re.sub(r"[^\w ]+", "", " ".join(an.clean(text).split()).casefold()).strip()


def set_or_404(conn, set_id: int) -> dict:
    row = conn.execute("SELECT * FROM question_sets WHERE id = ?", (set_id,)).fetchone() if 0 < set_id < 2**63 else None
    if row is None:
        raise HTTPException(404, f"question set {set_id} not found")
    s = dict(row)
    s["questions"] = [dict(q) for q in conn.execute(
        "SELECT id, pos, text, kind FROM questions WHERE set_id = ? ORDER BY pos", (set_id,))]
    for q in s["questions"]:
        q["branded"] = q["kind"] == "branded"
    return s


def active_set(conn) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM question_sets WHERE status = 'approved' ORDER BY approved_at DESC, id DESC"
                        " LIMIT 1").fetchone()


def classify(questions: list[dict], ents: dict) -> tuple[list[dict], list[dict]]:
    """Keep unaided questions unaided: a question naming our brand becomes `branded`; one
    naming a competitor is dropped (it would measure the competitor's brand, not ours);
    duplicates are dropped. Returns (kept, dropped with reasons)."""
    kept, dropped, seen = [], [], set()
    for q in questions:
        text = " ".join(str(q.get("text") or "").split())
        kind = q.get("kind") if q.get("kind") in KINDS else "category"
        if len(text) < 10:
            dropped.append({"text": text, "why": "too short"})
            continue
        key = norm_q(text)
        if key in seen:
            dropped.append({"text": text, "why": "duplicate"})
            continue
        named = [c["name"] for c in ents["competitors"] if an.mentions(text, c)]
        if named:
            dropped.append({"text": text, "why": f"names a competitor ({', '.join(named)})"})
            continue
        ours = an.mentions(text, ents["brand"])
        if ours:
            kind = "branded"
        elif kind == "branded":
            kind = "category"  # a "branded" question that does not name us is unaided
        seen.add(key)
        kept.append({"text": text[:300], "kind": kind})
    return kept, dropped


def insert_set(conn, questions: list[dict], source: str, notes: str | None, parent_id: int | None = None) -> int:
    version = (conn.execute("SELECT COALESCE(MAX(version), 0) FROM question_sets").fetchone()[0]) + 1
    cur = conn.execute("INSERT INTO question_sets (version, status, source, parent_id, notes, created_at)"
                       " VALUES (?, 'draft', ?, ?, ?, ?)", (version, source, parent_id, notes, now_utc()))
    sid = cur.lastrowid
    conn.executemany("INSERT INTO questions (set_id, pos, text, kind) VALUES (?, ?, ?, ?)",
                     [(sid, i + 1, q["text"], q["kind"]) for i, q in enumerate(questions)])
    return sid


class QuestionIn(BaseModel):
    text: str = Field(min_length=10, max_length=300)
    kind: Literal["category", "comparison", "problem", "branded"] | None = None


class SetIn(BaseModel):
    questions: list[QuestionIn] = Field(min_length=1, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)


class GenerateIn(BaseModel):
    count: int = Field(default=20, ge=15, le=30)
    branded: int = Field(default=3, ge=0, le=6)
    market: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class ApproveIn(BaseModel):
    approved_by: str | None = Field(default=None, max_length=200)


# ---------- runs


class RunIn(BaseModel):
    set_id: int | None = None
    providers: list[str] | None = None
    samples: int | None = Field(default=None, ge=1, le=10)
    question_ids: list[int] | None = Field(default=None, max_length=60)
    check_accuracy: bool = True

    @field_validator("providers")
    @classmethod
    def known(cls, v):
        if v is not None:
            bad = [p for p in v if p not in pv.NAMES]
            if bad:
                raise ValueError(f"unknown provider(s) {bad}; known: {list(pv.NAMES)}")
        return v


def reserve_call(provider: str) -> str | None:
    """Count one call against today's caps before making it. Returns why not, or None."""
    cap = pv.caps(provider)
    with db() as conn, write(conn):
        conn.execute("INSERT OR IGNORE INTO usage (day, provider) VALUES (?, ?)", (today(), provider))
        u = conn.execute("SELECT * FROM usage WHERE day = ? AND provider = ?", (today(), provider)).fetchone()
        if u["calls"] >= cap["calls"]:
            return f"daily call cap reached ({cap['calls']})"
        if cap["tokens"] and u["input_tokens"] + u["output_tokens"] >= cap["tokens"]:
            return f"daily token cap reached ({cap['tokens']})"
        if cap["usd"] and u["cost_usd"] >= cap["usd"]:
            return f"daily cost cap reached (${cap['usd']})"
        conn.execute("UPDATE usage SET calls = calls + 1 WHERE day = ? AND provider = ?", (today(), provider))
    return None


def record_usage(provider: str, a: pv.Answer | None, cost: float) -> None:
    with db() as conn, write(conn):
        if a is None:
            conn.execute("UPDATE usage SET errors = errors + 1 WHERE day = ? AND provider = ?", (today(), provider))
        else:
            conn.execute("UPDATE usage SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ?,"
                         " searches = searches + ?, cost_usd = cost_usd + ? WHERE day = ? AND provider = ?",
                         (a.input_tokens, a.output_tokens, a.searches, cost, today(), provider))


def store_answer(run_id: int, q: dict, provider: str, sample: int, **fields) -> None:
    cols = {"run_id": run_id, "question_id": q["id"], "provider": provider, "sample": sample,
            "created_at": now_utc(), **fields}
    for k in ("citations", "urls_in_text", "analysis"):
        if k in cols and cols[k] is not None:
            cols[k] = json.dumps(cols[k])
    if cols.get("error"):
        cols["error"] = redact(cols["error"])
    with db() as conn, write(conn):
        conn.execute(f"INSERT INTO answers ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     tuple(cols.values()))


def ask_all(run_id: int, provider: str, questions: list[dict], samples: int, ents: dict) -> None:
    """One provider, every question × sample, in order. After a cap is hit the rest are
    stored as skipped, so the run shows exactly what was not asked."""
    delay = max(0.0, float(env("VIS_DELAY_S", "1")))
    stop = None
    with httpx.Client() as client:
        for q in questions:
            for s in range(1, samples + 1):
                if stop is None:
                    stop = reserve_call(provider)
                if stop:
                    store_answer(run_id, q, provider, s, error=f"skipped: {stop}")
                    continue
                try:
                    a = pv.ask(provider, q["text"], client)
                except pv.ProviderError as exc:
                    record_usage(provider, None, 0)
                    store_answer(run_id, q, provider, s, model=pv.model_of(provider), error=str(exc))
                    log.warning("run %s: %s question %s failed: %s", run_id, provider, q["id"], exc)
                    time.sleep(delay)
                    continue
                c = pv.cost(provider, a)
                record_usage(provider, a, c)
                res = an.analyse(a.text, a.citations, ents["brand"], ents["competitors"])
                res["says_unknown"] = res["brand_mentioned"] and bool(UNKNOWN_RE.search(a.text))
                store_answer(run_id, q, provider, s, model=a.model, text=a.text[:20000], citations=a.citations,
                             urls_in_text=an.urls_in(a.text) if not pv.PROVIDERS[provider]["web_search"] else None,
                             analysis=res, input_tokens=a.input_tokens, output_tokens=a.output_tokens, cost_usd=c)
                time.sleep(delay)


def check_accuracy(run_id: int, ents: dict) -> dict:
    """Every sentence about us goes to the claim checker (44) against our approved facts.
    "I don't know this brand" sentences are counted, not checked."""
    if not env("CLAIMS_URL"):
        return {"checked": 0, "skipped": "CLAIMS_URL is not set"}
    limit = env_int("VIS_MAX_CLAIM_CHECKS", 60, 0, 1000)
    cache: dict[str, dict] = {}
    checked = wrong = errors = 0
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, question_id, provider, text, analysis FROM answers WHERE run_id = ? AND error IS NULL", (run_id,))]
    for r in rows:
        res = loads(r["analysis"]) or {}
        if not res.get("brand_mentioned"):
            continue
        for sent in an.sentences_about(r["text"], ents["brand"], ents["products"])[:12]:
            if UNKNOWN_RE.search(sent) or sent.rstrip().endswith("?"):
                continue
            key = norm_q(sent)
            if key not in cache:
                if len(cache) >= limit:
                    continue
                try:
                    cache[key] = claim_check(sent)
                except UpstreamError as exc:
                    errors += 1
                    cache[key] = {"error": str(exc)}
                checked += 1
            v = cache[key]
            if v.get("error") or v.get("ok", True):
                continue
            bad_nums = [f"the number {n['value']} is not in the approved facts" for n in v.get("numbers") or []
                        if not n.get("supported")]
            reasons = bad_nums + [x for c in v.get("claims") or [] if not c.get("supported")
                                  for x in (c.get("reasons") or [f"not supported: {c.get('claim')}"])]
            reasons = list(dict.fromkeys(reasons))[:8] or list(v.get("unsupported") or [])[:8]
            with db() as conn, write(conn):
                conn.execute("INSERT INTO claims (run_id, answer_id, question_id, provider, sentence, kind, reasons,"
                             " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (run_id, r["id"], r["question_id"], r["provider"], sent[:1000],
                              "number" if bad_nums else "unsupported", json.dumps(reasons), now_utc()))
            wrong += 1
    return {"checked": checked, "wrong": wrong, "errors": errors,
            "limit_hit": len(cache) >= limit and limit > 0}


def execute(run_id: int) -> None:
    with _run_lock:
        try:
            with db() as conn:
                run = dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())
                ents = loads(run["entities"])
                ids = loads(run["question_ids"])
                qs = [dict(q) for q in conn.execute("SELECT id, text, kind FROM questions WHERE set_id = ? ORDER BY pos",
                                                     (run["set_id"],))]
            if ids:
                qs = [q for q in qs if q["id"] in ids]
            threads = [threading.Thread(target=ask_all, args=(run_id, p, qs, run["samples"], ents), daemon=True)
                       for p in loads(run["providers"])]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            acc = check_accuracy(run_id, ents) if loads(run["notes"]).get("check_accuracy", True) else {"skipped": "off"}
            stats = summarize(run_id, accuracy=acc)
            with db() as conn, write(conn):
                conn.execute("UPDATE runs SET status = ?, stats = ?, finished_at = ? WHERE id = ?",
                             ("done", json.dumps(stats), now_utc(), run_id))
        except Exception as exc:  # recorded, never raised from a background thread
            log.exception("run %s failed", run_id)
            with db() as conn, write(conn):
                conn.execute("UPDATE runs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                             (redact(f"{type(exc).__name__}: {exc}")[:500], now_utc(), run_id))


# ---------- summaries


def run_or_404(conn, run_id: int) -> dict:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone() if 0 < run_id < 2**63 else None
    if row is None:
        raise HTTPException(404, f"run {run_id} not found")
    return run_view(row)


def run_view(row) -> dict:
    r = dict(row)
    for k in ("providers", "question_ids", "entities", "notes", "stats"):
        r[k] = loads(r[k])
    return r


def latest_run(conn, since: str | None = None) -> dict | None:
    q = "SELECT * FROM runs WHERE status = 'done'"
    args: tuple = ()
    if since:
        q += " AND finished_at >= ?"
        args = (since,)
    row = conn.execute(q + " ORDER BY id DESC LIMIT 1", args).fetchone()
    return run_view(row) if row else None


def answers_of(conn, run_id: int) -> list[dict]:
    out = []
    for r in conn.execute("SELECT a.*, q.kind, q.text AS question FROM answers a JOIN questions q ON q.id = a.question_id"
                          " WHERE a.run_id = ? ORDER BY a.question_id, a.provider, a.sample", (run_id,)):
        a = dict(r)
        for k in ("citations", "urls_in_text", "analysis"):
            a[k] = loads(a[k])
        out.append(a)
    return out


def _avg(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def provider_stats(rows: list[dict], ents: dict, provider: str, wrong: int) -> dict:
    web = pv.PROVIDERS[provider]["web_search"]
    ok = [a for a in rows if not a["error"]]
    unaided = [a for a in ok if a["kind"] != "branded"]
    branded = [a for a in ok if a["kind"] == "branded"]
    bname = ents["brand"]["name"]
    k = sum(1 for a in unaided if a["analysis"]["brand_mentioned"])
    qids = sorted({a["question_id"] for a in unaided})
    any_q = sum(1 for qid in qids if any(a["analysis"]["brand_mentioned"] for a in unaided if a["question_id"] == qid))
    positions = [a["analysis"]["brand_position"] for a in unaided if a["analysis"]["brand_position"]]
    counts = {bname: k}
    comps = {}
    for c in ents["competitors"]:
        n = c["name"]
        m = sum(1 for a in unaided if a["analysis"]["competitors"].get(n, {}).get("mentioned"))
        counts[n] = m
        cpos = [a["analysis"]["competitors"][n]["position"] for a in unaided
                if a["analysis"]["competitors"].get(n, {}).get("position")]
        comps[n] = {"mention": an.rate(m, len(unaided)),
                    "citation": an.rate(sum(1 for a in unaided if a["analysis"]["competitors"].get(n, {}).get("cited")),
                                        len(unaided)) if web else None,
                    "avg_position": _avg(cpos)}
    total = sum(counts.values())
    sov = {n: {"mentions": v, "share": round(v / total, 4) if total else None} for n, v in counts.items()}
    return {
        "provider": provider, "label": pv.PROVIDERS[provider]["label"], "web_search": web,
        "answers": len(ok), "errors": sum(1 for a in rows if a["error"] and not a["error"].startswith("skipped")),
        "skipped": sum(1 for a in rows if a["error"] and a["error"].startswith("skipped")),
        "unaided_answers": len(unaided),
        "mention": an.rate(k, len(unaided)),
        "mention_by_question": an.rate(any_q, len(qids)),
        "citation": an.rate(sum(1 for a in unaided if a["analysis"]["brand_cited"]), len(unaided)) if web else None,
        "listed": an.rate(len(positions), len(unaided)),
        "avg_position": _avg(positions),
        "median_position": statistics.median(positions) if positions else None,
        "positions": {str(p): positions.count(p) for p in sorted(set(positions))},
        "share_of_voice": sov,
        "competitors": comps,
        "branded": {"answers": len(branded),
                    "knows_us": an.rate(sum(1 for a in branded if a["analysis"]["brand_mentioned"]
                                            and not a["analysis"].get("says_unknown")), len(branded)),
                    "says_unknown": sum(1 for a in branded if a["analysis"].get("says_unknown")),
                    "wrong_claims": wrong},
        "tokens": {"input": sum(a["input_tokens"] for a in rows), "output": sum(a["output_tokens"] for a in rows)},
        "cost_usd": round(sum(a["cost_usd"] for a in rows), 4),
    }


def summarize(run_id: int, accuracy: dict | None = None) -> dict:
    with db() as conn:
        run = run_or_404(conn, run_id)
        rows = answers_of(conn, run_id)
        wrong = {r["provider"]: r["n"] for r in conn.execute(
            "SELECT provider, COUNT(*) AS n FROM claims WHERE run_id = ? GROUP BY provider", (run_id,))}
    ents = run["entities"]
    per = {p: provider_stats([a for a in rows if a["provider"] == p], ents, p, wrong.get(p, 0))
           for p in run["providers"]}
    out = {"providers": per, "notes": list(ents.get("notes") or [])}
    if accuracy is not None:
        out["accuracy"] = accuracy
    elif run.get("stats"):
        out["accuracy"] = run["stats"].get("accuracy")
    return out


def delta(cur: dict | None, prev: dict | None) -> float | None:
    if not cur or not prev or cur.get("rate") is None or prev.get("rate") is None:
        return None
    return round(cur["rate"] - prev["rate"], 4)


def with_trend(run: dict, conn) -> dict:
    """Trend vs the previous finished run on the SAME question set version: a changed set is
    not comparable, so it gets no trend."""
    prev_row = conn.execute("SELECT * FROM runs WHERE status = 'done' AND set_id = ? AND id < ? ORDER BY id DESC LIMIT 1",
                            (run["set_id"], run["id"])).fetchone()
    prev = run_view(prev_row) if prev_row else None
    stats = run["stats"] or summarize(run["id"])
    for p, s in stats["providers"].items():
        ps = ((prev or {}).get("stats") or {}).get("providers", {}).get(p) if prev else None
        if not ps:
            s["trend"] = None
            continue
        bname = run["entities"]["brand"]["name"]
        s["trend"] = {"previous_run_id": prev["id"],
                      "mention_delta": delta(s["mention"], ps["mention"]),
                      "citation_delta": delta(s["citation"], ps["citation"]),
                      "sov_delta": (round(s["share_of_voice"][bname]["share"] - ps["share_of_voice"][bname]["share"], 4)
                                    if s["share_of_voice"].get(bname, {}).get("share") is not None
                                    and (ps["share_of_voice"].get(bname) or {}).get("share") is not None else None)}
    if not prev:
        older = conn.execute("SELECT id, set_version FROM runs WHERE status = 'done' AND id < ? ORDER BY id DESC LIMIT 1",
                             (run["id"],)).fetchone()
        if older:
            stats.setdefault("notes", []).append(
                f"no trend: the previous run ({older['id']}) used question set v{older['set_version']}, "
                f"this one v{run['set_version']}")
    return stats


# ---------- endpoints


@app.get("/health")
def health():
    with db() as conn:
        s = active_set(conn)
        last = conn.execute("SELECT id, status, started_at, finished_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        n = conn.execute("SELECT COUNT(*) FROM questions WHERE set_id = ?", (s["id"],)).fetchone()[0] if s else 0
    return {"status": "ok", "providers_enabled": pv.enabled(),
            "active_set": {"id": s["id"], "version": s["version"], "questions": n} if s else None,
            "last_run": dict(last) if last else None,
            "claims_check": bool(env("CLAIMS_URL")), "competitors_source": bool(env("AD_LIBRARY_URL"))}


@app.get("/providers")
def get_providers():
    with db() as conn:
        used = {r["provider"]: dict(r) for r in conn.execute("SELECT * FROM usage WHERE day = ?", (today(),))}
    out = pv.describe()
    for p in out:
        u = used.get(p["name"], {})
        p["today"] = {k: u.get(k, 0) for k in ("calls", "errors", "input_tokens", "output_tokens", "searches", "cost_usd")}
    return out


@app.get("/usage")
def usage(days: int = Query(default=7, ge=1, le=366)):
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM usage WHERE day >= ? ORDER BY day, provider", (since,))]
    for r in rows:
        r["cost_usd"] = round(r["cost_usd"], 6)
    return {"since": since, "rows": rows,
            "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "total_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in rows)}


@app.post("/questions/generate", status_code=201, dependencies=[Depends(require_key)])
def generate(req: GenerateIn):
    try:
        ents = build_entities()
        b = brand_profile()
        prods = "\n".join(f"- {p['name']}: {p.get('one_line', '')}".rstrip(": ")
                          for p in b.get("products") or [] if isinstance(p, dict) and p.get("name"))
        aud = b.get("audience") or {}
        audience = "; ".join(str(x) for x in [aud.get("primary"), aud.get("secondary")] if x) if isinstance(aud, dict) else str(aud)
        pains = aud.get("pains") if isinstance(aud, dict) else None
        if pains:
            audience += "\nTheir problems: " + "; ".join(str(p) for p in pains)
        variables = {"count": req.count, "branded_count": req.branded, "products": prods or None,
                     "audience": audience or None, "market": req.market or env("VIS_COUNTRY") or None}
        out = gateway("visibility_questions", {k: v for k, v in variables.items() if v is not None})
    except UpstreamError as exc:
        raise HTTPException(502, str(exc))
    raw = [q for q in out.get("questions") or [] if isinstance(q, dict)]
    kept, dropped = classify(raw, ents)
    warnings = []
    if len(kept) < 15:
        warnings.append(f"only {len(kept)} usable questions (15-30 wanted): add some by hand before approving")
    kept = kept[:30]
    nb = sum(1 for q in kept if q["kind"] == "branded")
    if nb < req.branded:
        warnings.append(f"{nb} of {req.branded} branded questions (naming {ents['brand']['name']}): add them by hand")
    with db() as conn, write(conn):
        sid = insert_set(conn, kept, "generated", req.notes)
    with db() as conn:
        s = set_or_404(conn, sid)
    return s | {"dropped": dropped, "warnings": warnings}


@app.get("/question-sets")
def list_sets():
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM questions q WHERE q.set_id = s.id) AS questions"
            " FROM question_sets s ORDER BY s.id DESC")]
    return rows


@app.get("/question-sets/{set_id}")
def get_set(set_id: int):
    with db() as conn:
        return set_or_404(conn, set_id)


@app.post("/question-sets", status_code=201, dependencies=[Depends(require_key)])
def create_set(req: SetIn):
    ents = _entities_or_502()
    kept, dropped = classify([q.model_dump() for q in req.questions], ents)
    if not kept:
        raise HTTPException(422, {"message": "no usable question", "dropped": dropped})
    with db() as conn, write(conn):
        sid = insert_set(conn, kept, "manual", req.notes)
    with db() as conn:
        return set_or_404(conn, sid) | {"dropped": dropped}


def _entities_or_502() -> dict:
    try:
        return build_entities()
    except UpstreamError as exc:
        raise HTTPException(502, str(exc))


@app.put("/question-sets/{set_id}", dependencies=[Depends(require_key)])
def edit_set(set_id: int, req: SetIn):
    """Replace a DRAFT set's questions. An approved set never changes (runs on it stay
    comparable): revise it into a new draft version instead."""
    ents = _entities_or_502()
    kept, dropped = classify([q.model_dump() for q in req.questions], ents)
    if not kept:
        raise HTTPException(422, {"message": "no usable question", "dropped": dropped})
    with db() as conn, write(conn):
        s = set_or_404(conn, set_id)
        if s["status"] != "draft":
            raise HTTPException(409, f"question set {set_id} is {s['status']}; POST /question-sets/{set_id}/revise "
                                     "makes an editable copy with a new version")
        conn.execute("DELETE FROM questions WHERE set_id = ?", (set_id,))
        conn.executemany("INSERT INTO questions (set_id, pos, text, kind) VALUES (?, ?, ?, ?)",
                         [(set_id, i + 1, q["text"], q["kind"]) for i, q in enumerate(kept)])
        if req.notes is not None:
            conn.execute("UPDATE question_sets SET notes = ? WHERE id = ?", (req.notes, set_id))
    with db() as conn:
        return set_or_404(conn, set_id) | {"dropped": dropped}


@app.post("/question-sets/{set_id}/revise", status_code=201, dependencies=[Depends(require_key)])
def revise_set(set_id: int):
    with db() as conn, write(conn):
        s = set_or_404(conn, set_id)
        sid = insert_set(conn, [{"text": q["text"], "kind": q["kind"]} for q in s["questions"]], "revision",
                         s["notes"], parent_id=set_id)
    with db() as conn:
        return set_or_404(conn, sid)


@app.post("/question-sets/{set_id}/approve", dependencies=[Depends(require_key)])
def approve_set(set_id: int, req: ApproveIn | None = None):
    """The approved set is the one weekly runs use. Approving retires the previous one."""
    with db() as conn, write(conn):
        s = set_or_404(conn, set_id)
        if s["status"] == "approved":
            return s
        if s["status"] != "draft":
            raise HTTPException(409, f"question set {set_id} is {s['status']}; revise it to make a new draft")
        if not s["questions"]:
            raise HTTPException(422, "a set needs at least one question")
        conn.execute("UPDATE question_sets SET status = 'retired' WHERE status = 'approved'")
        conn.execute("UPDATE question_sets SET status = 'approved', approved_at = ?, approved_by = ? WHERE id = ?",
                     (now_utc(), (req.approved_by if req else None), set_id))
    with db() as conn:
        return set_or_404(conn, set_id)


@app.post("/runs", status_code=202, dependencies=[Depends(require_key)])
def start_run(req: RunIn | None = None, wait: bool = False):
    """Ask every approved question to every enabled provider, `samples` times each.
    `?wait=true` answers when the run is finished (n8n uses this); otherwise 202 at once."""
    req = req or RunIn()
    enabled = pv.enabled()
    wanted = req.providers or enabled
    use = [p for p in wanted if p in enabled]
    if not use:
        raise HTTPException(409, "no provider is enabled: set at least one key (VIS_OPENAI_API_KEY, "
                                 "VIS_PERPLEXITY_API_KEY, VIS_GEMINI_API_KEY, VIS_GROQ_API_KEY)"
                            + (f"; asked for {wanted}" if req.providers else ""))
    skipped = [p for p in wanted if p not in enabled]
    samples = req.samples or env_int("VIS_SAMPLES", 3, 1, 10)
    ents = _entities_or_502()
    with db() as conn, write(conn):
        if conn.execute("SELECT 1 FROM runs WHERE status = 'running'").fetchone():
            raise HTTPException(409, "a run is already running")
        s = set_or_404(conn, req.set_id) if req.set_id else (dict(active_set(conn)) if active_set(conn) else None)
        if s is None:
            raise HTTPException(409, "no approved question set: POST /questions/generate, edit it, then approve it")
        if s["status"] not in ("approved", "retired"):
            raise HTTPException(409, f"question set {s['id']} is a {s['status']}: approve it first")
        notes = {"check_accuracy": req.check_accuracy,
                 "providers_without_key": skipped}
        cur = conn.execute("INSERT INTO runs (set_id, set_version, status, providers, samples, question_ids, entities,"
                           " notes, started_at) VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?)",
                           (s["id"], s["version"], json.dumps(use), samples,
                            json.dumps(req.question_ids) if req.question_ids else None,
                            json.dumps(ents), json.dumps(notes), now_utc()))
        run_id = cur.lastrowid
    if wait:
        execute(run_id)
        return JSONResponse(get_run(run_id), status_code=200)
    threading.Thread(target=execute, args=(run_id,), daemon=True).start()
    return {"id": run_id, "status": "running", "providers": use, "providers_without_key": skipped,
            "samples": samples, "set_id": s["id"], "set_version": s["version"]}


@app.get("/runs")
def list_runs(limit: int = Query(default=20, ge=1, le=200)):
    with db() as conn:
        rows = conn.execute("SELECT id, set_id, set_version, status, providers, samples, error, started_at, finished_at"
                            " FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["providers"] = loads(d["providers"])
        out.append(d)
    return out


@app.get("/runs/{run_id}")
def get_run(run_id: int):
    with db() as conn:
        run = run_or_404(conn, run_id)
        run["answers"] = conn.execute("SELECT COUNT(*) FROM answers WHERE run_id = ?", (run_id,)).fetchone()[0]
    return run


@app.get("/summary")
def summary(since: str | None = None, run_id: int | None = None):
    """Per provider: mention rate (unaided questions), citation rate, list position, share of
    voice vs each competitor, branded-question accuracy, and the trend vs the previous run."""
    since_iso = to_utc_iso(since) if since else None
    with db() as conn:
        run = run_or_404(conn, run_id) if run_id else latest_run(conn, since_iso)
        if run is None:
            return {"run": None, "providers": {}, "history": [],
                    "notes": ["no finished run" + (f" since {since_iso}" if since_iso else "")]}
        stats = with_trend(run, conn)
        hist = conn.execute("SELECT id, set_version, finished_at, stats FROM runs WHERE status = 'done'"
                            + (" AND finished_at >= ?" if since_iso else "") + " ORDER BY id",
                            (since_iso,) if since_iso else ()).fetchall()
    history = [{"run_id": h["id"], "set_version": h["set_version"], "finished_at": h["finished_at"],
                "mention_rate": {p: (s.get("mention") or {}).get("rate")
                                 for p, s in (loads(h["stats"]) or {}).get("providers", {}).items()}}
               for h in hist][-52:]
    return {"run": {k: run[k] for k in ("id", "set_id", "set_version", "samples", "started_at", "finished_at")},
            "brand": run["entities"]["brand"]["name"],
            "competitors": [c["name"] for c in run["entities"]["competitors"]],
            "providers": stats["providers"], "accuracy": stats.get("accuracy"),
            "notes": stats.get("notes", []), "history": history,
            "method": "Rates count answers to unaided (unbranded) questions; ci95 is a Wilson interval that treats "
                      "every answer as independent, which overstates precision when samples of one question agree. "
                      "API answers approximate, but are not, what a person sees in the consumer app."}


@app.get("/claims/wrong")
def wrong_claims(run_id: int | None = None, since: str | None = None):
    """Sentences about us that the claim checker could not support with our approved facts,
    grouped by sentence."""
    with db() as conn:
        if run_id:
            ids = [run_or_404(conn, run_id)["id"]]
        elif since:
            ids = [r["id"] for r in conn.execute("SELECT id FROM runs WHERE status = 'done' AND finished_at >= ?",
                                                 (to_utc_iso(since),))]
        else:
            last = latest_run(conn)
            ids = [last["id"]] if last else []
        if not ids:
            return {"runs": [], "claims": []}
        rows = conn.execute(f"SELECT c.*, q.text AS question FROM claims c JOIN questions q ON q.id = c.question_id"
                            f" WHERE c.run_id IN ({','.join('?' * len(ids))}) ORDER BY c.id", ids).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(norm_q(r["sentence"]), {
            "sentence": r["sentence"], "kind": r["kind"], "reasons": loads(r["reasons"]) or [],
            "providers": [], "questions": [], "answer_ids": [], "count": 0, "first_seen": r["created_at"]})
        g["count"] += 1
        g["answer_ids"].append(r["answer_id"])
        if r["provider"] not in g["providers"]:
            g["providers"].append(r["provider"])
        if r["question"] not in g["questions"]:
            g["questions"].append(r["question"])
    claims = sorted(groups.values(), key=lambda g: (-g["count"], g["first_seen"]))
    return {"runs": ids, "claims": claims,
            "note": "Not supported by your approved facts (05) means wrong or unknown to us: check each one. "
                    "Fix it at the source the AI cites, or add the true fact to brand.yaml."}


@app.get("/questions/{question_id}/answers")
def question_answers(question_id: int, run_id: int | None = None):
    with db() as conn:
        q = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone() if 0 < question_id < 2**63 else None
        if q is None:
            raise HTTPException(404, f"question {question_id} not found")
        if run_id is None:
            r = conn.execute("SELECT MAX(run_id) FROM answers WHERE question_id = ?", (question_id,)).fetchone()[0]
            run_id = r
        rows = [a for a in answers_of(conn, run_id) if a["question_id"] == question_id] if run_id else []
        claims = [dict(c) for c in conn.execute("SELECT answer_id, sentence, kind, reasons FROM claims WHERE run_id = ?"
                                                " AND question_id = ?", (run_id or 0, question_id))]
    for c in claims:
        c["reasons"] = loads(c["reasons"])
    for a in rows:
        a.pop("question", None)
        a["wrong_claims"] = [c for c in claims if c["answer_id"] == a["id"]]
    return {"question": dict(q), "run_id": run_id, "answers": rows}


def owner_of(domain: str, ents: dict) -> str:
    if an.domain_matches(domain, ents["brand"]["domains"]):
        return "us"
    for c in ents["competitors"]:
        if an.domain_matches(domain, c["domains"]):
            return c["name"]
    return "third party"


def best_fact(question: str, facts: list[str]) -> str | None:
    words = {w for w in re.findall(r"[a-z]{4,}", question.lower())}
    scored = sorted(((len(words & set(re.findall(r"[a-z]{4,}", f.lower()))), f) for f in facts), reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


@app.get("/gaps")
def gaps(run_id: int | None = None, limit: int = Query(default=20, ge=1, le=100)):
    """Unaided questions where a competitor is mentioned or cited and we are not mentioned in
    any answer, with the pages the answers cite: input for SEO briefs (69) and refreshes (68)."""
    with db() as conn:
        run = run_or_404(conn, run_id) if run_id else latest_run(conn)
        if run is None:
            return {"run_id": None, "gaps": []}
        rows = [a for a in answers_of(conn, run["id"]) if not a["error"] and a["kind"] != "branded"]
    ents = run["entities"]
    facts = brand_facts()
    by_q: dict[int, list[dict]] = {}
    for a in rows:
        by_q.setdefault(a["question_id"], []).append(a)
    out = []
    for qid, ans in by_q.items():
        if any(a["analysis"]["brand_mentioned"] for a in ans):
            continue
        comp: dict[str, dict] = {}
        for a in ans:
            for name, c in a["analysis"]["competitors"].items():
                if c["mentioned"] or c["cited"]:
                    e = comp.setdefault(name, {"name": name, "mentions": 0, "cited": 0, "positions": []})
                    e["mentions"] += int(c["mentioned"])
                    e["cited"] += int(c["cited"])
                    if c["position"]:
                        e["positions"].append(c["position"])
        if not comp:
            continue
        pages: dict[str, dict] = {}
        for a in ans:
            for c in a["citations"] or []:
                p = pages.setdefault(c["url"], {"url": c["url"], "title": c.get("title"), "domain": c.get("domain"),
                                                "owner": owner_of(c.get("domain") or "", ents), "count": 0})
                p["count"] += 1
        question = ans[0]["question"]
        fact = best_fact(question, facts)
        top_pages = sorted(pages.values(), key=lambda p: -p["count"])[:8]
        out.append({
            "question_id": qid, "question": question, "kind": ans[0]["kind"],
            "providers": sorted({a["provider"] for a in ans}), "answers": len(ans),
            "competitors": sorted(({**c, "avg_position": _avg(c.pop("positions"))} for c in comp.values()),
                                  key=lambda c: -(c["mentions"] + c["cited"])),
            "cited_pages": top_pages,
            "brief_keyword": question.rstrip("?").strip().lower()[:200],
            "suggested_faq": {
                "question": question,
                "answer_first": (f"Answer \"{question}\" in the first one or two sentences, naming "
                                 f"{ents['brand']['name']}" + (f" and this approved fact: {fact.rstrip('.')}" if fact else "")
                                 + (". Then cover what the cited pages cover." if top_pages else ".")),
                "fact": fact},
        })
    out.sort(key=lambda g: -sum(c["mentions"] + c["cited"] for c in g["competitors"]))
    return {"run_id": run["id"], "brand": ents["brand"]["name"], "gaps": out[:limit], "total": len(out)}
