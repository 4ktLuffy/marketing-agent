"""Site assistant: a guarded website chat that answers only from approved sources.

Pipeline for one visitor message (POST /chat):
1. Code routes first, with no LLM: a request for a person, a prompt-injection attempt,
   "are you human?", escalation topics (complaint, refund demand, account, legal, health),
   a request for a discount, and answers to a qualifying question.
2. Otherwise: knowledge-base excerpts (06) + approved facts (05) -> prompt `site_answer`
   via the gateway (03) -> every sentence through the deterministic guards (guards.py) ->
   the rest through the claim checker (44) with the same sources. Unsupported sentences
   are dropped; if none is left, or the model says the sources don't cover it, a person
   takes over (handoff record + notification). Nothing is shown unchecked.
3. Buying intent (code rules OR the model's flag): at most MAX_QUALIFY qualifying
   questions, then the booking link. An email is only stored through POST /consent with
   the consent box ticked; then the lead goes to 80 lead-hub (or stays here).

The public endpoints are /chat, /consent, /widget-config, /widget.js and /widget.css.
Everything under /admin needs X-API-Key and must not be exposed publicly.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StrictBool

from app import guards

log = logging.getLogger("site-assistant")
app = FastAPI(title="site-assistant")
STATIC = Path(__file__).parent / "static"


# ---------- configuration (read on every use, so tests and restarts see changes)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except ValueError:
        return default


def service_url(name: str, default: str = "") -> str:
    return env(name, default).rstrip("/")


def allowed_origins() -> set[str]:
    return {o.strip().rstrip("/") for o in env("ALLOWED_ORIGINS").split(",") if o.strip()}


def untrusted_sources() -> set[str]:
    raw = env("KB_UNTRUSTED_SOURCES", "trend-digest,competitor-watch")
    return {s.strip() for s in raw.split(",") if s.strip()}


def kb_source_allowlist() -> set[str]:
    return {s.strip() for s in env("KB_SOURCES").split(",") if s.strip()}


DEFAULT_QUESTIONS = [
    {"key": "team_size", "question": "How many people would it be for?"},
    {"key": "use_case", "question": "Is it for an office, for teammates working from home, or both?"},
]


def qualify_questions() -> list[dict]:
    """QUALIFY_QUESTIONS: a JSON list of {"key","question"}; at most MAX_QUALIFY are asked."""
    raw = env("QUALIFY_QUESTIONS")
    questions = DEFAULT_QUESTIONS
    if raw:
        try:
            parsed = json.loads(raw)
            questions = [{"key": str(q["key"]), "question": str(q["question"])} for q in parsed]
        except (ValueError, KeyError, TypeError):
            log.warning("QUALIFY_QUESTIONS is not a JSON list of {key, question}; using the default")
    return questions[:max(0, min(env_int("MAX_QUALIFY", 2), 2))]


def booking_url() -> str | None:
    url = env("BOOKING_URL")
    return url if re.match(r"^https://[^\s\"'<>]+$", url) else None


MAX_CONCURRENT = max(1, env_int("MAX_CONCURRENT_ANSWERS", 2))
_answer_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


# ---------- time


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    return fmt(datetime.now(timezone.utc))


# ---------- storage (SQLite, WAL; one instance only)

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_at TEXT NOT NULL,
    origin TEXT,
    visitor_messages INTEGER NOT NULL DEFAULT 0,
    buying_intent INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT '{}',
    email TEXT,
    name TEXT,
    company_domain TEXT,
    consent_text TEXT,
    consent_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages (conversation_id, id);
CREATE INDEX IF NOT EXISTS messages_kind ON messages (role, kind);
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    reason TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    email TEXT,
    notified INTEGER NOT NULL DEFAULT 0,
    notify_error TEXT,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id),
    at TEXT NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    company_domain TEXT,
    message TEXT NOT NULL,
    consent_text TEXT NOT NULL,
    consent_at TEXT NOT NULL,
    status TEXT NOT NULL,
    remote_id TEXT,
    error TEXT
);
"""
_migrate_lock = threading.Lock()
_migrated: set[str] = set()


def db_path() -> str:
    return env("DB_PATH", "/data/site.sqlite")


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


def session_hash(token: str) -> str:
    """Only a hash of the session id is stored: a copy of the database can't resume a chat."""
    return hashlib.sha256(token.encode()).hexdigest()


_last_purge = {"at": 0.0}


def purge_old(conn: sqlite3.Connection) -> None:
    """Delete conversations idle for RETENTION_DAYS (0 = keep), except ones that produced a lead."""
    days = env_int("RETENTION_DAYS", 90)
    if days <= 0 or time.monotonic() - _last_purge["at"] < 3600:
        return
    _last_purge["at"] = time.monotonic()
    cutoff = fmt(datetime.now(timezone.utc) - timedelta(days=days))
    conn.execute("DELETE FROM conversations WHERE last_at < ? AND id NOT IN "
                 "(SELECT conversation_id FROM leads)", (cutoff,))


