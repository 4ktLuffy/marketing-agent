"""Lead hub: inbound leads -> enrichment from their own website -> score with reasons ->
CRM (dry run by default) -> a drafted first reply that a person approves and sends.

Inbound only. It never sends cold outreach, never scrapes LinkedIn or any social network,
never buys data, and never sends an email itself. Nothing leaves the hub for a lead without
recorded consent AND DRY_RUN=false.
"""
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import crm, enrich, scoring, webhooks

app = FastAPI(title="lead-hub")
log = logging.getLogger("lead-hub")

SOURCES = ("site_assistant", "form", "webhook", "manual")
MAX_WEBHOOK_BYTES = 200_000
EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,253}\.[a-zA-Z]{2,63}$")
ACTIONS = {"A": ("notify_now", "hot"), "B": ("daily_digest", "digest"),
           "C": ("nurture", "nurture"), "D": ("nurture", "nurture")}
LEAD_JSON = ("utm", "score_detail", "enrichment", "route", "reply", "sources")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    given_domain TEXT,
    company_domain TEXT,
    domain_note TEXT,
    sources TEXT NOT NULL,
    consent_text TEXT,
    consent_at TEXT,
    consent_recorded_at TEXT,
    utm TEXT,
    status TEXT NOT NULL,
    score INTEGER,
    grade TEXT,
    score_detail TEXT,
    enrich_status TEXT NOT NULL,
    enrichment TEXT,
    route TEXT,
    reply TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS leads_grade ON leads (grade, updated_at);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT,
    message TEXT,
    transcript_ref TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS activities_lead ON activities (lead_id, id);
