"""Task bridge: any business, any chatbot (free plans too), facts you can trust. No model needed.

POST /tasks builds a plain-text task pack from the confirmed facts in the brand service (05):
public facts as text plus a [[slot]], internal facts only as a slot, restricted facts never, with a
preview of exactly what leaves the business. The person pastes the pack into any chatbot and pastes
the answer back. The answer is split into pieces, slots are filled from the pack's snapshot, and
every sentence gets an evidence label (match / wrong scope / conflicting or expired / no source /
forbidden phrase / missing disclosure) against the facts on the PUBLISH date. Each piece becomes a
content-calendar (19) item that needs a version-bound approval. The export refuses until every
piece is approved with the exact text that was checked and every fact is still valid.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import arith, evidence, extras, pack, paste, quote, selfaudit, services, slots, templates_render
from . import local as L
from . import facts as F
from .db import approved_examples, db, event, meta_get, meta_set, now, write

# ---------- settings


def env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name) or default)
    except ValueError:
        return default
    return min(max(v, lo), hi)


def pack_max_chars() -> int:
    return env_int("PACK_MAX_CHARS", 8000, 2000, 100_000)


def pack_target_chars() -> int:
    """Free chatbots want short packs: fill by relevance up to this, never past PACK_MAX_CHARS."""
    return env_int("PACK_TARGET_CHARS", 5000, 1000, 100_000)


def pack_examples_on() -> bool:
    """PACK_EXAMPLES=on|off (default on): approved, exported pieces of this business as "write like these"."""
    return (os.environ.get("PACK_EXAMPLES") or "on").strip().lower() not in ("off", "0", "false", "no")


def paste_max_chars() -> int:
    return env_int("PASTE_MAX_CHARS", 40_000, 1000, 200_000)


def reconcile_minutes() -> int:
    return env_int("RECONCILE_MIN", 10, 0, 1440)


def rate_per_min() -> int:
    return env_int("RATE_PER_MIN", 60, 1, 10_000)


# ---------- background reconcile

_stop = threading.Event()
_reconcile_lock = threading.Lock()


def _loop():
    while not _stop.wait(reconcile_minutes() * 60):
        try:
            reconcile_run()
        except Exception:  # noqa: BLE001 - the loop must survive any one failed run
            pass


@asynccontextmanager
async def lifespan(_app):
    t = None
    if reconcile_minutes() > 0 and services.brand_url() and services.calendar_url():
        _stop.clear()
        t = threading.Thread(target=_loop, name="reconcile", daemon=True)
        t.start()
    yield
    _stop.set()


app = FastAPI(title="task-bridge", lifespan=lifespan)

# ---------- auth and limits


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


_hits: deque = deque()
_hits_lock = threading.Lock()


def rate_limit():
    """Paste, submit and check do real work: at most RATE_PER_MIN of them per minute."""
    t = time.monotonic()
    with _hits_lock:
        while _hits and t - _hits[0] > 60:
            _hits.popleft()
        if len(_hits) >= rate_per_min():
            raise HTTPException(429, "too many requests; try again in a minute")
        _hits.append(t)


def upstream(exc: services.ServiceError) -> HTTPException:
    msg = str(exc)
    return HTTPException(503 if "is not set" in msg else 502, msg)


def canonical_sha256(body: str) -> str:
    return hashlib.sha256(body.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


# ---------- models

DIM_ITEM = Field(default_factory=list, max_length=20)


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sites: list[str] = DIM_ITEM
    regions: list[str] = DIM_ITEM
    channels: list[str] = DIM_ITEM
    segments: list[str] = DIM_ITEM
    plan_tiers: list[str] = DIM_ITEM
    variants: list[str] = DIM_ITEM

    @field_validator("*")
    @classmethod
    def short(cls, v: list[str]) -> list[str]:
        out = []
        for s in v:
            s = " ".join(str(s).split())
            if not s or len(s) > 80:
                raise ValueError("each scope value is 1 to 80 characters")
            out.append(s)
        return out


class PieceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z0-9_-]{1,20}$")
    channel: str = Field(min_length=1, max_length=30)
    kind: str | None = Field(default=None, max_length=40)
    max_chars: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("channel")
    @classmethod
    def chan(cls, v: str) -> str:
        v = v.strip().lower().replace(" ", "_").replace("-", "_")
        if not v or not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("channel is letters, digits and _")
        return v


def _day_str(v: str) -> str:
    try:
        return date.fromisoformat(v).isoformat()
    except (TypeError, ValueError):
        raise ValueError("use YYYY-MM-DD") from None


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=3, max_length=2000)
    pieces: list[PieceIn] = Field(min_length=1, max_length=10)
    scope: Scope = Field(default_factory=Scope)
    publish_on: str
    audience: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("publish_on")
    @classmethod
    def day(cls, v):
        return _day_str(v)

    @field_validator("pieces")
    @classmethod
    def unique(cls, v):
        keys = [p.key for p in v]
        if len(set(keys)) != len(keys):
            raise ValueError("piece keys must be unique")
        return v


class PasteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=200_000)
    provider: Literal["chatgpt", "claude", "gemini", "other", "self", "template"] = "other"


class ManualPiece(BaseModel):
    piece_key: str = Field(max_length=20)
    text: str = Field(max_length=200_000)


class ManualSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pieces: list[ManualPiece] = Field(min_length=1, max_length=10)


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: int = Field(ge=1)


# findings a person may accept ("it's there, in other words"); facts that are wrong, expired, out of
# scope or a blocked slot can never be waved through
ACCEPTABLE = {"missing_disclosure"}


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding: int = Field(ge=0, le=500)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    by: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=3, max_length=300)
    # optional: the exact words in the text that say the disclosure. Proposed to 05 as the
    # business's own wording; it counts on later checks only after the owner confirms it there.
    wording: str | None = Field(default=None, min_length=3, max_length=200)


class CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=200_000)
    scope: Scope = Field(default_factory=Scope)
    publish_on: str | None = None
    channel: str | None = Field(default=None, max_length=30)

    @field_validator("publish_on")
    @classmethod
    def day(cls, v):
        return _day_str(v) if v else None


# ---------- helpers


def new_task_id(conn) -> str:
    for _ in range(20):
        tid = "T-" + base64.b32encode(secrets.token_bytes(5)).decode()[:6]
        if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (tid,)).fetchone():
            return tid
    raise HTTPException(500, "could not make a task id")


def task_or_404(conn, task_id: str) -> dict:
    r = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if r is None:
        raise HTTPException(404, f"task {task_id} not found")
    t = dict(r)
    for k in ("pieces", "scope", "snapshot", "snapshot_facts", "share_preview"):
        t[k] = json.loads(t[k])
    return t


def piece_scope(task_scope: dict, channel: str | None, all_channels: list[str] | None = None) -> dict:
    """The task scope, with the channel dimension named when the task left it empty: the piece's
    own channel for checking a piece, every piece channel for the pack."""
    s = F.norm_scope(task_scope)
    if not s["channels"]:
        s["channels"] = [channel] if channel else list(dict.fromkeys(all_channels or []))
    return s


def known_facts(query: dict, everything: list[dict]) -> dict[str, dict]:
    known = {f["key"]: f for f in everything if (f.get("status") or "active") != "draft"}
    for f in query.get("facts") or []:
        if isinstance(f, dict) and f.get("key"):
            known[f["key"]] = f
    return known


def _one_line(s: str, n: int = 200) -> str:
    s = " ".join(str(s).replace(";", ",").replace("|", "/").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def notes_for(task: dict, piece: dict, findings: list[dict], brand: list[dict], platform: list[dict],
              skipped: list[str], sha: str) -> str:
    """Lines in the prefixes the control room (72) already flags."""
    lines = [f"task-bridge: task {task['id']} piece {piece['key']} · facts {task['fact_set_version']} · "
             f"publish {task['publish_on']} · sha256 {sha[:12]}"]
    unsupported = [f"{_one_line(x['sentence'], 120)} ({x['label']}: {_one_line(x['detail'], 120)})"
                   for x in findings if x["blocking"] and x["label"] != "slot_blocked"]
    gate = [_one_line(x["detail"], 160) for x in findings if x["label"] == "slot_blocked"]
    gate += [_one_line(f"brand: {v.get('detail') or v.get('rule')}") for v in brand if v.get("severity") == "error"]
    gate += [_one_line(f"platform: {v.get('detail') or v.get('rule')}") for v in platform if v.get("severity") == "error"]
    warns = [f"{_one_line(x['sentence'], 100)} ({x['label']}: {_one_line(x['detail'], 100)})"
             for x in findings if not x["blocking"] and x["label"] != "match"]
    warns += [_one_line(f"brand: {v.get('detail') or v.get('rule')}") for v in brand if v.get("severity") != "error"]
    warns += [_one_line(f"platform: {v.get('detail') or v.get('rule')}") for v in platform if v.get("severity") != "error"]
    warns += [_one_line(s) for s in skipped]
    if unsupported:
        lines.append("unsupported claim: " + "; ".join(unsupported[:10]))
    if gate:
        lines.append("quality gate: " + "; ".join(gate[:10]))
    if warns:
        lines.append("warnings: " + "; ".join(warns[:10]))
    return "\n".join(lines)[:4000]


def model_findings(text: str, fact_lines: list[str], findings: list[dict]) -> tuple[list[dict], str | None]:
    """44 may only ADD non-blocking review / no_source findings, never remove or unblock."""
    claims, note = services.model_check(text, fact_lines)
    covered = {x["sentence"] for x in findings if x["blocking"]}
    out = []
    for c in claims:
        sentence = str(c.get("claim") or "").strip()
        if not sentence or sentence in covered or sentence not in text:
            continue
        reasons = [str(r) for r in c.get("reasons") or []]
        label = "no_source" if any("number" in r for r in reasons) else "review"
        ev = c.get("evidence") or []
        quote = ev[0].get("quote") if ev and isinstance(ev[0], dict) else None
        out.append({"sentence": sentence[:500], "label": label, "fact_key": None, "quote": quote, "blocking": False,
                    "detail": "model check: " + ("; ".join(reasons)[:300] or "not supported")})
    return out, note


# ---------- MODEL_CHECK=review: a narrow second look, only where the rules are unsure

# Offer / scope / claim cues: a sentence with none of these and no rule finding is not sent.
_NUMWORD = (r"two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|"
            r"sixty|seventy|eighty|ninety|hundred|thousand|million|half|dozen|double|twice")
REVIEW_CUE = re.compile(
    r"\d|[£$€%]|\b(?:" + _NUMWORD + r")\b|\b(?:pounds?|quid|pence|dollars?|euros?|cents?|per\s*cent|percent" + "".join("|" + w for w in evidence._LATIN_WORDS) + r")\b|"
    r"\bfree\b|\bon\s+(?:us|the\s+house)\b|\b(?:for|4)\s+(?:one|1|the\s+price\s+of)\b|\bbogo\b|\bhalf[\s-]+price\b|"
    r"\bdiscount|\bsave\b|\boff\s+(?:your|the|all|every|any)\b|\bcheapest\b|\bguarantee|"
    r"\b(?:every|all|any|each)\s+(?:(?:of\s+)?(?:our|the)\s+)?(?:branch|shop|store|site|location|clinic|outlet|"
    r"venue|office|depot|restaurant|pub|salon|studio|centre|center)(?:e?s)?\b|"
    r"\b24\s*/\s*7\b|\bround[\s-]the[\s-]clock\b|\b(?:day\s+and\s+night|night\s+and\s+day)\b|\bweekends?\b|"
    r"\bevery\s+day\b|\b(?:award|awarded|award-winning|rated|voted|best|certified|accredited|approved|"
    r"licensed|leading|trusted|official)\b|\bno\.?\s*1\b|\bnumber\s+one\b|#1\b|"
    r"\bsince\s+(?:19|20)\d\d\b|\b\d[\d,]*\+?\s*(?:customers|clients|patients|members|users|households|"
    r"businesses|families|people)\b", re.I)
REVIEW_MAX = 12


def _covers(a: str, b: str) -> bool:
    a, b = " ".join(a.split()).lower(), " ".join(b.split()).lower()
    return bool(a and b) and (a in b or b in a)


def review_sentences(text: str, findings: list[dict]) -> list[str]:
    """Sentences for MODEL_CHECK=review, at most REVIEW_MAX: (a) a sentence with a non-blocking
    `review` finding and nothing blocking, then (b) a sentence with no finding at all
    that carries an offer / scope / claim cue. Blocking or matched sentences are never sent."""
    first, second = [], []
    for a, b in F.sentence_spans(text):
        s = text[a:b].strip()
        if not re.search(r"[A-Za-z]{3}", s) or s.endswith("?"):
            continue
        mine = [x for x in findings if _covers(str(x.get("sentence") or ""), s)]
        if not mine:
            if REVIEW_CUE.search(s):
                second.append(s)
        elif any(x["label"] == "review" for x in mine) and not any(x["blocking"] for x in mine):
            first.append(s)
    return list(dict.fromkeys(first + second))[:REVIEW_MAX]


def _day_words(v) -> str:
    try:
        d = date.fromisoformat(str(v)[:10])
    except ValueError:
        return ""
    return d.strftime("%d %B %Y").lstrip("0")


def review_fact_lines(fact_lines: list[str], known: dict) -> list[str]:
    """The pack's public lines, each with its validity dates and scope, so the model can see that
    an offer ends or covers one site. Lines stay public: nothing is added that the pack withheld."""
    out = []
    for line in fact_lines:
        m = re.search(r"\[\[([a-z0-9-]+)\]\]", line)
        f = known.get(m.group(1)) if m else None
        extra = []
        if f:
            if f.get("valid_from"):
                extra.append(f"valid from {_day_words(f['valid_from'])}")
            if f.get("valid_to"):
                extra.append(f"valid until {_day_words(f['valid_to'])}")
            if F.scope_text(f):
                extra.append(f"applies only to {F.scope_text(f)}")
        out.append((line + (" (" + "; ".join(extra) + ")" if extra else ""))[:500])
    return out


def review_findings(text: str, fact_lines: list[str], findings: list[dict], known: dict) -> tuple[list[dict], str | None]:
    """44 may only ADD one non-blocking `review` finding per chosen sentence: never block, never
    remove or change a rule finding."""
    chosen = review_sentences(text, findings)
    if not chosen:
        return [], None
    claims, note = services.model_review(chosen, review_fact_lines(fact_lines, known))
    out, seen = [], set()
    for c in claims:
        claim = str(c.get("claim") or "").strip()
        s = next((x for x in chosen if _covers(claim, x)), None)
        if not s or s in seen:
            continue
        seen.add(s)
        reasons = [str(r) for r in c.get("reasons") or []]
        out.append({"sentence": s[:500], "label": "review", "fact_key": None, "quote": None, "blocking": False,
                    "detail": "model check: " + ("; ".join(reasons)[:300] or "not supported")})
    return out, note


def _amount_of(detail: str) -> str | None:
    """The amount a price finding is about ("12,480 kora differs from …" -> "12480")."""
    m = re.match(r"\s*[$£€]?\s?(\d[\d,]*(?:\.\d+)?)", detail)
    if not m:
        return None
    from decimal import Decimal, InvalidOperation
    try:
        return str(Decimal(m.group(1).replace(",", "")).normalize())
    except InvalidOperation:
        return None


def _self_flagged(text: str, listed: list[str], findings: list[dict]) -> list[dict]:
    """The chatbot's own "NOT IN FACTS" sentences, on sentences no blocking finding has already."""
    if not listed or not selfaudit.on():     # the list is always cut off the answer; it blocks only when on
        return []
    flagged = {f["sentence"] for f in findings if f.get("blocking")}
    sents = [text[a:b] for a, b in F.sentence_spans(text)]
    return [x for x in selfaudit.findings(text, listed, sents) if x["sentence"] not in flagged]