def load_conversation(conn: sqlite3.Connection, token: str | None) -> dict | None:
    if not token or len(token) > 200:
        return None
    row = conn.execute("SELECT * FROM conversations WHERE session_hash = ?", (session_hash(token),)).fetchone()
    if row is None:
        return None
    ttl = timedelta(hours=env_float("SESSION_TTL_HOURS", 24))
    if datetime.fromisoformat(row["last_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc) - ttl:
        return None
    conv = dict(row)
    conv["state"] = json.loads(conv["state"] or "{}")
    return conv


def save_state(conn: sqlite3.Connection, conv: dict) -> None:
    conn.execute("UPDATE conversations SET state = ?, buying_intent = ?, last_at = ? WHERE id = ?",
                 (json.dumps(conv["state"]), int(bool(conv["buying_intent"])), now_utc(), conv["id"]))


def add_message(conn: sqlite3.Connection, conv_id: int, role: str, kind: str, text: str,
                meta: dict | None = None) -> None:
    conn.execute("INSERT INTO messages (conversation_id, at, role, kind, text, meta) VALUES (?, ?, ?, ?, ?, ?)",
                 (conv_id, now_utc(), role, kind, text, json.dumps(meta or {})))


# ---------- rate limits (in memory: one instance, reset on restart)


class Limiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.hits: dict[str, deque] = {}
        self.refused = 0

    def hit(self, key: str, limit: int, window: float) -> float | None:
        """Record a hit; None if allowed, else seconds until the next one is."""
        now = time.monotonic()
        with self.lock:
            if len(self.hits) > 50000:  # forget idle keys so memory stays bounded
                self.hits = {k: q for k, q in self.hits.items() if q and now - q[-1] < 86400}
            q = self.hits.setdefault(key, deque())
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) >= limit:
                self.refused += 1
                return window - (now - q[0])
            q.append(now)
            return None

    def reset(self):
        with self.lock:
            self.hits.clear()
            self.refused = 0


limiter = Limiter()


def client_ip(request: Request) -> str:
    """Behind a reverse proxy set TRUST_PROXY=true: the proxy appends the real client address
    as the last X-Forwarded-For entry. Without a proxy never trust that header."""
    if env("TRUST_PROXY").lower() == "true":
        fwd = request.headers.get("x-forwarded-for", "")
        last = fwd.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def limit(key: str, per_minute: int, per_day: int) -> None:
    for n, window in ((per_minute, 60), (per_day, 86400)):
        if n > 0 and (wait := limiter.hit(f"{key}:{window}", n, window)) is not None:
            raise HTTPException(429, "too many messages; please wait a moment",
                                headers={"Retry-After": str(max(1, int(wait) + 1))})


def check_origin(request: Request) -> str | None:
    """A browser always sends Origin on these POSTs; only the listed sites may use the chat.
    Requests without Origin (curl, server-side) are allowed unless REQUIRE_ORIGIN=true: the
    header is trivial to fake outside a browser, so the rate limits are the real protection."""
    origin = request.headers.get("origin")
    if origin is None:
        if env("REQUIRE_ORIGIN").lower() == "true":
            raise HTTPException(403, "origin not allowed")
        return None
    if origin.rstrip("/") not in allowed_origins():
        raise HTTPException(403, "origin not allowed")
    return origin.rstrip("/")


CORS_PATHS = {"/chat", "/consent", "/widget-config"}
BODY_PATHS = {"/chat", "/consent"}


class PublicEdge:
    """CORS for the allowed origins and a hard request-size cap on the public endpoints."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in CORS_PATHS:
            return await self.inner(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        origin = (headers.get("origin") or "").rstrip("/")
        cors = []
        if origin and origin in allowed_origins():
            cors = [(b"access-control-allow-origin", origin.encode("latin-1")), (b"vary", b"Origin")]
        if scope["method"] == "OPTIONS":
            extra = cors + [(b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                            (b"access-control-allow-headers", b"content-type"),
                            (b"access-control-max-age", b"600")] if cors else []
            await send({"type": "http.response.start", "status": 204 if cors else 403, "headers": extra})
            await send({"type": "http.response.body", "body": b""})
            return
        cap = env_int("MAX_BODY_BYTES", 4096)
        length = headers.get("content-length")
        if scope["path"] in BODY_PATHS and length is not None and (not length.isdigit() or int(length) > cap):
            body = json.dumps({"detail": f"request body over {cap} bytes"}).encode()
            await send({"type": "http.response.start", "status": 413,
                        "headers": cors + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return
        seen = 0

        async def capped_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > cap:  # chunked bodies have no content-length
                    raise HTTPException(413, f"request body over {cap} bytes")
            return message

        async def send_with_cors(message):
            if message["type"] == "http.response.start" and cors:
                message = {**message, "headers": list(message.get("headers", [])) + cors}
            await send(message)

        await self.inner(scope, capped_receive, send_with_cors)


app.add_middleware(PublicEdge)


# ---------- admin auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def key_headers() -> dict:
    key = os.environ.get("INTERNAL_API_KEY")
    return {"X-API-Key": key} if key else {}


# ---------- other services


class Unavailable(Exception):
    pass


_brand_cache: dict = {"at": 0.0, "name": None, "facts": None}


def brand() -> tuple[str, list[str]]:
    """(brand name, approved fact lines) from 05, cached 60 s; last good value kept."""
    now = time.monotonic()
    if _brand_cache["facts"] is not None and now - _brand_cache["at"] < 60:
        return _brand_cache["name"], _brand_cache["facts"]
    base = service_url("BRAND_URL", "http://brand-service:8000")
    try:
        facts = [f["text"] for f in httpx.get(f"{base}/facts", timeout=10).json()["facts"]]
        name = httpx.get(f"{base}/profile", timeout=10).json().get("name") or "our company"
        _brand_cache.update(at=now, name=name, facts=facts)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("brand-service unavailable: %s", exc)
        if _brand_cache["facts"] is None:
            return env("BRAND_NAME") or "our company", []
    return _brand_cache["name"], _brand_cache["facts"]


def brand_name() -> str:
    return env("BRAND_NAME") or brand()[0]


def kb_search(query: str) -> list[dict]:
    """Trusted knowledge-base excerpts above KB_MIN_SCORE; [] when 06 is down (the answer
    then has only the approved facts, which makes it stricter, not looser)."""
    base = service_url("KB_URL", "http://knowledge-base:8000")
    if not base:
        return []
    try:
        r = httpx.post(f"{base}/search", json={"query": query[:500], "k": env_int("KB_K", 4)}, timeout=30)
        r.raise_for_status()
        hits = r.json().get("results", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("knowledge base unavailable: %s", exc)
        return []
    allow, deny, floor = kb_source_allowlist(), untrusted_sources(), env_float("KB_MIN_SCORE", 0.35)
    out = []
    for h in hits:
        src = h.get("source") or ""
        if h.get("score", 0) < floor or src in deny or (allow and src not in allow):
            continue
        out.append({"title": (h.get("title") or "Knowledge base").strip()[:80], "text": h.get("chunk") or ""})
    return out


def run_prompt(variables: dict) -> dict:
    base = service_url("GATEWAY_URL", "http://llm-gateway:8000")
    try:
        r = httpx.post(f"{base}/v1/run", json={"prompt": "site_answer", "vars": variables},
                       headers=key_headers(), timeout=env_float("GATEWAY_TIMEOUT", 120))
        r.raise_for_status()
        out = r.json().get("output")
    except (httpx.HTTPError, ValueError) as exc:
        raise Unavailable(f"gateway: {type(exc).__name__}") from exc
    if not isinstance(out, dict):
        raise Unavailable("gateway: output is not an object")
    return out


def verify(text: str, context: str) -> dict:
    base = service_url("CLAIMS_URL", "http://claim-checker:8000")
    try:
        r = httpx.post(f"{base}/verify", json={"text": text, "context": context or None},
                       headers=key_headers(), timeout=env_float("CLAIMS_TIMEOUT", 180))
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise Unavailable(f"claim checker: {type(exc).__name__}") from exc


def notify(text: str) -> tuple[bool, str | None]:
    """Same body as the n8n notify helper's generic format: {text, content}."""
    url = env("NOTIFY_WEBHOOK_URL")
    if not url:
        return False, "NOTIFY_WEBHOOK_URL not set"
    try:
        httpx.post(url, json={"text": text, "content": text}, timeout=10).raise_for_status()
        return True, None
    except httpx.HTTPError as exc:
        return False, f"notify failed: {type(exc).__name__}"


FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com",
             "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de",
             "mail.com", "yandex.com", "web.de", "hey.com", "fastmail.com", "zoho.com"}