"""
_migrate_lock = threading.Lock()
_migrated: set[str] = set()
_process_lock = threading.Lock()  # one lead at a time: polite to sites and to the local LLM


# ---------- time


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    return fmt(datetime.now(timezone.utc))


def to_utc_iso(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"not an ISO 8601 date/time: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return fmt(dt)


def env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() not in ("false", "0", "no", "off")


# ---------- storage


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/leads.sqlite")


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
        conn.execute("PRAGMA secure_delete=ON")  # erased leads are overwritten, not just unlinked
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


def row_to_lead(row: sqlite3.Row) -> dict:
    lead = dict(row)
    for k in LEAD_JSON:
        lead[k] = json.loads(lead[k]) if lead.get(k) else None
    text, at, recorded = lead.pop("consent_text"), lead.pop("consent_at"), lead.pop("consent_recorded_at")
    lead["consent"] = {"text": text, "at": at, "recorded_at": recorded} if text else None
    return lead


def lead_or_404(conn: sqlite3.Connection, lead_id: int) -> dict:
    if not 0 < lead_id < 2**63:
        raise HTTPException(404, f"lead {lead_id} not found")
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"lead {lead_id} not found")
    return row_to_lead(row)


def activities(conn: sqlite3.Connection, lead_id: int) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM activities WHERE lead_id = ? ORDER BY id", (lead_id,)):
        a = dict(r)
        a["data"] = json.loads(a["data"]) if a["data"] else None
        a.pop("lead_id")
        out.append(a)
    return out


def add_activity(conn, lead_id: int, kind: str, source=None, message=None, transcript_ref=None, data=None):
    conn.execute("INSERT INTO activities (lead_id, at, kind, source, message, transcript_ref, data)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (lead_id, now_utc(), kind, source, message, transcript_ref,
                  json.dumps(data) if data is not None else None))


def set_fields(lead_id: int, kind: str | None = None, data=None, **fields) -> None:
    """Update a lead (JSON fields serialised) and log one activity, in one transaction.
    A lead erased meanwhile is left erased."""
    with db() as conn, write(conn):
        if conn.execute("SELECT 1 FROM leads WHERE id = ?", (lead_id,)).fetchone() is None:
            return
        cols = {k: (json.dumps(v) if k in LEAD_JSON and v is not None else v) for k, v in fields.items()}
        cols["updated_at"] = now_utc()
        conn.execute(f"UPDATE leads SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                     (*cols.values(), lead_id))
        if kind:
            add_activity(conn, lead_id, kind, data=data)


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- models


class Consent(BaseModel):
    text: str = Field(max_length=1000)
    at: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("consent text must say what the person agreed to")
        return v.strip()

    @field_validator("at")
    @classmethod
    def when(cls, v: str) -> str:
        return to_utc_iso(v)


class LeadIn(BaseModel):
    source: Literal["site_assistant", "form", "webhook", "manual"]
    email: str = Field(max_length=320)
    name: str | None = Field(default=None, max_length=200)
    company_domain: str | None = Field(default=None, max_length=300)
    message: str | None = Field(default=None, max_length=10000)
    transcript_ref: str | None = Field(default=None, max_length=300)
    consent: Consent | None = None
    utm: dict[str, str] | None = None

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("not a valid email address")
        return v

    @field_validator("name", "message", "transcript_ref", "company_domain")
    @classmethod
    def blank_is_none(cls, v):
        return v.strip() or None if isinstance(v, str) else v

    @field_validator("utm")
    @classmethod
    def small_utm(cls, v):
        if v is None:
            return v
        if len(v) > 10:
            raise ValueError("at most 10 utm fields")
        return {k.removeprefix("utm_")[:40]: str(val)[:200] for k, val in v.items()}


class ConsentChange(BaseModel):
    given: bool
    text: str | None = Field(default=None, max_length=1000)
    at: str | None = None


class ReplyApproval(BaseModel):
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=5000)
    approved_by: str | None = Field(default=None, max_length=200)


# ---------- capture


def capture(req: LeadIn) -> tuple[dict, bool]:
    ts = now_utc()
    domain, why = enrich.pick_domain(req.email, req.company_domain)
    with db() as conn, write(conn):
        row = conn.execute("SELECT * FROM leads WHERE email = ?", (req.email,)).fetchone()
        created = row is None
        if created:
            cur = conn.execute(
                "INSERT INTO leads (email, name, given_domain, company_domain, domain_note, sources,"
                " consent_text, consent_at, consent_recorded_at, utm, status, enrich_status,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)",
                (req.email, req.name, req.company_domain, domain, why, json.dumps([req.source]),
                 req.consent.text if req.consent else None, req.consent.at if req.consent else None,
                 ts if req.consent else None, json.dumps(req.utm) if req.utm else None,
                 "pending" if domain else "skipped", ts, ts))
            lead_id = cur.lastrowid
        else:
            lead = row_to_lead(row)
            lead_id = lead["id"]
            sources = lead["sources"] + ([req.source] if req.source not in lead["sources"] else [])
            upd = {"sources": json.dumps(sources), "updated_at": ts, "name": lead["name"] or req.name,
                   "given_domain": lead["given_domain"] or req.company_domain,
                   "utm": json.dumps(lead["utm"] or req.utm) if (lead["utm"] or req.utm) else None}
            if not lead["company_domain"] and domain:
                upd |= {"company_domain": domain, "domain_note": why, "enrich_status": "pending"}
            if req.consent:  # newer consent replaces the older record
                upd |= {"consent_text": req.consent.text, "consent_at": req.consent.at,
                        "consent_recorded_at": ts}
            conn.execute(f"UPDATE leads SET {', '.join(f'{k} = ?' for k in upd)} WHERE id = ?",
                         (*upd.values(), lead_id))
        add_activity(conn, lead_id, "captured" if created else "merged", source=req.source,
                     message=req.message, transcript_ref=req.transcript_ref,
                     data={"utm": req.utm} if req.utm else None)
        if req.consent:
            add_activity(conn, lead_id, "consent_given", source=req.source,
                         data={"text": req.consent.text, "at": req.consent.at})
        return lead_or_404(conn, lead_id), created


def capture_response(lead: dict, created: bool, tasks: BackgroundTasks) -> JSONResponse:
    if env_bool("AUTO_PROCESS", True):
        tasks.add_task(process, lead["id"])
    body = {"id": lead["id"], "created": created, "merged": not created,
            "company_domain": lead["company_domain"], "domain_note": lead["domain_note"],
            "enrich_status": lead["enrich_status"], "consent": lead["consent"] is not None,
            "processing": env_bool("AUTO_PROCESS", True)}
    return JSONResponse(body, status_code=201 if created else 200, background=tasks)


# ---------- pipeline


def messages_of(lead_id: int) -> list[dict]:
    with db() as conn:
        return [a for a in activities(conn, lead_id) if a["message"]]


def get_lead(lead_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return row_to_lead(row) if row else None


def step_enrich(lead: dict, force: bool = False) -> None:
    if not lead["company_domain"]:
        return
    if lead["enrich_status"] == "done" and not force:
        return
    if not env_bool("ENRICH", True):
        set_fields(lead["id"], "enrich_skipped", {"why": "ENRICH=false"}, enrich_status="skipped")
        return
    if not force:
        # Another lead from the same company was enriched recently: reuse it, don't re-fetch.
        days = int(os.environ.get("ENRICH_CACHE_DAYS", "30") or 0)
        cutoff = fmt(datetime.now(timezone.utc) - timedelta(days=days))
        with db() as conn:
            for (raw,) in conn.execute(
                    "SELECT enrichment FROM leads WHERE company_domain = ? AND id != ?"
                    " AND enrich_status = 'done' ORDER BY updated_at DESC LIMIT 5",
                    (lead["company_domain"], lead["id"])):
                cached = json.loads(raw)
                if days > 0 and cached.get("at", "") >= cutoff:
                    set_fields(lead["id"], "enriched", {"sources": cached["sources"], "reused": True},
                               enrich_status="done", enrichment=cached)
                    return
    try:
        record = enrich.enrich_domain(lead["company_domain"])
    except enrich.EnrichError as exc:
        set_fields(lead["id"], "enrich_failed", {"why": str(exc)}, enrich_status="failed",
                   enrichment={"domain": lead["company_domain"], "error": str(exc)})
        return
    record["at"] = now_utc()
    set_fields(lead["id"], "enriched",
               {"sources": record["sources"], "facts": len(record["facts"]),
                "dropped": len(record["dropped"])}, enrich_status="done", enrichment=record)


def score_facts(lead: dict, msgs: list[dict]) -> dict:
    e = lead.get("enrichment") or {}
    return {
        "message": "\n".join(m["message"] for m in msgs),
        "industry": e.get("industry", ""),
        "enrichment": " ".join([e.get("industry", ""), e.get("sells", "")]
                               + [f["text"] for f in e.get("facts", [])]),
        "size": " ".join(f"{h['statement']} {h['quote']}" for h in e.get("size_hints", [])),
        "source": lead["sources"],
        "utm_source": (lead.get("utm") or {}).get("source"),
        "utm_medium": (lead.get("utm") or {}).get("medium"),
        "utm_campaign": (lead.get("utm") or {}).get("campaign"),
        "messages": len(msgs),
        "has_company_domain": lead["company_domain"] is not None,
        "has_consent": lead["consent"] is not None,
    }


def step_score(lead: dict, msgs: list[dict]) -> dict:
    result = scoring.score_lead(score_facts(lead, msgs))
    set_fields(lead["id"], "scored", {"score": result["score"], "grade": result["grade"]},
               score=result["score"], grade=result["grade"], score_detail=result)
    return result


def crm_note(lead: dict, msgs: list[dict], action: str) -> str:
    e = lead.get("enrichment") or {}
    lines = [f"Inbound lead via {', '.join(lead['sources'])} (lead-hub #{lead['id']}).",
             f"Score {lead['score']} ({lead['grade']}), routing: {action}."]
    lines += [f"- {r['reason']} ({r['points']:+d})" for r in (lead.get("score_detail") or {}).get("reasons", [])]
    if msgs:
        lines += ["", "Their message:", msgs[-1]["message"][:2000]]
    if e.get("facts"):
        lines += ["", f"From their website ({lead['company_domain']}):"]
        lines += [f"- {f['text']} [{f['source_url']}]" for f in e["facts"]]
    lines += ["", f"Consent: {lead['consent']['text']} ({lead['consent']['at']})"]
    return "\n".join(lines)


def notify(lead: dict) -> dict:
    """Grade A: tell a person now. Personal details stay in the hub; the alert has the id."""
    url = os.environ.get("NOTIFY_WEBHOOK_URL", "").strip()
    payload = {"text": f"New A lead #{lead['id']} ({lead['score']}/100)"
                       f"{' from ' + lead['company_domain'] if lead['company_domain'] else ''}: "
                       + "; ".join(r["reason"] for r in lead["score_detail"]["reasons"][:4])}
    if not url:
        return {"sent": False, "why": "NOTIFY_WEBHOOK_URL is empty", "payload": payload}
    if crm.dry_run():
        return {"sent": False, "dry_run": True, "payload": payload}
    try:
        r = httpx.post(url, json=payload, timeout=10)
        return {"sent": r.status_code < 300, "status": r.status_code}
    except httpx.HTTPError as exc:
        return {"sent": False, "error": type(exc).__name__}


def step_route(lead: dict, msgs: list[dict]) -> dict:
    action, tag = ACTIONS[lead["grade"]]
    route = {"grade": lead["grade"], "action": action, "tag": tag, "at": now_utc(),
             "dry_run": crm.dry_run(), "crm": [], "notify": None}
    if lead["consent"] is None:
        route["blocked"] = "no consent recorded: nothing is sent to a CRM or a person's inbox"
    else:
        lead_for_crm = {**lead, "now": now_utc()}
        route["crm"] = crm.sync(lead_for_crm, crm_note(lead, msgs, action), tag)
        if action == "notify_now":
            route["notify"] = notify(lead)
    set_fields(lead["id"], "routed", {"action": action, "blocked": route.get("blocked"),
                                      "crm": [{k: c.get(k) for k in ("crm", "dry_run", "action", "error")}
                                              for c in route["crm"]]},
               route=route, status="routed")
    return route


def first_name(name: str | None) -> str | None:
    parts = (name or "").split()
    return parts[0].strip(",.;") if parts and len(parts[0].strip(",.;")) >= 2 else None


def remove_sentences(text: str, flagged: list[str]) -> str:
    out = text
    for s in flagged:
        if s in out:
            out = out.replace(s, "")
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def calendar_save(lead: dict, subject: str, body: str, note: str) -> dict:
    base = os.environ.get("CALENDAR_URL", "").rstrip("/")
    if not base:
        return {}
    try:
        r = httpx.post(f"{base}/items", headers=enrich._key_headers(), timeout=15, json={
            "title": f"Reply to lead #{lead['id']} ({lead['grade']})"[:200],
            "channel": "lead_reply", "status": "draft",
            "body": f"Subject: {subject}\n\n{body}",
            "notes": note[:1000]})
        if r.status_code == 201:
            return {"calendar_item_id": r.json()["id"]}
        return {"calendar_error": f"HTTP {r.status_code}"}
    except httpx.HTTPError as exc:
        return {"calendar_error": type(exc).__name__}


def step_reply(lead: dict, msgs: list[dict]) -> None:
    if not env_bool("REPLY_DRAFTS", True):
        return
    if lead["consent"] is None:
        set_fields(lead["id"], "reply_skipped", {"why": "no consent to follow up"})
        return
    if not msgs:
        set_fields(lead["id"], "reply_skipped", {"why": "no message to answer"})
        return
    old = lead.get("reply") or {}
    if old.get("status") in ("approved", "sent"):
        return  # a person already decided; a new message shows in the timeline
    message = msgs[-1]["message"]
    booking = os.environ.get("BOOKING_URL", "").strip()
    variables = {"message": message, "lead_first_name": first_name(lead["name"]) or ""}
    if booking:
        variables["booking_url"] = booking
    try:
        out = enrich.gateway("lead_first_reply", variables)
        check = enrich.claim_check(out["reply"], message,
                                   [f"Booking link: {booking}"] if booking else None)
    except (enrich.EnrichError, KeyError) as exc:
        set_fields(lead["id"], "reply_failed", {"why": str(exc)},
                   reply={"status": "failed", "error": str(exc), "at": now_utc()})
        return
    flagged = [c["claim"] for c in check.get("claims", []) if not c.get("supported")]
    body = remove_sentences(out["reply"], flagged)
    reply = {"status": "needs_human" if out.get("needs_human") else "drafted",
             "subject": out.get("subject", ""), "body": body, "model_reason": out.get("reason"),
             "removed": flagged, "claim_ok": not check.get("unsupported"),
             "drafted_at": now_utc(), "sent": False}
    note = (f"lead-hub #{lead['id']}: approve and send from the hub, not the publisher."
            + (f" Claim check removed {len(flagged)} sentence(s)." if flagged else ""))
    reply |= calendar_save(lead, reply["subject"], body, note) if not old.get("calendar_item_id") else {
        "calendar_item_id": old["calendar_item_id"]}
    set_fields(lead["id"], "reply_drafted", {"status": reply["status"], "removed": len(flagged)},
               reply=reply)


def process(lead_id: int, force_enrich: bool = False) -> None:
    """Enrich -> score -> route -> draft the first reply. Errors are recorded, never raised."""
    with _process_lock:
        try:
            lead = get_lead(lead_id)
            if lead is None:
                return
            step_enrich(lead, force=force_enrich)
            msgs = messages_of(lead_id)
            lead = get_lead(lead_id)
            step_score(lead, msgs)
            lead = get_lead(lead_id)
            step_route(lead, msgs)
            lead = get_lead(lead_id)
            if lead:
                step_reply(lead, msgs)
        except scoring.RulesError as exc:
            set_fields(lead_id, "process_failed", {"why": str(exc)})
        except Exception as exc:  # a background task must never die silently
            log.error("processing lead %s failed: %s", lead_id, type(exc).__name__)
            set_fields(lead_id, "process_failed", {"why": type(exc).__name__})


# ---------- erase / retention


def erase_calendar(item_id: int) -> str:
    """Overwrite the lead's reply draft in the calendar (it has no delete)."""
    base = os.environ.get("CALENDAR_URL", "").rstrip("/")
    if not base:
        return "CALENDAR_URL is empty: erase calendar item by hand"
    h = enrich._key_headers()
    try:
        item = httpx.get(f"{base}/items/{item_id}", timeout=10)
        if item.status_code == 404:
            return "gone"
        status = item.json().get("status")
        if status == "published":
            return "published: erase it by hand"
        if status == "approved":
            httpx.post(f"{base}/items/{item_id}/status", headers=h, timeout=10,
                       json={"status": "draft", "note": "lead erased"})
        r = httpx.patch(f"{base}/items/{item_id}", headers=h, timeout=10,
                        json={"title": "[erased lead reply]", "body": "[erased: the lead's data was deleted]",
                              "notes": "[erased]"})
        if status != "rejected":
            httpx.post(f"{base}/items/{item_id}/status", headers=h, timeout=10, json={"status": "rejected"})
        return "erased" if r.status_code == 200 else f"HTTP {r.status_code}"
    except httpx.HTTPError as exc:
        return type(exc).__name__