def check_piece(text: str, snapshot: dict, known: dict, day: date, scope: dict, fact_lines: list[str]):
    fill = slots.fill(text, snapshot, known, day, scope)
    found, used = evidence.check_text(fill.text, list(known.values()), day, scope, fill.used)
    sums = arith.check(fill.text, list(known.values()), day, scope)
    # real-10: "Six crates at 2,080 kora per crate cost 12,480 kora": a total the arithmetic proves is not a
    # wrong price for the product
    for m in [x for x in sums if x["label"] == "match" and x.get("total_amount")]:
        found = [f for f in found if not (f["sentence"] == m["sentence"] and f["label"] == "conflict_or_expired"
                                          and _amount_of(f.get("detail") or "") == _amount_of(m["total_amount"]))]
    findings = fill.findings + found + sums
    if extras_on():
        # a product the business doesn't carry ("Corvo 33cl at 1,450 kora"): say that, not "wrong price"
        for u in extras.unknown_products(fill.text, list(known.values())):
            findings = [f for f in findings if not (f["sentence"] == u["sentence"] and f["label"] == "conflict_or_expired")] + [u]
        flagged = {f["sentence"] for f in findings if f.get("blocking")}
        findings += [x for x in extras.check(fill.text, list(known.values()), day, scope) if x["sentence"] not in flagged]
    if services.model_check_mode() == "review":
        extra, note = review_findings(fill.text, fact_lines, findings, known)
    else:
        extra, note = model_findings(fill.text, fact_lines, findings)
    return fill, findings + extra, used, note