def forward_lead(lead: dict) -> tuple[str, str | None, str | None]:
    """(status, remote id, error). LEADS_URL empty: the lead only stays here ("stored")."""
    base = service_url("LEADS_URL")
    if not base:
        return "stored", None, None
    body = {"source": "site_assistant", "email": lead["email"], "name": lead["name"],
            "company_domain": lead["company_domain"], "message": lead["message"],
            "transcript_ref": f"site-assistant:conversation:{lead['conversation_id']}",
            "consent": {"text": lead["consent_text"], "at": lead["consent_at"]}}
    body = {k: v for k, v in body.items() if v is not None}
    try:
        r = httpx.post(f"{base}/leads", json=body, headers=key_headers(), timeout=15)
        r.raise_for_status()
        data = r.json() if r.content else {}
        remote = data.get("id") if isinstance(data, dict) else None
        return "forwarded", str(remote) if remote is not None else None, None
    except (httpx.HTTPError, ValueError) as exc:
        return "failed", None, f"{type(exc).__name__}: status {getattr(getattr(exc, 'response', None), 'status_code', '-')}"


# ---------- replies (fixed texts: nothing here comes from a model)

# Every handoff also says what this is, so a visitor asking "am I talking to a real person?" in
# words the code rules don't catch still gets an honest answer (held-out eval, night 6b).
NO_LIVE_CHAT = ("I'm an AI assistant, not a person. If you'd like a reply, leave your email in the form "
                "below and a person will answer by email. This chat isn't staffed live.")
