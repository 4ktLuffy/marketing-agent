"""Client approval links (Phase 2, agencies).

The agency makes a link for some items; the client opens it (through the control room, 72) and
enters a 6-digit PIN that was sent by another route. With both, the client can read those items
as they are now and answer each one: "approve" (recorded, the status does not change; the
agency's own approval stays the one that counts) or "changes" (recorded, and an item in review
or approved goes back to draft so it can't go out as it is).

Only hashes are stored: sha256 of the token, and an HMAC of the PIN keyed with the raw token (so
the database alone can't be used to try PINs). Five wrong PINs lock the link for good; the agency
makes a new one. Every answer is an append-only audit row with the hash of the text the client saw.
All endpoints need X-API-Key: only the control room calls them.
"""
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, StrictInt, field_validator

from .main import (actor_of, add_audit, append_note, db, fmt, get_or_404, now_utc, require_key,
                   update)

router = APIRouter(dependencies=[Depends(require_key)])

MAX_WRONG_PINS = 5
MAX_ITEMS = 50
TOKEN = re.compile(r"^[A-Za-z0-9_-]{40,64}$")
PIN = re.compile(r"^\d{6}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
OPEN_FOR_ANSWERS = {"idea", "draft", "in_review", "approved"}
BACK_TO_DRAFT = {"in_review", "approved"}   # a client's change request stops these going out as they are
ACTIONS = {"approve": "client_approved", "changes": "client_changes_requested"}
LINK_COLUMNS = ("id", "label", "item_ids", "expires_at", "created_by", "created_at", "revoked_at", "uses",
                "wrong_pins")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pin_hash(token: str, pin: str) -> str:
    return hmac.new(token.encode(), pin.encode(), hashlib.sha256).hexdigest()


def one_line(v: str) -> str:
    """Whitespace collapsed, control characters and "|" (the notes' segment separator) removed."""
    v = "".join(ch for ch in v if ch.isprintable() or ch.isspace()).replace("|", "/")
    return " ".join(v.split())


def state_of(row) -> str:
    if row["revoked_at"]:
        return "revoked"
    if row["expires_at"] <= now_utc():
        return "expired"
    if row["wrong_pins"] >= MAX_WRONG_PINS:
        return "locked"
    return "active"


def link_view(row) -> dict:
    out = {k: row[k] for k in LINK_COLUMNS}
    out["item_ids"] = json.loads(row["item_ids"])
    out["state"] = state_of(row)
    return out


# ---------- models


class NewLink(BaseModel):
    item_ids: list[StrictInt] = Field(min_length=1, max_length=MAX_ITEMS)
    label: str = Field(default="", max_length=80)
    days: StrictInt = Field(default=7, ge=1, le=30)
    pin: str

    @field_validator("pin")
    @classmethod
    def six_digits(cls, v: str) -> str:
        if not PIN.match(v):
            raise ValueError("pin: exactly 6 digits")
        return v

    @field_validator("label")
    @classmethod
    def label_line(cls, v: str) -> str:
        return one_line(v)


class Opening(BaseModel):
    token: str
    pin: str

    @field_validator("token")
    @classmethod
    def token_shape(cls, v: str) -> str:
        if not TOKEN.match(v):
            raise ValueError("token: 40-64 characters A-Z a-z 0-9 _ -")
        return v

    @field_validator("pin")
    @classmethod
    def pin_shape(cls, v: str) -> str:
        if not PIN.match(v):
            raise ValueError("pin: exactly 6 digits")
        return v


class Answer(Opening):
    item_id: StrictInt
    body_sha256: str
    decision: str
    name: str
    comment: str = Field(default="", max_length=2000)

    @field_validator("body_sha256")
    @classmethod
    def sha(cls, v: str) -> str:
        if not HEX64.match(v):
            raise ValueError("body_sha256: 64 hex characters")
        return v

    @field_validator("decision")
    @classmethod
    def known(cls, v: str) -> str:
        if v not in ACTIONS:
            raise ValueError("decision: approve or changes")
        return v

    @field_validator("name")
    @classmethod
    def who(cls, v: str) -> str:
        v = one_line(v)
        if not v:
            raise ValueError("name: say who you are")
        if len(v) > 80:
            raise ValueError("name: at most 80 characters")
        return v

    @field_validator("comment")
    @classmethod
    def comment_line(cls, v: str) -> str:
        return one_line(v)


# ---------- opening a link (token + PIN)


def open_link(conn, token: str, pin: str):
    """The link row, or an HTTPException. A wrong PIN is counted and committed before raising."""
    row = conn.execute("SELECT * FROM client_links WHERE token_sha256 = ?", (token_hash(token),)).fetchone()
    if row is None:
        raise HTTPException(404, "no such link")
    state = state_of(row)
    if state == "revoked":
        raise HTTPException(410, "this link was withdrawn")
    if state == "expired":
        raise HTTPException(410, "this link has expired")
    if state == "locked":
        raise HTTPException(409, "this link is locked after too many wrong PINs")
    if not hmac.compare_digest(pin_hash(token, pin), row["pin_sha256"]):
        wrong = row["wrong_pins"] + 1
        conn.execute("UPDATE client_links SET wrong_pins = ? WHERE id = ?", (wrong, row["id"]))
        conn.commit()
        if wrong >= MAX_WRONG_PINS:
            raise HTTPException(409, "this link is locked after too many wrong PINs")
        left = MAX_WRONG_PINS - wrong
        raise HTTPException(403, {"message": f"wrong PIN: {left} tries left", "tries_left": left})
    if row["wrong_pins"]:
        conn.execute("UPDATE client_links SET wrong_pins = 0 WHERE id = ?", (row["id"],))
    return row


def responses(conn, item_id: int, link_id: int) -> list[dict]:
    """The answers given through this link (not through other links) for one item."""
    rows = conn.execute(
        "SELECT a.at, a.actor, a.action, a.body_sha256,"
        " (SELECT MAX(n) FROM item_versions v WHERE v.item_id = a.item_id AND v.body_sha256 = a.body_sha256) AS version"
        " FROM audit a WHERE a.item_id = ? AND a.action IN ('client_approved', 'client_changes_requested')"
        " AND (a.detail = ? OR a.detail LIKE ?) ORDER BY a.id",
        (item_id, f"via client link #{link_id}", f"via client link #{link_id}: %"))
    return [{"at": r["at"], "name": r["actor"].removeprefix("client:"), "action": r["action"],
             "body_sha256": r["body_sha256"], "version": r["version"]} for r in rows]


# ---------- endpoints


@router.post("/client-links", status_code=201)
def create_link(req: NewLink, x_actor: str | None = Header(default=None)):
    ids = list(dict.fromkeys(req.item_ids))
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with db() as conn:
        for i in ids:
            get_or_404(conn, i)
        cur = conn.execute(
            "INSERT INTO client_links (token_sha256, label, item_ids, pin_sha256, expires_at, created_by,"
            " created_at, uses, wrong_pins) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (token_hash(token), req.label, json.dumps(ids), pin_hash(token, req.pin),
             fmt(now + timedelta(days=req.days)), actor_of(x_actor), fmt(now)))
        row = conn.execute("SELECT * FROM client_links WHERE id = ?", (cur.lastrowid,)).fetchone()
        # The only time the token leaves this service. The PIN is not returned: the caller chose it.
        return {**link_view(row), "days": req.days, "token": token}