# ---------- endpoints


def pick_examples(conn, task_id: str, pieces: list[dict], scope: dict, publish_on: str, q: dict,
                  everything: list[dict] | None) -> list[dict]:
    """Up to 2 short approved examples for this task's channels, each re-checked against the facts valid
    on this task's publish date and scope. Same channel first, then same family, newest first; an
    example with a blocking finding, a slot, or an internal/restricted value is skipped. Never raises."""
    if not pack_examples_on() or everything is None:
        return []
    try:
        day = F.parse_day(publish_on)
        task_channels = [p["channel"] for p in pieces]
        ranked = []
        for i, c in enumerate(approved_examples(conn, task_id)):
            rk = pack.example_rank(c["channel"], task_channels)
            if rk is not None:
                ranked.append((rk, i, c))
        ranked.sort(key=lambda x: (x[0], x[1]))
        known = known_facts(q, everything)
        secret = [f for f in list(everything) + [f for f in q.get("facts") or [] if isinstance(f, dict)]
                  if (f.get("sensitivity") or "public") != "public"]
        out, texts = [], set()
        for tier in (0, 1):             # same channel first; the family only when no same-channel one qualifies
            for _rk, _i, c in [r for r in ranked if r[0] == tier][:20]:
                text = pack.trim_example(c["text"])
                if not text or text in texts or not pack.example_safe(text, secret):
                    continue
                sc = piece_scope(scope, c["channel"])
                found, _used = evidence.check_text(text, list(known.values()), day, sc, [])
                if evidence.blocked(found):
                    continue
                texts.add(text)
                out.append({"task_id": c["task_id"], "piece_key": c["piece_key"], "channel": c["channel"],
                            "text": text})
                if len(out) >= pack.EXAMPLES_MAX:
                    break
            if out:
                break
        return out
    except Exception:       # examples are a bonus: never stop a task from being created
        return []


@app.get("/health")
def health():
    return {"status": "ok", "brand": bool(services.brand_url()), "calendar": bool(services.calendar_url()),
            "rules": bool(services.rules_url()), "model_check": services.model_check_on(),
            "model_check_mode": services.model_check_mode(),
            "pack_examples": pack_examples_on(), "leads": bool(services.leads_url()), "pack_max_chars": pack_max_chars(), "pack_target_chars": pack_target_chars(),
            "paste_max_chars": paste_max_chars(), "reconcile_min": reconcile_minutes()}


@app.post("/tasks", status_code=201, dependencies=[Depends(require_key)])
def create_task(req: TaskIn):
    pieces = [p.model_dump() for p in req.pieces]
    scope = req.scope.model_dump()
    qscope = piece_scope(scope, None, [p["channel"] for p in pieces])
    try:
        q = services.query_facts(qscope, req.publish_on)
    except services.ServiceError as exc:
        raise upstream(exc) from None
    notes = []
    try:
        everything = services.all_facts()
    except services.ServiceError as exc:
        everything = None
        notes.append(f"voice left out: cannot list all facts to keep internal values out of it ({exc})")
    voice, n1 = services.profile_summary() if everything is not None else (None, None)
    rules, n2 = services.channel_rules()
    kit, n3 = services.kit_rules()
    notes += [n for n in (n1, n2, n3) if n]
    with db() as conn, write(conn):
        tid = new_task_id(conn)
        examples = pick_examples(conn, tid, pieces, qscope, req.publish_on, q, everything)
        try:
            built = pack.build(tid, str(q["fact_set_version"]), req.publish_on, req.goal, req.audience, req.notes,
                               pieces, qscope, [f for f in q["facts"] if isinstance(f, dict) and f.get("key")],
                               [e for e in q["excluded"] if isinstance(e, dict)], everything or [], voice, rules,
                               pack_max_chars(), pack_target_chars(), calendar_lines(req.publish_on), examples, kit)
        except pack.PackTooLong as exc:
            raise HTTPException(422, str(exc)) from None
        preview = {"sent": built.sent, "slotted": built.slotted, "withheld": built.withheld, "chars": len(built.text),
                   "examples": built.examples}
        ts = now()
        conn.execute(
            "INSERT INTO tasks (id, goal, audience, notes, pieces, scope, publish_on, pack, pack_sha256,"
            " fact_set_version, snapshot, snapshot_facts, share_preview, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (tid, req.goal, req.audience, req.notes, json.dumps(pieces), json.dumps(scope), req.publish_on,
             built.text, pack.sha256(built.text), str(q["fact_set_version"]), json.dumps(built.snapshot),
             json.dumps(built.snapshot_facts), json.dumps(preview), ts, ts))
        for i, p in enumerate(pieces, 1):
            conn.execute("INSERT INTO pieces (task_id, piece_key, n, channel, updated_at) VALUES (?, ?, ?, ?, ?)",
                         (tid, p["key"], i, p["channel"], ts))
        event(conn, tid, "created", pack_sha256=pack.sha256(built.text), chars=len(built.text),
              sent=len(built.sent), examples=[f"{e['task_id']}/{e['piece_key']}" for e in built.examples],
              slotted=built.slotted, withheld=[w["key"] for w in built.withheld])
    return {"id": tid, "pack": built.text, "pack_sha256": pack.sha256(built.text),
            "fact_set_version": str(q["fact_set_version"]), "snapshot": built.snapshot,
            "share_preview": preview, "status": "open", "notes": notes}


@app.get("/tasks", dependencies=[Depends(require_key)])
def list_tasks(status: str | None = Query(None, max_length=20), limit: int = Query(50, ge=1, le=500)):
    sql, args = "SELECT id, goal, publish_on, status, fact_set_version, pieces, created_at FROM tasks", []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    sql += " ORDER BY created_at DESC, id LIMIT ?"
    args.append(limit)
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            blocked = conn.execute("SELECT COUNT(*) FROM pieces WHERE task_id = ? AND blocked = 1", (r["id"],)).fetchone()[0]
            out.append({"id": r["id"], "goal": r["goal"][:200], "publish_on": r["publish_on"], "status": r["status"],
                        "fact_set_version": r["fact_set_version"], "pieces": len(json.loads(r["pieces"])),
                        "blocked_pieces": blocked, "created_at": r["created_at"]})
    return out


def _pieces_out(conn, task_id: str) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM pieces WHERE task_id = ? ORDER BY n", (task_id,)):
        out.append({"piece_key": r["piece_key"], "n": r["n"], "channel": r["channel"], "state": r["state"],
                    "draft_id": r["draft_id"], "filled_text": r["filled_text"], "filled_sha256": r["filled_sha256"],
                    "blocked": bool(r["blocked"]), "findings": json.loads(r["findings"]),
                    "checks": json.loads(r["checks"]), "used_facts": json.loads(r["used_facts"]),
                    "calendar_item_id": r["item_id"], "stale_facts": json.loads(r["stale"])})
    return out