HANDOFF_TEXT = {
    "requested": "Of course. I've asked a person from our team to pick this up.",
    "low_confidence": "I don't know — let me get a person. I've passed your question to our team.",
    "unsupported": "I couldn't confirm an answer from our sources, so I've passed your question to a person on our team.",
    "unavailable": "I can't answer that right now, so I've passed your question to a person on our team.",
    "complaint": "I'm sorry to hear that. This needs a person, so I've passed it to our team.",
    "refund": "I'm sorry to hear that. Refunds are handled by a person, so I've passed this to our team.",
    "account": "Changes to orders and accounts are handled by a person, so I've passed this to our team.",
    "legal": "This needs a person, so I've passed it to our team.",
    "health": "I can't give health or medical advice. I've passed your question to a person on our team.",
    "contact": "Thanks. A person from our team will reply by email.",
}


def disclosure() -> str:
    return (f"Hi, I'm the AI assistant of {brand_name()}, not a person. I answer from our FAQ and product "
            "facts, and I can get a person for you at any time.")


def human_reply() -> str:
    return ("No, I'm an AI assistant, not a person. I answer from our FAQ and product facts. "
            "If you'd like a person, press \"Talk to a person\" or just ask.")


def injection_reply() -> str:
    return (f"I can't change how I work or share my instructions. I can answer questions about "
            f"{brand_name()} from our FAQ and product facts, or get a person for you.")


DISCOUNT_REPLY = ("I can't offer or promise discounts or deals. If you'd like to ask our team about "
                  "pricing, I can get a person for you.")
EMAIL_IN_CHAT = ("For your privacy I didn't keep the contact details you typed. To get a reply by email, "
                 "use the form below.")


def consent_text() -> str:
    return env("CONSENT_TEXT") or (
        f"Yes, {brand_name()} may use my email address and this chat to reply to me. "
        "It won't be added to a mailing list.")


# ---------- the pipeline


def open_handoff(conn: sqlite3.Connection, conv: dict, reason: str, message: str) -> dict:
    """One open handoff per conversation; a new one notifies the team."""
    row = conn.execute("SELECT * FROM handoffs WHERE conversation_id = ? AND status = 'open'",
                       (conv["id"],)).fetchone()
    if row:
        return dict(row)
    cur = conn.execute(
        "INSERT INTO handoffs (conversation_id, at, reason, message, email) VALUES (?, ?, ?, ?, ?)",
        (conv["id"], now_utc(), reason, message[:1000], conv.get("email")))
    hid = cur.lastrowid
    email = conv.get("email")
    ok, err = notify(
        f"Site assistant: a visitor needs a person (handoff #{hid}, reason: {reason}). "
        f"Message: \"{message[:300]}\". "
        + (f"Reply by email to {email}." if email else "No email yet (the visitor was asked to leave one).")
        + f" Conversation {conv['id']}: GET /admin/conversations/{conv['id']} on the site assistant.")
    conn.execute("UPDATE handoffs SET notified = ?, notify_error = ? WHERE id = ?", (int(ok), err, hid))
    return dict(conn.execute("SELECT * FROM handoffs WHERE id = ?", (hid,)).fetchone())


def handoff_reply(conn, conv: dict, reason: str, message: str) -> dict:
    h = open_handoff(conn, conv, reason, message)
    text = HANDOFF_TEXT.get(reason, HANDOFF_TEXT["requested"])
    if conv.get("email"):
        text += " I'm an AI assistant, not a person; they'll reply by email to the address you gave us."
    else:
        text += " " + NO_LIVE_CHAT
    return {"reply": text, "kind": "handoff", "handoff": True, "show_consent": not conv.get("email"),
            "meta": {"reason": reason, "handoff_id": h["id"]}}


def next_qualify_step(conv: dict) -> tuple[str | None, bool]:
    """(text to append, offer booking). Asks the next unanswered qualifying question, then
    offers the booking link once."""
    st = conv["state"]
    answers = st.setdefault("answers", {})
    for q in qualify_questions():
        if q["key"] not in answers and q["key"] not in st.get("asked", []):
            st.setdefault("asked", []).append(q["key"])
            st["pending"] = q["key"]
            return q["question"], False
    st.pop("pending", None)
    if st.get("offer_made"):
        return None, False
    st["offer_made"] = True
    url = booking_url()
    if url:
        return (f"If you'd like to talk it through, you can book a call with our team: {url} "
                "Or leave your email below and a person will reply."), True
    return "If you'd like to talk it through, leave your email below and a person from our team will reply.", True