def erase(conn, lead: dict) -> dict:
    conn.execute("DELETE FROM activities WHERE lead_id = ?", (lead["id"],))
    conn.execute("DELETE FROM leads WHERE id = ?", (lead["id"],))
    crm_copies = [{k: c.get(k) for k in ("crm", "contact_id", "person_id")}
                  for c in ((lead.get("route") or {}).get("crm") or []) if not c.get("dry_run") and not c.get("error")]
    return {"id": lead["id"], "crm_copies": crm_copies,
            "calendar_item_id": (lead.get("reply") or {}).get("calendar_item_id")}


def purge_expired() -> list[int]:
    days = int(os.environ.get("RETENTION_DAYS", "365") or 0)
    if days <= 0:
        return []
    cutoff = fmt(datetime.now(timezone.utc) - timedelta(days=days))
    erased = []
    with db() as conn, write(conn):
        rows = conn.execute("SELECT * FROM leads WHERE updated_at < ?", (cutoff,)).fetchall()
        for row in rows:
            erased.append(erase(conn, row_to_lead(row)))
    for e in erased:
        if e["calendar_item_id"]:
            erase_calendar(e["calendar_item_id"])
    return [e["id"] for e in erased]


# ---------- endpoints


@app.get("/health")
def health():
    return {"status": "ok", "dry_run": crm.dry_run(), "crm": crm.configured()}