@app.get("/tasks/{task_id}", dependencies=[Depends(require_key)])
def get_task(task_id: str):
    with db() as conn:
        t = task_or_404(conn, task_id)
        drafts = [{"draft_id": r["id"], "kind": r["kind"], "provider": r["provider"], "chars": r["raw_chars"],
                   "problems": json.loads(r["problems"]), "parent_id": r["parent_id"], "created_at": r["created_at"]}
                  for r in conn.execute("SELECT * FROM drafts WHERE task_id = ? ORDER BY id", (task_id,))]
        exports = [{"id": r["id"], "format": r["format"], "sha256": r["sha256"], "created_at": r["created_at"]}
                   for r in conn.execute("SELECT * FROM exports WHERE task_id = ? ORDER BY id", (task_id,))]
        events = [{"at": r["at"], "kind": r["kind"], "detail": json.loads(r["detail"])}
                  for r in conn.execute("SELECT * FROM events WHERE task_id = ? ORDER BY id", (task_id,))]
        pieces = _pieces_out(conn, task_id)
    t.pop("snapshot_facts")
    return {**t, "piece_state": pieces, "drafts": drafts, "exports": exports, "events": events,
            "export": export_state(t, pieces)}


@app.get("/tasks/{task_id}/pack", dependencies=[Depends(require_key)])
def get_pack(task_id: str, format: Literal["json", "txt"] = "json"):
    with db() as conn:
        t = task_or_404(conn, task_id)
    if format == "txt":
        return PlainTextResponse(t["pack"], headers={"Cache-Control": "no-store"})
    return {"id": t["id"], "pack": t["pack"], "pack_sha256": t["pack_sha256"],
            "fact_set_version": t["fact_set_version"], "share_preview": t["share_preview"]}


def _store_draft(conn, task_id, kind, provider, raw, split, problems, parent_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO drafts (task_id, parent_id, kind, provider, raw_text, raw_sha256, raw_chars, split, problems,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, parent_id, kind, provider, raw, pack.sha256(raw), len(raw), json.dumps(split),
         json.dumps(problems), now()))
    return cur.lastrowid


@app.post("/tasks/{task_id}/paste", status_code=201, dependencies=[Depends(require_key), Depends(rate_limit)])
def paste_answer(task_id: str, req: PasteIn):
    if len(req.text) > paste_max_chars():
        raise HTTPException(413, f"the pasted text is longer than {paste_max_chars()} characters")
    if not req.text.strip():
        raise HTTPException(422, "the pasted text is empty")
    with db() as conn, write(conn):
        t = task_or_404(conn, task_id)
        answer, listed = selfaudit.extract(req.text)     # the chatbot's own "NOT IN FACTS" list
        res = paste.split(answer, t["pieces"])
        for sp in res.split:
            sp["self_flagged"] = listed
        did = _store_draft(conn, task_id, "paste", req.provider, req.text, res.split, res.problems)
        event(conn, task_id, "pasted", draft_id=did, provider=req.provider, chars=len(req.text),
              sha256=pack.sha256(req.text), pieces=len(res.split), problems=len(res.problems))
    return {"draft_id": did, "split": res.split, "problems": res.problems}


@app.get("/tasks/{task_id}/drafts/{draft_id}", dependencies=[Depends(require_key)])
def get_draft(task_id: str, draft_id: int):
    with db() as conn:
        task_or_404(conn, task_id)
        r = conn.execute("SELECT * FROM drafts WHERE id = ? AND task_id = ?", (draft_id, task_id)).fetchone()
    if r is None:
        raise HTTPException(404, f"draft {draft_id} not found in {task_id}")
    return {"draft_id": r["id"], "kind": r["kind"], "provider": r["provider"], "text": r["raw_text"],
            "split": json.loads(r["split"]), "problems": json.loads(r["problems"]), "parent_id": r["parent_id"]}


@app.post("/tasks/{task_id}/drafts/{draft_id}/split", status_code=201,
          dependencies=[Depends(require_key), Depends(rate_limit)])
def manual_split(task_id: str, draft_id: int, req: ManualSplit):
    """The person's own split. A new draft (drafts are append-only) that submit can use."""
    with db() as conn, write(conn):
        t = task_or_404(conn, task_id)
        parent = conn.execute("SELECT id FROM drafts WHERE id = ? AND task_id = ?", (draft_id, task_id)).fetchone()
        if parent is None:
            raise HTTPException(404, f"draft {draft_id} not found in {task_id}")
        by_key = {p["key"]: p for p in t["pieces"]}
        got = [p.piece_key for p in req.pieces]
        unknown = [k for k in got if k not in by_key]
        if unknown:
            raise HTTPException(422, f"not pieces of this task: {', '.join(unknown)}")
        if len(set(got)) != len(got) or set(got) != set(by_key):
            raise HTTPException(422, f"give every piece exactly once: {', '.join(by_key)}")
        total = sum(len(p.text) for p in req.pieces)
        if total > paste_max_chars():
            raise HTTPException(413, f"the pieces are longer than {paste_max_chars()} characters together")
        split = []
        for p in sorted(req.pieces, key=lambda p: [x["key"] for x in t["pieces"]].index(p.piece_key)):
            text = paste.normalise(p.text).strip()
            if not text:
                raise HTTPException(422, f"piece {p.piece_key} is empty")
            split.append({"piece_key": p.piece_key, "text": text, "removed_pre": "", "removed_post": ""})
        order = [x["key"] for x in t["pieces"]]
        raw = "\n\n".join(pack.marker(order.index(s["piece_key"]) + 1, by_key[s["piece_key"]]["channel"]) + "\n" + s["text"]
                          for s in split)
        did = _store_draft(conn, task_id, "manual", None, raw, split, [], parent_id=draft_id)
        event(conn, task_id, "split_by_hand", draft_id=did, parent_id=draft_id, chars=total)
    return {"draft_id": did, "split": split, "problems": []}


@app.post("/tasks/{task_id}/submit", dependencies=[Depends(require_key), Depends(rate_limit)])
def submit(task_id: str, req: SubmitIn):
    with db() as conn:
        t = task_or_404(conn, task_id)
        d = conn.execute("SELECT * FROM drafts WHERE id = ? AND task_id = ?", (req.draft_id, task_id)).fetchone()
        if d is None:
            raise HTTPException(404, f"draft {req.draft_id} not found in {task_id}")
        existing = {r["piece_key"]: dict(r) for r in conn.execute("SELECT * FROM pieces WHERE task_id = ?", (task_id,))}
    problems = json.loads(d["problems"])
    split = {s["piece_key"]: s for s in json.loads(d["split"])}
    if problems:
        raise HTTPException(409, {"message": "the split has problems; split it by hand first", "problems": problems})
    missing = [p["key"] for p in t["pieces"] if p["key"] not in split]
    if missing:
        raise HTTPException(409, {"message": "pieces missing from this draft", "missing": missing})
    try:
        for k, row in existing.items():
            if row["item_id"]:
                st = services.get_item(row["item_id"]).get("status")
                if st in ("approved", "published"):
                    raise HTTPException(409, {"message": f"piece {k} is already {st}; nothing was submitted",
                                              "piece_key": k, "calendar_item_id": row["item_id"]})
                existing[k]["_status"] = st
        day = F.parse_day(t["publish_on"])
        q = services.query_facts(piece_scope(t["scope"], None, [p["channel"] for p in t["pieces"]]), t["publish_on"])
        known = known_facts(q, services.all_facts())
    except services.ServiceError as exc:
        raise upstream(exc) from None
    snapshot = {}
    versions = {s["key"]: s.get("version") for s in t["snapshot"]}
    for f in t["snapshot_facts"]:
        snapshot[f["key"]] = {**f, "version": versions.get(f["key"], f.get("version"))}
    fact_lines = t["share_preview"].get("sent") or []

    results = []
    for i, p in enumerate(t["pieces"], 1):
        text = split[p["key"]]["text"]
        scope = piece_scope(t["scope"], p["channel"])
        fill, findings, used, mnote = check_piece(text, snapshot, known, day, scope, fact_lines)
        findings += _self_flagged(fill.text, split[p["key"]].get("self_flagged") or [], findings)
        filled = fill.text.strip()
        brand, bnote = services.brand_check(filled, p["channel"])
        platform, pnote = services.validate(p["channel"], filled)
        if p.get("max_chars") and len(filled) > p["max_chars"]:
            platform.append({"rule": "too_long", "severity": "error",
                             "detail": f"{len(filled)} chars, the task allows {p['max_chars']}"})
        skipped = [n for n in (mnote, bnote, pnote) if n]
        is_blocked = evidence.blocked(findings) or any(v.get("severity") == "error" for v in brand + platform)
        sha = canonical_sha256(filled)
        notes = notes_for(t, p, findings, brand, platform, skipped, sha)
        old = existing.get(p["key"]) or {}
        try:
            if old.get("item_id") and old.get("_status") in ("draft", "in_review", "idea"):
                try:
                    services.set_status(old["item_id"], "rejected", f"replaced by a new draft of task {task_id}")
                except services.ServiceError as exc:
                    skipped.append(f"old item {old['item_id']} not rejected: {exc}")
            item = services.create_item(f"{t['goal'][:80]} · {p['channel']}", p["channel"], filled,
                                        "draft" if is_blocked else "in_review", notes, f"task {task_id}")
        except services.ServiceError as exc:
            raise upstream(exc) from None
        state = "draft" if is_blocked else "in_review"
        checks = {"brand": brand, "platform": platform, "skipped": skipped,
                  "calendar_sha256": item.get("body_sha256")}
        with db() as conn, write(conn):
            conn.execute(
                "UPDATE pieces SET draft_id = ?, filled_text = ?, filled_sha256 = ?, blocked = ?, findings = ?,"
                " checks = ?, used_facts = ?, item_id = ?, state = ?, stale = '[]', updated_at = ?"
                " WHERE task_id = ? AND piece_key = ?",
                (req.draft_id, filled, sha, int(is_blocked), json.dumps(findings), json.dumps(checks),
                 json.dumps(used), item["id"], state, now(), task_id, p["key"]))
            event(conn, task_id, "submitted", piece_key=p["key"], draft_id=req.draft_id, item_id=item["id"],
                  sha256=sha, blocked=is_blocked, labels=sorted({x["label"] for x in findings}))
        results.append({"piece_key": p["key"], "filled_text": filled, "filled_sha256": sha, "blocked": is_blocked,
                        "findings": findings, "calendar_item_id": item["id"], "status": state,
                        "checks": {"brand": brand, "platform": platform}, "notes": skipped})
    with db() as conn, write(conn):
        conn.execute("UPDATE tasks SET status = 'submitted', updated_at = ? WHERE id = ?", (now(), task_id))
    return {"task_id": task_id, "draft_id": req.draft_id, "pieces": results}