def cite(kept: list[str], listed: list, sources: list[dict]) -> list[str]:
    """Titles of the sources the answer used: the model's list, checked; else best word overlap."""
    valid = [n for n in listed if isinstance(n, int) and 1 <= n <= len(sources)]
    if not valid:
        words = {w for w in re.findall(r"[a-z]{4,}", " ".join(kept).lower())}
        scored = sorted(((len(words & set(re.findall(r"[a-z]{4,}", s["text"].lower()))), i + 1)
                         for i, s in enumerate(sources)), reverse=True)
        valid = [n for score, n in scored[:2] if score >= 2]
    titles = []
    for n in valid:
        t = sources[n - 1]["title"]
        if t not in titles:
            titles.append(t)
    return titles


def drop_unsupported(kept: list[str], result: dict) -> tuple[list[str], list[str]]:
    """Split sentences into (supported, unsupported) using 44's per-sentence verdicts."""
    bad_claims = [guards.normalize(c["claim"]) for c in result.get("claims", []) if not c.get("supported")]
    bad_claims = [c for c in bad_claims if c]
    bad_numbers = {n["value"] for n in result.get("numbers", []) if not n.get("supported")}
    ok, dropped = [], []
    for s in kept:
        ns = guards.normalize(s)
        if any(c == ns or c in ns or (len(ns) >= 20 and ns in c) for c in bad_claims) or (guards.numbers(s) & bad_numbers):
            dropped.append(s)
        else:
            ok.append(s)
    if not result.get("ok", False) and not dropped and result.get("unsupported"):
        # 44 said "not ok" but no sentence matched: fail closed, drop everything.
        return [], kept
    return ok, dropped


def answer_from_sources(conn, conv: dict, message: str) -> dict:
    st = conv["state"]
    query = message
    if len(message.split()) < 6 and st.get("previous"):
        query = f"{st['previous']} {message}"
    name, facts = brand()
    sources = kb_search(query) + [{"title": "Product facts", "text": f} for f in facts]
    if not sources:
        return handoff_reply(conn, conv, "unavailable", message)
    listing = "\n".join(f"[{i + 1}] ({s['title']}) {' '.join(s['text'].split())}" for i, s in enumerate(sources))
    evidence = "\n".join(s["text"] for s in sources)
    kb_context = "\n\n".join(s["text"] for s in sources if s["title"] != "Product facts")
    variables = {"question": message, "sources": listing, "brand_name": env("BRAND_NAME") or name}
    if st.get("previous"):
        variables["previous"] = st["previous"]
    if not _answer_slots.acquire(timeout=env_float("QUEUE_WAIT_SECONDS", 30)):
        raise HTTPException(503, "the assistant is busy; please try again in a minute",
                            headers={"Retry-After": "30"})
    try:
        try:
            out = run_prompt(variables)
        except Unavailable as exc:
            log.warning("%s", exc)
            return handoff_reply(conn, conv, "unavailable", message)
        llm_buying = bool(out.get("buying_intent"))
        text = str(out.get("answer") or "")
        if not out.get("covered") or re.search(r"don.?t know|let me get a person", text, re.I):
            r = handoff_reply(conn, conv, "low_confidence", message)
            r["llm_buying"] = llm_buying
            return r
        kept, guard_dropped = [], []
        for s in guards.split_sentences(text):
            why = guards.blocked_reason(s, evidence)
            (guard_dropped.append({"sentence": s, "reason": why}) if why else kept.append(s))
        unsupported = []
        if kept:
            try:
                result = verify("\n".join(kept), kb_context)
            except Unavailable as exc:
                log.warning("%s", exc)
                r = handoff_reply(conn, conv, "unavailable", message)
                r["meta"]["guard_dropped"] = guard_dropped
                return r
            kept, unsupported = drop_unsupported(kept, result)
    finally:
        _answer_slots.release()
    meta = {"guard_dropped": guard_dropped, "unsupported_dropped": unsupported}
    if not kept:
        r = handoff_reply(conn, conv, "unsupported", message)
        r["meta"].update(meta)
        r["llm_buying"] = llm_buying
        return r
    titles = cite(kept, out.get("sources") or [], sources)
    meta["sources"] = titles
    return {"reply": " ".join(kept), "kind": "answer", "sources": titles, "meta": meta,
            "llm_buying": llm_buying}