@router.get("/client-links")
def list_links():
    with db() as conn:
        return [link_view(r) for r in conn.execute("SELECT * FROM client_links ORDER BY id DESC LIMIT 200")]


@router.post("/client-links/resolve")
def resolve(req: Opening):
    with db() as conn:
        row = open_link(conn, req.token, req.pin)
        conn.execute("UPDATE client_links SET uses = uses + 1 WHERE id = ?", (row["id"],))
        items = []
        for i in json.loads(row["item_ids"]):
            try:
                it = get_or_404(conn, i)
            except HTTPException:
                continue
            items.append({k: it[k] for k in ("id", "title", "channel", "body", "body_sha256", "version", "status",
                                             "scheduled_at", "image_url", "video_url", "link", "origin", "notes")}
                         | {"client_responses": responses(conn, i, row["id"])})
        return {"id": row["id"], "label": row["label"], "expires_at": row["expires_at"], "items": items}


@router.post("/client-links/respond")
def respond(req: Answer):
    if req.decision == "changes" and not req.comment:
        raise HTTPException(422, "say what should change (comment)")
    with db() as conn:
        row = open_link(conn, req.token, req.pin)
        if req.item_id not in json.loads(row["item_ids"]):
            raise HTTPException(404, "this post is not in this link")
        item = get_or_404(conn, req.item_id)
        if item["status"] not in OPEN_FOR_ANSWERS:
            raise HTTPException(409, f"this post is {item['status']}; it is no longer open for answers")
        if req.body_sha256 != item["body_sha256"]:
            raise HTTPException(409, {"message": "the text changed since the link was opened",
                                      "current_sha256": item["body_sha256"]})
        ts, v, current = now_utc(), item["version"], item["status"]
        if req.decision == "approve":
            line, fields = f"[{ts}] client approved by {req.name} (v{v})", {}
        else:
            said = f"client requested changes ({req.name}, v{v}): {req.comment}"
            if current in BACK_TO_DRAFT:
                line, fields = f"[{ts}] {current} -> draft: {said}", {"status": "draft"}
            else:
                line, fields = f"[{ts}] {said}", {}
        new = update(conn, item["id"], {**fields, "notes": append_note(item["notes"], line)})
        detail = f"via client link #{row['id']}" + (f": {req.comment}" if req.comment else "")
        add_audit(conn, new, f"client:{req.name}", ACTIONS[req.decision], current, detail)
        return {"item_id": new["id"], "decision": req.decision, "status": new["status"], "version": v,
                "body_sha256": new["body_sha256"]}


@router.post("/client-links/{link_id}/revoke")
def revoke(link_id: int):
    with db() as conn:
        if not 0 < link_id < 2**63:
            raise HTTPException(404, "no such link")
        row = conn.execute("SELECT * FROM client_links WHERE id = ?", (link_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such link")
        if not row["revoked_at"]:
            conn.execute("UPDATE client_links SET revoked_at = ? WHERE id = ?", (now_utc(), link_id))
        return link_view(conn.execute("SELECT * FROM client_links WHERE id = ?", (link_id,)).fetchone())