@app.post("/tasks/{task_id}/pieces/{piece_key}/accept", dependencies=[Depends(require_key), Depends(rate_limit)])
def accept_finding(task_id: str, piece_key: str, req: AcceptIn):
    """A person says a blocking finding is wrong for this text ("the disclosure is there, in other
    words"). Only ACCEPTABLE labels, only on the text they saw (expected_sha256), recorded in the
    append-only events and the export manifest. When nothing else blocks the piece, its calendar
    item moves from draft to in_review; approval still needs the approver, bound to this text."""
    with db() as conn:
        task_or_404(conn, task_id)
        row = conn.execute("SELECT * FROM pieces WHERE task_id = ? AND piece_key = ?", (task_id, piece_key)).fetchone()
    if row is None or not row["filled_sha256"]:
        raise HTTPException(404, f"piece {piece_key} of {task_id} has not been submitted")
    if req.expected_sha256 != row["filled_sha256"]:
        raise HTTPException(409, {"message": "changed since you looked", "current_sha256": row["filled_sha256"]})
    findings = json.loads(row["findings"])
    if req.finding >= len(findings):
        raise HTTPException(404, f"finding {req.finding} not found")
    f = findings[req.finding]
    if f.get("accepted"):
        raise HTTPException(409, {"message": "already accepted", "accepted": f["accepted"]})
    if not f.get("blocking") or f.get("label") not in ACCEPTABLE:
        raise HTTPException(422, f"a {f.get('label')} finding cannot be accepted here; "
                                 "fix the text or the fact and submit again")
    wording = " ".join(req.wording.split()) if req.wording is not None else None
    # the same exact, case / space folded, whole-word match the evidence check uses later
    if wording is not None and (len(wording) < 3 or not evidence.phrase_in(wording, row["filled_text"] or "")):
        raise HTTPException(422, "the wording must be copied from the text")
    accepted = {"by": " ".join(req.by.split()), "note": " ".join(req.note.split()), "at": now()}
    findings[req.finding] = {**f, "blocking": False, "accepted": accepted}
    checks = json.loads(row["checks"])
    gate = [v for v in (checks.get("brand") or []) + (checks.get("platform") or []) if v.get("severity") == "error"]
    still = evidence.blocked(findings) or bool(gate)
    moved = None
    if not still and row["item_id"] and row["state"] == "draft":
        try:
            st = services.get_item(row["item_id"]).get("status")
            if st == "draft":
                services.set_status(row["item_id"], "in_review",
                                    f"{accepted['by']} accepted a {f['label']} finding: {accepted['note']}"[:500])
                moved = "in_review"
        except services.ServiceError as exc:
            raise upstream(exc) from None
    with db() as conn, write(conn):
        conn.execute("UPDATE pieces SET findings = ?, blocked = ?, state = ?, updated_at = ? "
                     "WHERE task_id = ? AND piece_key = ?",
                     (json.dumps(findings), int(still), moved or row["state"], now(), task_id, piece_key))
        event(conn, task_id, "finding_accepted", piece_key=piece_key, finding=req.finding, label=f["label"],
              fact_key=f.get("fact_key"), sentence=_one_line(f.get("sentence") or "", 200),
              sha256=row["filled_sha256"], by=accepted["by"], note=accepted["note"], moved_to=moved,
              wording=wording)
    out = {"task_id": task_id, "piece_key": piece_key, "finding": findings[req.finding], "blocked": still,
           "status": moved or row["state"]}
    if wording is not None:  # best effort: the acceptance above stands whatever 05 says
        try:
            if not f.get("fact_key") or not f.get("quote"):
                raise services.ServiceError("the finding names no fact or disclosure")
            prop = services.propose_wording(f["fact_key"], str(f["quote"]), wording, task_id, accepted["by"])
            out["wording_proposal"] = {"id": prop.get("id"), "status": prop.get("status")}
        except services.ServiceError as exc:
            out["wording_proposal"] = {"error": str(exc)}
    return out


def extras_on() -> bool:
    """EXTRAS_CHECK=off turns off the invented-extras check (on by default)."""
    return os.environ.get("EXTRAS_CHECK", "on").strip().lower() not in ("off", "0", "false", "no")


def calendar_lines(publish_on: str) -> list[str] | None:
    """For the pack: the publish date in the local calendar, occasions near it (short) and the plugin's own
    notes. Only with a local calendar plugin (LOCAL_DIR/occasions.py); None otherwise (the pack is unchanged)."""
    cal = L.occasions_plugin()
    if cal is None:
        return None
    day = F.parse_day(publish_on)
    lines = []
    local_day = cal.format_date(day) if hasattr(cal, "format_date") else None
    if local_day:
        lines.append(f"Publish date {publish_on} = {local_day}.")
    for o in cal.occasions_near(day, days=21, past_days=3)[:4]:
        when = str(o["date"]) + (f" to {o['end']}" if o.get("end") and o["end"] != o["date"] else "")
        note = f" {o['notes']}" if o.get("notes") else ""
        flag = "" if o.get("verified", True) else " (date not confirmed)"
        lines.append(f"{o['name']}: {when}{flag}.{note}"[:220])
    lines += [str(x)[:220] for x in getattr(cal, "PACK_NOTES", [])]
    return lines


@app.get("/occasions", dependencies=[Depends(require_key)])
def occasions(on: str | None = Query(None, max_length=10), days: int = Query(45, ge=1, le=366)):
    """Dated occasions near a publish date (holidays, seasons), with notes for marketers, from the local
    calendar plugin (LOCAL_DIR/occasions.py). Off without one."""
    cal = L.occasions_plugin()
    if cal is None:
        return {"calendar": None, "occasions": [], "note": "no local calendar (add LOCAL_DIR/occasions.py)"}
    day = F.parse_day(on) if on else date.today()
    items = [{k: o.get(k) for k in ("name", "date", "end", "local_date", "kind", "movable", "verified", "notes", "source")}
             for o in cal.occasions_near(day, days=days, past_days=3)]
    for o in items:
        o["date"] = str(o["date"]) if o.get("date") else None
        o["end"] = str(o["end"]) if o.get("end") else None
    return {"calendar": getattr(cal, "NAME", "local"), "on": day.isoformat(),
            "on_local": cal.format_date(day) if hasattr(cal, "format_date") else None, "occasions": items,
            "note": getattr(cal, "NOTE", None)}