def route(conn, conv: dict, message: str, action: str | None) -> dict:
    """Decide the reply. Code rules first; the LLM only for questions that pass them."""
    st = conv["state"]
    if action == "handoff":
        return handoff_reply(conn, conv, "requested", message or "(pressed Talk to a person)")
    if guards.is_injection(message):
        return {"reply": injection_reply(), "kind": "refusal", "meta": {"reason": "injection"}}
    if guards.asks_if_human(message):
        return {"reply": human_reply(), "kind": "disclosure", "meta": {}}
    if guards.asks_for_person(message):
        return handoff_reply(conn, conv, "requested", message)
    topics = guards.escalation_topics(message)
    if topics:
        # health first: its reply must say "no health advice"
        reason = "health" if "health" in topics else topics[0]
        r = handoff_reply(conn, conv, reason, message)
        r["meta"]["topics"] = topics
        return r
    if guards.asks_for_discount(message):
        conv["buying_intent"] = True
        return {"reply": DISCOUNT_REPLY, "kind": "refusal", "meta": {"reason": "discount"},
                "show_consent": True}
    if st.get("pending") and not guards.is_question(message):
        st.setdefault("answers", {})[st.pop("pending")] = message[:200]
        return {"reply": "Thanks.", "kind": "qualify", "meta": {}, "qualify_turn": True}
    return answer_from_sources(conn, conv, message)


# ---------- public endpoints


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=200)
    message: str = Field(default="", max_length=20000)
    action: Literal["handoff"] | None = None


class ConsentRequest(BaseModel):
    session_id: str = Field(max_length=200)
    email: str = Field(max_length=254)
    name: str | None = Field(default=None, max_length=120)
    agree: StrictBool


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/widget-config")
def widget_config():
    return {"assistant_name": f"{brand_name()} assistant", "disclosure": disclosure(),
            "consent_text": consent_text(), "booking_url": booking_url()}