@app.post("/leads", dependencies=[Depends(require_key)])
def create_lead(req: LeadIn, tasks: BackgroundTasks):
    purge_expired()
    lead, created = capture(req)
    return capture_response(lead, created, tasks)


@app.post("/leads/webhook/{source}")
async def webhook(source: str, request: Request, tasks: BackgroundTasks):
    # Public endpoint: never hold more than the cap in memory, even for a chunked body (LH-1).
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_WEBHOOK_BYTES):
        raise HTTPException(413, "payload too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "payload too large")
    body = bytes(body)
    if not webhooks.verify(source, body, request.headers):
        raise HTTPException(401, "missing or wrong webhook secret for this source")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(422, "body is not JSON")
    if not isinstance(payload, dict):
        raise HTTPException(422, "body must be a JSON object")
    fields = webhooks.map_fields(payload)
    if fields.get("consent") and not fields["consent"].get("at"):
        fields["consent"]["at"] = now_utc()  # ticked on the form now
    try:
        req = LeadIn(source="webhook" if source.lower() not in SOURCES else source.lower(), **fields)
    except ValueError as exc:
        raise HTTPException(422, f"could not map the form to a lead: {str(exc)[:300]}")
    purge_expired()
    lead, created = capture(req)
    with db() as conn, write(conn):
        add_activity(conn, lead["id"], "webhook", source=source.lower()[:40])
    return capture_response(lead, created, tasks)


@app.get("/leads", dependencies=[Depends(require_key)])
def list_leads(grade: Literal["A", "B", "C", "D"] | None = None,
               source: str | None = None,
               status: str | None = None,
               consent: bool | None = None,
               q: str | None = Query(None, max_length=200),
               limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    where, args = [], []
    if grade:
        where.append("grade = ?"), args.append(grade)
    if source:
        where.append("sources LIKE ?"), args.append(f'%"{source}"%')
    if status:
        where.append("status = ?"), args.append(status)
    if consent is not None:
        where.append("consent_text IS NOT NULL" if consent else "consent_text IS NULL")
    if q:
        where.append("(email LIKE ? OR name LIKE ? OR company_domain LIKE ?)")
        args += [f"%{q}%"] * 3
    sql = "SELECT * FROM leads" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
    with db() as conn:
        rows = conn.execute(sql, (*args, limit, offset)).fetchall()
    return [{k: v for k, v in row_to_lead(r).items() if k not in ("enrichment", "score_detail")}
            for r in rows]


@app.get("/leads/{lead_id}", dependencies=[Depends(require_key)])
def lead_detail(lead_id: int):
    with db() as conn:
        lead = lead_or_404(conn, lead_id)
        lead["timeline"] = activities(conn, lead_id)
    return lead


@app.post("/leads/{lead_id}/process", dependencies=[Depends(require_key)])
def reprocess(lead_id: int, force_enrich: bool = False):
    with db() as conn:
        lead_or_404(conn, lead_id)
    process(lead_id, force_enrich=force_enrich)
    return lead_detail(lead_id)


@app.post("/leads/{lead_id}/consent", dependencies=[Depends(require_key)])
def change_consent(lead_id: int, req: ConsentChange):
    with db() as conn, write(conn):
        lead_or_404(conn, lead_id)
        if req.given:
            try:
                c = Consent(text=req.text or "", at=req.at or now_utc())
            except ValueError as exc:
                raise HTTPException(422, str(exc)[:300])
            conn.execute("UPDATE leads SET consent_text = ?, consent_at = ?, consent_recorded_at = ?,"
                         " updated_at = ? WHERE id = ?", (c.text, c.at, now_utc(), now_utc(), lead_id))
            add_activity(conn, lead_id, "consent_given", data={"text": c.text, "at": c.at})
        else:
            conn.execute("UPDATE leads SET consent_text = NULL, consent_at = NULL,"
                         " consent_recorded_at = NULL, updated_at = ? WHERE id = ?", (now_utc(), lead_id))
            add_activity(conn, lead_id, "consent_withdrawn")
        return lead_or_404(conn, lead_id)


@app.post("/leads/{lead_id}/reply/approve", dependencies=[Depends(require_key)])
def approve_reply(lead_id: int, req: ReplyApproval):
    with db() as conn, write(conn):
        lead = lead_or_404(conn, lead_id)
        reply = lead.get("reply") or {}
        if reply.get("status") not in ("drafted", "needs_human", "approved"):
            raise HTTPException(409, "no reply draft to approve")
        if lead["consent"] is None:
            raise HTTPException(409, "no consent recorded: this lead may not be contacted")
        edited = req.body is not None and req.body.strip() != reply.get("body")
        reply |= {"status": "approved", "approved_at": now_utc(), "approved_by": req.approved_by,
                  "edited": edited}
        if req.subject:
            reply["subject"] = req.subject.strip()
        if req.body and req.body.strip():
            reply["body"] = req.body.strip()
        conn.execute("UPDATE leads SET reply = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(reply), now_utc(), lead_id))
        add_activity(conn, lead_id, "reply_approved", data={"edited": edited, "by": req.approved_by})
        return reply


@app.post("/leads/{lead_id}/reply/send", dependencies=[Depends(require_key)])
def send_reply(lead_id: int):
    """Stub: the hub has no mail sender. With DRY_RUN it shows what would be sent."""
    with db() as conn:
        lead = lead_or_404(conn, lead_id)
    reply = lead.get("reply") or {}
    if reply.get("status") != "approved":
        raise HTTPException(409, "the reply must be approved by a person first")
    if lead["consent"] is None:
        raise HTTPException(409, "no consent recorded: this lead may not be contacted")
    if not crm.dry_run():
        raise HTTPException(501, "sending is not built: send the approved reply from your mail "
                                 "client, then POST /leads/{id}/reply/sent")
    return {"sent": False, "dry_run": True,
            "would_send": {"to": lead["email"], "subject": reply["subject"], "body": reply["body"]}}


@app.post("/leads/{lead_id}/reply/sent", dependencies=[Depends(require_key)])
def mark_sent(lead_id: int):
    with db() as conn, write(conn):
        lead = lead_or_404(conn, lead_id)
        reply = lead.get("reply") or {}
        if reply.get("status") != "approved":
            raise HTTPException(409, "only an approved reply can be marked sent")
        reply |= {"status": "sent", "sent": True, "sent_at": now_utc()}
        conn.execute("UPDATE leads SET reply = ?, status = 'replied', updated_at = ? WHERE id = ?",
                     (json.dumps(reply), now_utc(), lead_id))
        add_activity(conn, lead_id, "reply_sent")
        return reply


@app.delete("/leads/{lead_id}", dependencies=[Depends(require_key)])
def delete_lead(lead_id: int):
    """GDPR erasure: the lead, its messages, enrichment, score, route and reply draft."""
    with db() as conn, write(conn):
        result = erase(conn, lead_or_404(conn, lead_id))
    if result["calendar_item_id"]:
        result["calendar"] = erase_calendar(result["calendar_item_id"])
    result["erased"] = True
    if result["crm_copies"]:
        result["note"] = "copies were sent to the CRMs listed: delete them there too"
    return result


@app.get("/leads/{lead_id}/export", dependencies=[Depends(require_key)])
def export_lead(lead_id: int):
    """Everything the hub holds about one lead (GDPR access / portability)."""
    lead = lead_detail(lead_id)
    return {"exported_at": now_utc(), "lead": lead}


@app.get("/stats", dependencies=[Depends(require_key)])
def stats():
    with db() as conn:
        def count(sql, *a):
            return conn.execute(sql, a).fetchone()[0]
        by_grade = {r[0] or "unscored": r[1] for r in conn.execute("SELECT grade, COUNT(*) FROM leads GROUP BY grade")}
        by_status = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM leads GROUP BY status")}
        by_enrich = {r[0]: r[1] for r in conn.execute("SELECT enrich_status, COUNT(*) FROM leads GROUP BY enrich_status")}
        by_source: dict[str, int] = {}
        for (s,) in conn.execute("SELECT sources FROM leads"):
            for src in json.loads(s):
                by_source[src] = by_source.get(src, 0) + 1
        replies: dict[str, int] = {}
        for (r,) in conn.execute("SELECT reply FROM leads WHERE reply IS NOT NULL"):
            st = json.loads(r).get("status", "?")
            replies[st] = replies.get(st, 0) + 1
        return {"total": count("SELECT COUNT(*) FROM leads"),
                "with_consent": count("SELECT COUNT(*) FROM leads WHERE consent_text IS NOT NULL"),
                "by_grade": by_grade, "by_status": by_status, "by_source": by_source,
                "enrichment": by_enrich, "replies": replies, "dry_run": crm.dry_run()}


@app.get("/digest", dependencies=[Depends(require_key)])
def digest(grade: Literal["A", "B", "C", "D"] = "B", since: str | None = None):
    """Leads routed to the daily digest (grade B by default) since `since` (default 24 h)."""
    try:
        start = to_utc_iso(since) if since else fmt(datetime.now(timezone.utc) - timedelta(days=1))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with db() as conn:
        rows = conn.execute("SELECT * FROM leads WHERE grade = ? AND updated_at >= ? ORDER BY score DESC",
                            (grade, start)).fetchall()
    out = []
    for r in rows:
        lead = row_to_lead(r)
        out.append({"id": lead["id"], "name": lead["name"], "company_domain": lead["company_domain"],
                    "score": lead["score"], "grade": lead["grade"], "consent": lead["consent"] is not None,
                    "reasons": [x["reason"] for x in (lead["score_detail"] or {}).get("reasons", [])],
                    "reply_status": (lead["reply"] or {}).get("status")})
    return {"since": start, "grade": grade, "leads": out}


@app.post("/admin/purge", dependencies=[Depends(require_key)])
def purge():
    return {"erased": purge_expired(), "retention_days": int(os.environ.get("RETENTION_DAYS", "365") or 0)}


@app.get("/scoring/rules", dependencies=[Depends(require_key)])
def get_rules():
    try:
        return scoring.load_rules()
    except scoring.RulesError as exc:
        raise HTTPException(500, str(exc))