@app.post("/check", dependencies=[Depends(require_key), Depends(rate_limit)])
def check(req: CheckIn):
    """Stateless: slots and evidence for any text (no task, nothing written, no calendar item)."""
    if len(req.text) > paste_max_chars():
        raise HTTPException(413, f"the text is longer than {paste_max_chars()} characters")
    if not req.text.strip():
        raise HTTPException(422, "text is empty")
    day_s = req.publish_on or date.today().isoformat()
    channel = (req.channel or "").strip().lower() or None
    scope = piece_scope(req.scope.model_dump(), channel)
    try:
        q = services.query_facts(scope, day_s)
        known = known_facts(q, services.all_facts())
    except services.ServiceError as exc:
        raise upstream(exc) from None
    day = F.parse_day(day_s)
    ok_lines = [pack.public_line(f) for f in known.values()
                if F.classify(f, day, scope) is None and (f.get("sensitivity") or "public") == "public"]
    answer, listed = selfaudit.extract(paste.normalise(req.text))
    fill, findings, used, note = check_piece(answer, {}, known, day, scope, ok_lines)
    findings += _self_flagged(fill.text, listed, findings)
    # the same brand (05: banned phrases, confirmed kit rules, AI-sheen) and channel (14) checks a
    # submitted piece gets, so a "check this line" answer matches what submit would say
    filled = fill.text.strip()
    brand, bnote = services.brand_check(filled, channel)
    platform, pnote = services.validate(channel, filled) if channel else ([], None)
    notes = [n for n in (note, bnote, pnote) if n]
    gate = any(v.get("severity") == "error" for v in brand + platform)
    return {"findings": findings, "blocked": evidence.blocked(findings) or gate, "filled_text": fill.text,
            "used_facts": used, "fact_set_version": q.get("fact_set_version"), "publish_on": day_s,
            "checks": {"brand": brand, "platform": platform}, "notes": notes}


# ---------- zero-AI post templates (price list, rate card, facts digest)


class TemplateRenderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: Literal["price_list", "rate_card", "facts_digest"]
    channel: str = Field(min_length=1, max_length=30)
    scope: Scope = Field(default_factory=Scope)
    publish_on: str
    title: str | None = Field(default=None, max_length=120)
    intro: str | None = Field(default=None, max_length=500)
    subjects: list[str] | None = Field(default=None, max_length=20)
    group_by: Literal["subject"] | None = None

    @field_validator("publish_on")
    @classmethod
    def day(cls, v):
        return _day_str(v)

    @field_validator("channel")
    @classmethod
    def chan(cls, v):
        return PieceIn(key="p1", channel=v).channel


class TemplateTaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: Literal["price_list", "rate_card", "facts_digest"]
    channel: str | None = Field(default=None, min_length=1, max_length=30)
    channels: list[str] | None = Field(default=None, max_length=5)
    scope: Scope = Field(default_factory=Scope)
    publish_on: str
    goal: str | None = Field(default=None, min_length=3, max_length=2000)
    title: str | None = Field(default=None, max_length=120)
    intro: str | None = Field(default=None, max_length=500)
    subjects: list[str] | None = Field(default=None, max_length=20)
    group_by: Literal["subject"] | None = None
    audience: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("publish_on")
    @classmethod
    def day(cls, v):
        return _day_str(v)


def _render_template(req, channel: str, kit: list[dict], kit_note: str | None, limits: dict | None) -> dict:
    scope = piece_scope(req.scope.model_dump(), channel, [channel])
    try:
        q = services.query_facts(scope, req.publish_on)
    except services.ServiceError as exc:
        raise upstream(exc) from None
    rule = ((limits or {}).get("channels") or {}).get(channel) or {}
    out = templates_render.render(
        req.template, channel, [f for f in q["facts"] if isinstance(f, dict)], F.parse_day(req.publish_on), scope,
        kit, rule.get("limit"), req.title, req.intro, req.subjects, req.group_by, [kit_note] if kit_note else None)
    out["channel"] = channel
    out["publish_on"] = req.publish_on
    out["fact_set_version"] = q.get("fact_set_version")
    return out


@app.post("/templates/render", dependencies=[Depends(require_key), Depends(rate_limit)])
def templates_render_endpoint(req: TemplateRenderIn):
    """A post written from the facts alone, no chatbot. Nothing is stored; /templates/task does the rest."""
    kit, kit_note = services.kit_rules()
    limits, _ = services.channel_rules()
    out = _render_template(req, req.channel, kit, kit_note, limits)
    return {k: out[k] for k in ("text", "parts", "facts_used", "chars", "notes", "channel", "publish_on",
                                "fact_set_version")}


@app.post("/templates/task", status_code=201, dependencies=[Depends(require_key), Depends(rate_limit)])
def templates_task(req: TemplateTaskIn):
    """Render, then take the normal path: a task, the text pasted as provider "template", submitted
    (same checks, a calendar item bound to the text). A person still approves in the calendar."""
    chans = list(dict.fromkeys(PieceIn(key="p1", channel=c).channel for c in (req.channels or []) + ([req.channel] if req.channel else [])))
    if not chans:
        raise HTTPException(422, "give channel or channels")
    kit, kit_note = services.kit_rules()
    limits, _ = services.channel_rules()
    rendered, pieces, texts = [], [], []
    for ch in chans:
        out = _render_template(req, ch, kit, kit_note, limits)
        if not out["text"]:
            raise HTTPException(422, {"message": f"nothing to render for {ch}", "notes": out["notes"]})
        for part in out["parts"]:
            key = f"p{len(pieces) + 1}"
            pieces.append({"key": key, "channel": ch, "kind": req.template})
            texts.append(part)
            rendered.append({"piece_key": key, "channel": ch, "chars": len(part), "facts_used": out["facts_used"],
                             "notes": out["notes"]})
    if len(pieces) > 10:
        raise HTTPException(422, f"that makes {len(pieces)} messages; a task holds at most 10")
    goal = req.goal or f"{templates_render.TITLES[req.template]} for {req.publish_on}"
    created = create_task(TaskIn(goal=goal, pieces=[PieceIn(**p) for p in pieces], scope=req.scope,
                                 publish_on=req.publish_on, audience=req.audience, notes=req.notes))
    raw = "\n\n".join(pack.marker(i, p["channel"]) + "\n" + t for i, (p, t) in enumerate(zip(pieces, texts), 1))
    pasted = paste_answer(created["id"], PasteIn(text=raw, provider="template"))
    if pasted["problems"]:
        raise HTTPException(409, {"message": "the rendered text did not split into its pieces", "task_id": created["id"],
                                  "problems": pasted["problems"]})
    res = submit(created["id"], SubmitIn(draft_id=pasted["draft_id"]))
    return {**res, "rendered": rendered, "blocked": any(p["blocked"] for p in res["pieces"]),
            "calendar_item_ids": [p["calendar_item_id"] for p in res["pieces"]]}


# ---------- exact quote (arithmetic in code, not in the chatbot)
class QuoteLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_key: str = Field(max_length=80)
    quantity: int = Field(ge=1, le=1_000_000)
    nights: int | None = Field(default=None, ge=1, le=3650)
    guests: int | None = Field(default=None, ge=1, le=10_000)


class QuoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Scope = Field(default_factory=Scope)
    publish_on: str | None = None
    lines: list[QuoteLine] = Field(min_length=1, max_length=50)
    currency: str | None = Field(default=None, max_length=3)
    allow_internal: bool = False

    @field_validator("publish_on")
    @classmethod
    def day(cls, v):
        return _day_str(v) if v else None