@app.get("/widget.js")
def widget_js():
    return FileResponse(STATIC / "widget.js", media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"})


@app.get("/widget.css")
def widget_css():
    return FileResponse(STATIC / "widget.css", media_type="text/css",
                        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"})


@app.post("/chat")
def chat(req: ChatRequest, request: Request):
    origin = check_origin(request)
    limit(f"ip:{client_ip(request)}", env_int("RATE_IP_PER_MINUTE", 8), env_int("RATE_IP_PER_DAY", 150))
    message = " ".join(req.message.split())
    if len(message) > env_int("MAX_MESSAGE_CHARS", 800):
        raise HTTPException(413, "message too long")
    if not message and req.action != "handoff":
        raise HTTPException(422, "message is empty")
    with db() as conn:
        conv = load_conversation(conn, req.session_id)
        token, started = req.session_id, False
        if conv is None:
            token, started = secrets.token_urlsafe(24), True
            with write(conn):
                purge_old(conn)
                ts = now_utc()
                conn.execute("INSERT INTO conversations (session_hash, created_at, last_at, origin)"
                             " VALUES (?, ?, ?, ?)", (session_hash(token), ts, ts, origin))
            conv = load_conversation(conn, token)
        limit(f"session:{conv['id']}", env_int("RATE_SESSION_PER_MINUTE", 5), 0)
        if conv["visitor_messages"] >= env_int("MAX_SESSION_MESSAGES", 40):
            raise HTTPException(429, "this conversation is too long; please start a new one")
        stored = guards.redact(message) if message else "(pressed Talk to a person)"
        typed_contact = stored != message and bool(message)

        # The LLM work happens outside any write transaction; state is saved afterwards.
        result = route(conn, conv, guards.redact(message), req.action)
        kind, reason = result["kind"], result.get("meta", {}).get("reason")
        signal = guards.buying_intent(message) or bool(result.pop("llm_buying", False))
        if kind == "handoff":  # a complaint that mentions "team" is not a lead
            buying = signal and reason in ("requested", "low_confidence", "unsupported")
        else:
            buying = signal and kind in ("answer", "refusal") and reason != "injection"
        qualify_turn = bool(result.pop("qualify_turn", False))
        st = conv["state"]
        booking = False
        if buying:
            conv["buying_intent"] = True
            if (size := guards.team_size(message)) and "team_size" not in st.setdefault("answers", {}):
                st["answers"]["team_size"] = size
            st.setdefault("intent_message", guards.redact(message)[:500])
        if (buying or qualify_turn) and not result.get("handoff"):
            extra, booking = next_qualify_step(conv)
            if extra:
                result["reply"] = f"{result['reply']}\n\n{extra}"
        if typed_contact:
            result["reply"] = f"{EMAIL_IN_CHAT}\n\n{result['reply']}"
            result["show_consent"] = not conv.get("email")
        if message and result["kind"] not in ("refusal",):
            st["previous"] = guards.redact(message)[:300]
        reply = result["reply"]
        if started:
            reply = f"{disclosure()}\n\n{reply}"
        with write(conn):
            conn.execute("UPDATE conversations SET visitor_messages = visitor_messages + 1 WHERE id = ?",
                         (conv["id"],))
            add_message(conn, conv["id"], "visitor", "action" if req.action else "message", stored)
            add_message(conn, conv["id"], "assistant", result["kind"], reply, result.get("meta"))
            save_state(conn, conv)
    return {
        "session_id": token,
        "conversation_started": started,
        "disclosure": disclosure() if started else None,
        "reply": reply,
        "kind": result["kind"],
        "sources": result.get("sources", []),
        "actions": {
            "handoff": bool(result.get("handoff")),
            "show_consent": bool(result.get("show_consent") or booking) and not conv.get("email"),
            "booking_url": booking_url() if booking else None,
        },
    }


@app.post("/consent")
def consent(req: ConsentRequest, request: Request):
    """The only way an email is stored: the visitor ticked the consent box (agree=true)."""
    check_origin(request)
    limit(f"ip:{client_ip(request)}", env_int("RATE_IP_PER_MINUTE", 8), env_int("RATE_IP_PER_DAY", 150))
    if req.agree is not True:
        raise HTTPException(422, "consent is required to store an email address")
    email = req.email.strip()
    if not guards.EMAIL_RE.fullmatch(email):
        raise HTTPException(422, "not an email address")
    name = " ".join((req.name or "").split()) or None
    domain = email.rsplit("@", 1)[1].lower()
    company = None if domain in FREE_MAIL else domain
    text, at = consent_text(), now_utc()
    with db() as conn:
        conv = load_conversation(conn, req.session_id)
        if conv is None:
            raise HTTPException(404, "unknown or expired session")
        with write(conn):
            conn.execute("UPDATE conversations SET email = ?, name = ?, company_domain = ?, consent_text = ?,"
                         " consent_at = ?, last_at = ? WHERE id = ?",
                         (email, name, company, text, at, at, conv["id"]))
            add_message(conn, conv["id"], "visitor", "consent", f"[consent given: {text}]")
        conv.update(email=email, name=name, company_domain=company)
        lead_status = None
        open_h = conn.execute("SELECT * FROM handoffs WHERE conversation_id = ? AND status = 'open'",
                              (conv["id"],)).fetchone()
        if open_h:
            ok, err = notify(f"Site assistant: the visitor of handoff #{open_h['id']} left an email: {email}. "
                             f"Conversation {conv['id']}.")
            with write(conn):
                conn.execute("UPDATE handoffs SET email = ?, notified = MAX(notified, ?), notify_error = ?"
                             " WHERE id = ?", (email, int(ok), err, open_h["id"]))
        if conv["buying_intent"]:
            existing = conn.execute("SELECT id FROM leads WHERE conversation_id = ?", (conv["id"],)).fetchone()
            if not existing:
                st = conv["state"]
                answers = "; ".join(f"{k}: {v}" for k, v in st.get("answers", {}).items())
                lead = {"conversation_id": conv["id"], "email": email, "name": name, "company_domain": company,
                        "message": (st.get("intent_message") or st.get("previous") or "")
                        + (f" ({answers})" if answers else ""),
                        "consent_text": text, "consent_at": at}
                status, remote, err = forward_lead(lead)
                with write(conn):
                    conn.execute(
                        "INSERT INTO leads (conversation_id, at, email, name, company_domain, message,"
                        " consent_text, consent_at, status, remote_id, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (conv["id"], at, email, name, company, lead["message"], text, at, status, remote, err))
                lead_status = status
        elif not open_h:
            open_handoff(conn, conv, "contact", conv["state"].get("previous") or "(left an email)")
        reply = "Thanks. A person from our team will reply by email."
        if lead_status and booking_url():
            reply += f" You can also book a call: {booking_url()}"
        with write(conn):
            add_message(conn, conv["id"], "assistant", "consent_ack", reply)
    return {"ok": True, "reply": reply, "lead": lead_status is not None}


# ---------- admin (X-API-Key; never expose publicly)


def loads(row: sqlite3.Row, *json_cols: str) -> dict:
    d = dict(row)
    for c in json_cols:
        d[c] = json.loads(d[c] or "{}")
    return d


@app.get("/admin/conversations", dependencies=[Depends(require_key)])
def list_conversations(limit_: int = Query(50, alias="limit", ge=1, le=500),
                       handed_off: bool | None = None, buying: bool | None = None):
    sql = ("SELECT c.id, c.created_at, c.last_at, c.origin, c.visitor_messages, c.buying_intent,"
           " c.email IS NOT NULL AS has_email,"
           " (SELECT COUNT(*) FROM handoffs h WHERE h.conversation_id = c.id) AS handoffs,"
           " (SELECT COUNT(*) FROM leads l WHERE l.conversation_id = c.id) AS leads,"
           " (SELECT text FROM messages m WHERE m.conversation_id = c.id AND m.role = 'visitor'"
           "  ORDER BY m.id LIMIT 1) AS first_message FROM conversations c")
    where = []
    if handed_off is not None:
        where.append(("" if handed_off else "NOT ") + "EXISTS (SELECT 1 FROM handoffs h WHERE h.conversation_id = c.id)")
    if buying is not None:
        where.append(f"c.buying_intent = {int(buying)}")
    if where:
        sql += " WHERE " + " AND ".join(where)
    with db() as conn:
        rows = conn.execute(sql + " ORDER BY c.last_at DESC, c.id DESC LIMIT ?", (limit_,)).fetchall()
    return [dict(r) | {"buying_intent": bool(r["buying_intent"]), "has_email": bool(r["has_email"])} for r in rows]


@app.get("/admin/conversations/{conv_id}", dependencies=[Depends(require_key)])
def get_conversation(conv_id: int):
    if not 0 < conv_id < 2**63:
        raise HTTPException(404, "conversation not found")
    with db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "conversation not found")
        conv = loads(row, "state")
        conv.pop("session_hash")
        conv["buying_intent"] = bool(conv["buying_intent"])
        conv["messages"] = [loads(m, "meta") for m in conn.execute(
            "SELECT id, at, role, kind, text, meta FROM messages WHERE conversation_id = ? ORDER BY id", (conv_id,))]
        conv["handoffs"] = [dict(h) for h in conn.execute(
            "SELECT * FROM handoffs WHERE conversation_id = ? ORDER BY id", (conv_id,))]
        conv["leads"] = [dict(x) for x in conn.execute(
            "SELECT * FROM leads WHERE conversation_id = ? ORDER BY id", (conv_id,))]
    return conv


@app.get("/admin/handoffs", dependencies=[Depends(require_key)])
def list_handoffs(status: Literal["open", "closed"] | None = None):
    sql, args = "SELECT * FROM handoffs", []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    with db() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC", args)]


@app.post("/admin/handoffs/{handoff_id}/close", dependencies=[Depends(require_key)])
def close_handoff(handoff_id: int):
    with db() as conn, write(conn):
        cur = conn.execute("UPDATE handoffs SET status = 'closed', closed_at = ? WHERE id = ? AND status = 'open'",
                           (now_utc(), handoff_id))
        row = conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "handoff not found")
    return dict(row) | {"changed": bool(cur.rowcount)}


@app.get("/admin/leads", dependencies=[Depends(require_key)])
def list_leads(status: Literal["forwarded", "stored", "failed"] | None = None):
    sql, args = "SELECT * FROM leads", []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    with db() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC", args)]


@app.post("/admin/leads/{lead_id}/forward", dependencies=[Depends(require_key)])
def retry_lead(lead_id: int):
    """Send a stored or failed lead to LEADS_URL again (80 dedupes by email)."""
    with db() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "lead not found")
        if not service_url("LEADS_URL"):
            raise HTTPException(409, "LEADS_URL is not set")
        status, remote, err = forward_lead(dict(row))
        with write(conn):
            conn.execute("UPDATE leads SET status = ?, remote_id = ?, error = ? WHERE id = ?",
                         (status, remote, err, lead_id))
        return dict(conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())


@app.get("/admin/stats", dependencies=[Depends(require_key)])
def stats():
    with db() as conn:
        one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
        by_kind = {r["kind"]: r["n"] for r in conn.execute(
            "SELECT kind, COUNT(*) AS n FROM messages WHERE role = 'assistant' GROUP BY kind")}
        refusals = {}
        dropped_sentences = guard_sentences = answers_with_drops = 0
        for r in conn.execute("SELECT kind, meta FROM messages WHERE role = 'assistant'"):
            meta = json.loads(r["meta"] or "{}")
            if r["kind"] == "refusal":
                refusals[meta.get("reason", "other")] = refusals.get(meta.get("reason", "other"), 0) + 1
            n = len(meta.get("unsupported_dropped", []))
            dropped_sentences += n
            guard_sentences += len(meta.get("guard_dropped", []))
            answers_with_drops += int(n > 0 or bool(meta.get("guard_dropped")))
        reasons = {r["reason"]: r["n"] for r in conn.execute(
            "SELECT reason, COUNT(*) AS n FROM handoffs GROUP BY reason")}
        leads = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM leads GROUP BY status")}
        return {
            "conversations": one("SELECT COUNT(*) FROM conversations"),
            "visitor_messages": one("SELECT COUNT(*) FROM messages WHERE role = 'visitor'"),
            "answered": by_kind.get("answer", 0),
            "handed_off": {"total": sum(reasons.values()), "open": one(
                "SELECT COUNT(*) FROM handoffs WHERE status = 'open'"), "by_reason": reasons},
            "unsupported_dropped": {"sentences_by_claim_checker": dropped_sentences,
                                    "sentences_by_guards": guard_sentences,
                                    "replies_with_drops": answers_with_drops},
            "refused": refusals,
            "leads_captured": {"total": sum(leads.values()), **leads},
            "replies_by_kind": by_kind,
            "rate_limited_since_start": limiter.refused,
        }