@app.post("/quote", dependencies=[Depends(require_key), Depends(rate_limit)])
def make_quote(req: QuoteIn, x_internal_quote: str | None = Header(default=None)):
    day_s = req.publish_on or date.today().isoformat()
    internal_ok = req.allow_internal and (x_internal_quote or "").strip().lower() == "yes"
    if req.allow_internal and not internal_ok:
        raise HTTPException(422, "allow_internal needs the header X-Internal-Quote: yes")
    scope = F.norm_scope(req.scope.model_dump())
    try:
        q = services.query_facts(scope, day_s)
        known = {f["key"]: f for f in services.all_facts()}
    except services.ServiceError as exc:
        raise upstream(exc) from None
    by_key = {f["key"]: f for f in q.get("facts") or [] if isinstance(f, dict) and f.get("key")}
    excluded = {e.get("key"): e.get("reason") for e in q.get("excluded") or [] if isinstance(e, dict)}
    try:
        out = quote.build([ln.model_dump() for ln in req.lines], by_key, known, excluded, F.parse_day(day_s), scope,
                          req.currency, internal_ok)
    except quote.QuoteError as exc:
        raise HTTPException(422, exc.message) from None
    out.update({"publish_on": day_s, "fact_set_version": q.get("fact_set_version")})
    return out


# ---------- export


def _approval(audit: list[dict] | None) -> dict | None:
    for a in reversed(audit or []):
        if isinstance(a, dict) and a.get("to_status") == "approved":
            return {"at": a.get("at"), "actor": a.get("actor"), "body_sha256": a.get("body_sha256")}
    return None


def render_export(t: dict, pieces: list[dict], manifest: dict, fmt: str) -> str:
    head = f"Task {t['id']} · facts {t['fact_set_version']} · publish {t['publish_on']}"
    out = []
    if fmt == "md":
        out += [f"# {head}", "", f"**Goal:** {' '.join(t['goal'].split())}", ""]
        for p in pieces:
            fence = "`" * max(3, _longest_backticks(p["filled_text"]) + 1)
            out += [f"## {p['n']} · {p['channel']}", "", f"{fence}text", p["filled_text"], fence, ""]
        out += ["## Posting checklist", ""] + [f"- [ ] {c}" for c in manifest["checklist"]] + [""]
        out += ["## Manifest", "", "```json", json.dumps(manifest, indent=2, ensure_ascii=False), "```", ""]
    else:
        out += [head, f"Goal: {' '.join(t['goal'].split())}", ""]
        for p in pieces:
            out += [pack.marker(p["n"], p["channel"]), p["filled_text"], f"=== END {p['n']} ===", ""]
        out += ["--- POSTING CHECKLIST ---"] + [f"[ ] {c}" for c in manifest["checklist"]] + [""]
        out += ["--- MANIFEST ---", json.dumps(manifest, indent=2, ensure_ascii=False), ""]
    return "\n".join(out)


def _longest_backticks(s: str) -> int:
    best = cur = 0
    for ch in s:
        cur = cur + 1 if ch == "`" else 0
        best = max(best, cur)
    return best


def read_back(body: str, fmt: str, pieces: list[dict]) -> list[str]:
    """Parse the export again and compare each piece with the approved hash. Returns problems."""
    lines = body.split("\n")
    got: dict[int, str] = {}
    for p in pieces:
        n = p["n"]
        if fmt == "md":
            head = f"## {n} · {p['channel']}"
            if head not in lines:
                continue
            i = lines.index(head) + 2
            fence = lines[i][: len(lines[i]) - len("text")]
            j = lines.index(fence, i + 1)
            got[n] = "\n".join(lines[i + 1:j])
        else:
            start, end = pack.marker(n, p["channel"]), f"=== END {n} ==="
            if start not in lines or end not in lines:
                continue
            i = lines.index(start)
            j = lines.index(end, i + 1)
            got[n] = "\n".join(lines[i + 1:j])
    problems = []
    for p in pieces:
        if p["n"] not in got:
            problems.append(f"piece {p['piece_key']} missing from the export")
        elif canonical_sha256(got[p["n"]]) != p["filled_sha256"]:
            problems.append(f"piece {p['piece_key']} differs from the approved text")
    return problems


def export_readiness(t: dict, pieces: list[dict]) -> tuple[list[str], dict, dict, dict]:
    """(reasons it cannot be exported, approvals by piece, fact query, fresh facts by key).
    The one place the export rules live: GET /tasks/{id} shows it, GET /tasks/{id}/export
    enforces it. Raises services.ServiceError when 19 or 05 cannot be asked."""
    reasons: list[str] = []
    not_submitted = [p["piece_key"] for p in pieces if not p["calendar_item_id"]]
    if not_submitted or not pieces:
        return [f"piece {k} is not submitted yet" for k in not_submitted or [p["key"] for p in t["pieces"]]], {}, {}, {}
    approvals = {}
    for p in pieces:
        item = services.get_item(p["calendar_item_id"])
        status = item.get("status")
        if status not in ("approved", "published"):
            reasons.append(f"piece {p['piece_key']} is {status}, not approved (item {p['calendar_item_id']})")
            continue
        body_hash = canonical_sha256(str(item.get("body") or ""))
        if body_hash != p["filled_sha256"] or (item.get("body_sha256") and item["body_sha256"] != p["filled_sha256"]):
            reasons.append(f"piece {p['piece_key']}: the approved text is not the text that was checked")
            continue
        ap = _approval(services.item_audit(p["calendar_item_id"]))
        if ap and ap.get("body_sha256") and ap["body_sha256"] != p["filled_sha256"]:
            reasons.append(f"piece {p['piece_key']}: the approval was for another version of the text")
            continue
        approvals[p["piece_key"]] = {"item_id": p["calendar_item_id"], "status": status,
                                     "approved": ap, "audit_ref": f"19 /items/{p['calendar_item_id']}/audit"}
    q = services.query_facts(piece_scope(t["scope"], None, [p["channel"] for p in t["pieces"]]), t["publish_on"])
    fresh = {f["key"]: f for f in q.get("facts") or [] if isinstance(f, dict) and f.get("key")}
    why = {e.get("key"): e.get("reason") for e in q.get("excluded") or [] if isinstance(e, dict)}
    for s in t["snapshot"]:
        f = fresh.get(s["key"])
        if f is None:
            reasons.append(f"fact {s['key']} is no longer valid at {t['publish_on']} ({why.get(s['key'], 'not served')})")
        elif s.get("version") is not None and f.get("version") is not None and f["version"] != s["version"]:
            reasons.append(f"fact {s['key']} changed since the pack (v{s['version']} -> v{f['version']})")
    return reasons, approvals, q, fresh


def export_state(t: dict, pieces: list[dict]) -> dict:
    """{"ready", "missing"} for the control room: the export's own rules, nothing written."""
    try:
        reasons = export_readiness(t, pieces)[0]
    except services.ServiceError as exc:
        return {"ready": False, "missing": [f"could not check the export: {exc}"]}
    return {"ready": not reasons, "missing": reasons}


@app.get("/tasks/{task_id}/export", dependencies=[Depends(require_key)])
def export(task_id: str, format: Literal["txt", "md"] = "txt"):
    with db() as conn:
        t = task_or_404(conn, task_id)
        pieces = _pieces_out(conn, task_id)
    if not all(p["calendar_item_id"] for p in pieces) or not pieces:
        raise HTTPException(409, {"message": "not ready to export", "reasons": export_readiness(t, pieces)[0]})
    try:
        reasons, approvals, q, fresh = export_readiness(t, pieces)
    except services.ServiceError as exc:
        raise upstream(exc) from None
    if reasons:
        with db() as conn, write(conn):
            event(conn, task_id, "export_refused", reasons=reasons)
        raise HTTPException(409, {"message": "not ready to export", "reasons": reasons})

    by_key = {f["key"]: f for f in t["snapshot_facts"]}
    checklist, disclosures, accepted = [], {}, {}
    for p in pieces:
        disc = []
        for k in p["used_facts"]:
            disc += [str(d) for d in (by_key.get(k) or fresh.get(k) or {}).get("required_disclosures") or []]
        disc = list(dict.fromkeys(disc))
        disclosures[p["piece_key"]] = disc
        checklist.append(f"Post piece {p['n']} on {p['channel']} on {t['publish_on']}, exactly as exported")
        for d in disc:
            checklist.append(f"Piece {p['n']}: the disclosure \"{d}\" is visible, not cut off")
        for x in p["findings"]:
            if x.get("accepted"):
                accepted.setdefault(p["piece_key"], []).append(
                    {"label": x["label"], "fact_key": x.get("fact_key"), "sentence": x.get("sentence"),
                     "detail": x.get("detail"), **x["accepted"]})
                checklist.append(f"Piece {p['n']}: {x['accepted']['by']} accepted a {x['label']} finding "
                                 f"(\"{_one_line(x.get('detail') or '', 80)}\"); check it still holds")
    checklist.append("Do not edit the text after export: any change needs a new check and approval")
    manifest = {
        "task_id": t["id"], "publish_on": t["publish_on"], "fact_set_version": t["fact_set_version"],
        "fact_set_version_at_export": q.get("fact_set_version"), "pack_sha256": t["pack_sha256"],
        "exported_at": now(), "format": format,
        "pieces": [{"piece_key": p["piece_key"], "n": p["n"], "channel": p["channel"], "sha256": p["filled_sha256"],
                    "used_facts": p["used_facts"], **approvals[p["piece_key"]]} for p in pieces],
        "disclosures": disclosures,
        "accepted_findings": accepted,
        "facts": [{"key": s["key"], "version": s.get("version"), "sensitivity": s.get("sensitivity"),
                   "valid_to": (fresh.get(s["key"]) or {}).get("valid_to")} for s in t["snapshot"]],
        "checklist": checklist,
    }
    body = render_export(t, pieces, manifest, format)
    problems = read_back(body, format, pieces)
    if problems:
        raise HTTPException(500, "export check failed: " + "; ".join(problems))
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    with db() as conn, write(conn):
        cur = conn.execute("INSERT INTO exports (task_id, format, sha256, manifest, created_at) VALUES (?, ?, ?, ?, ?)",
                           (task_id, format, sha, json.dumps(manifest), now()))
        conn.execute("UPDATE tasks SET status = 'exported', updated_at = ? WHERE id = ?", (now(), task_id))
        event(conn, task_id, "exported", export_id=cur.lastrowid, sha256=sha, format=format)
    media = "text/markdown; charset=utf-8" if format == "md" else "text/plain; charset=utf-8"
    return Response(body.encode("utf-8"), media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{task_id}.{format}"', "Cache-Control": "no-store",
        "X-Export-Sha256": sha})


# ---------- reconcile and blockers


def reconcile_run() -> dict:
    if not _reconcile_lock.acquire(blocking=False):
        return {"skipped": "a reconcile run is already going"}
    try:
        return _reconcile()
    finally:
        _reconcile_lock.release()


def _reconcile() -> dict:
    with db() as conn:
        since = int(meta_get(conn, "changes_seq", "0") or 0)
    ch = services.fact_changes(since)
    keys = {c.get("key") for c in ch["changes"] if isinstance(c, dict) and c.get("key")}
    seq = int(ch.get("seq") or since)
    moved, blockers, errors, checked = [], [], [], 0
    if keys:
        with db() as conn:
            tasks = [task_or_404(conn, r["id"]) for r in conn.execute(
                "SELECT DISTINCT task_id AS id FROM pieces WHERE item_id IS NOT NULL")]
            piece_rows = {t["id"]: [dict(r) for r in conn.execute(
                "SELECT * FROM pieces WHERE task_id = ? AND item_id IS NOT NULL ORDER BY n", (t["id"],))] for t in tasks}
        for t in tasks:
            snap = {s["key"]: s for s in t["snapshot"]}
            hit = sorted(keys & set(snap))
            if not hit:
                continue
            checked += 1
            try:
                q = services.query_facts(piece_scope(t["scope"], None, [p["channel"] for p in t["pieces"]]),
                                         t["publish_on"])
            except services.ServiceError as exc:
                errors.append(f"{t['id']}: {exc}")
                continue
            fresh = {f["key"]: f for f in q.get("facts") or [] if isinstance(f, dict) and f.get("key")}
            changed = [k for k in hit if k not in fresh or (snap[k].get("version") is not None and
                                                            fresh[k].get("version") != snap[k]["version"])]
            if not changed:
                continue
            note = "warnings: " + "; ".join(f"fact {k} changed" for k in changed)
            for p in piece_rows[t["id"]]:
                try:
                    status = services.get_item(p["item_id"]).get("status")
                    if status in ("in_review", "approved"):
                        services.set_status(p["item_id"], "draft", note)
                        moved.append({"task_id": t["id"], "piece_key": p["piece_key"], "item_id": p["item_id"],
                                      "from": status, "to": "draft", "facts": changed})
                        state = "draft"
                    elif status == "published":
                        blockers.append({"task_id": t["id"], "piece_key": p["piece_key"], "item_id": p["item_id"],
                                         "facts": changed})
                        state = "published_fact_changed"
                    else:
                        if status == "draft":
                            services.add_note(p["item_id"], note)
                        state = p["state"] if status != "draft" else "draft"
                except services.ServiceError as exc:
                    errors.append(f"{t['id']}/{p['piece_key']}: {exc}")
                    continue
                with db() as conn, write(conn):
                    stale = sorted(set(json.loads(p["stale"])) | set(changed))
                    conn.execute("UPDATE pieces SET state = ?, stale = ?, updated_at = ? WHERE task_id = ? AND piece_key = ?",
                                 (state, json.dumps(stale), now(), t["id"], p["piece_key"]))
                    event(conn, t["id"], "fact_changed", piece_key=p["piece_key"], item_id=p["item_id"],
                          from_status=status, to_state=state, facts=changed)
    if not errors:
        with db() as conn, write(conn):
            meta_set(conn, "changes_seq", str(seq))
            meta_set(conn, "reconciled_at", now())
    return {"since": since, "seq": seq if not errors else since, "changed_keys": sorted(keys),
            "tasks_checked": checked, "moved": moved, "published_with_changed_facts": blockers, "errors": errors}


@app.post("/reconcile", dependencies=[Depends(require_key)])
def reconcile():
    try:
        return reconcile_run()
    except services.ServiceError as exc:
        raise upstream(exc) from None


@app.get("/blockers", dependencies=[Depends(require_key)])
def blockers():
    """What stops copy from going out: [{kind, count, text, link}], only kinds with a count."""
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM pieces WHERE item_id IS NOT NULL")]
    missing = blocked_slots = stale = published = 0
    for r in rows:
        f = json.loads(r["findings"])
        slot = [x for x in f if x["label"] == "slot_blocked"]
        if r["state"] == "published_fact_changed":
            published += 1
            continue
        if any(x["fact_key"] is None and "missing fact" in x["detail"] for x in slot) or \
                any(x["fact_key"] is None and "no fact has this key" in x["detail"] for x in slot):
            missing += 1
        if slot:
            blocked_slots += 1
        if json.loads(r["stale"]):
            stale += 1
    out = []
    questions = [q for q in services.open_questions() if q.get("kind") == "missing_fact"]
    if missing or questions:
        out.append({"kind": "missing_facts", "count": missing + len(questions),
                    "text": "Facts the copy needs that nobody has confirmed", "link": "/facts?filter=questions"})
    if blocked_slots:
        out.append({"kind": "blocked_slots", "count": blocked_slots,
                    "text": "Pieces with a fact slot that cannot be filled", "link": "/tasks?filter=blocked"})
    if stale:
        out.append({"kind": "expired_facts_in_use", "count": stale,
                    "text": "Pieces whose facts changed or expired since they were checked", "link": "/tasks?filter=stale"})
    if published:
        out.append({"kind": "published_with_changed_facts", "count": published,
                    "text": "Published pieces whose facts changed: check the live posts", "link": "/tasks?filter=published"})
    try:
        today = date.today()
        due = [f for f in services.all_facts() if (f.get("status") or "active") == "active"
               and F._day(f.get("review_by")) and F._day(f.get("review_by")) <= today]
    except services.ServiceError:
        due = []
    if due:
        out.append({"kind": "facts_due_for_review", "count": len(due),
                    "text": "Facts past their review date", "link": "/facts?filter=review"})
    leads = services.leads_waiting()
    if leads:
        out.append({"kind": "leads_waiting", "count": leads, "text": "Lead replies waiting for a person",
                    "link": "/leads"})
    return out



# ---------- public content drift audit (app/audit.py): POST /audit, read-only
from . import audit as _audit  # noqa: E402

app.include_router(_audit.make_router(require_key, rate_limit, piece_scope, known_facts))
