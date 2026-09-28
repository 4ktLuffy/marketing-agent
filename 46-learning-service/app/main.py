"""Learning service: turns a reviewer's approvals, edits and rejections into examples and rules.

Every review decision is recorded as an event. Edited finals become few-shot examples
("the reviewer's own words"). Edits and rejections are reflected on by the LLM (via the
gateway) into short candidate rules; a human activates or rejects them, and the active
ones are served as a summary the gateway appends to the brand text.
"""
import hmac
import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from app import performance

log = logging.getLogger("learning-service")
app = FastAPI(title="learning-service")

DECISIONS = ("approved", "edited", "rejected")
REFLECTED_DECISIONS = ("edited", "rejected")
# pending/active/rejected: rules reflected from reviews. Rules from experiments (45) start
# provisional, become active only after a replication and a person's approval (51), and are
# retired when later results contradict them at least as often as they support them.
RULE_STATUSES = ("pending", "active", "rejected", "provisional", "retired")
RULE_MAX_CHARS = 200
EVENT_COLUMNS = (
    "id", "item_id", "channel", "campaign_id", "decision", "draft", "final", "reason",
    "reviewer", "created_at", "reflected_at",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    campaign_id INTEGER,
    decision TEXT NOT NULL,
    draft TEXT NOT NULL,
    final TEXT,
    reason TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL,
    reflected_at TEXT
);
CREATE INDEX IF NOT EXISTS events_item ON events (item_id, decision);
CREATE INDEX IF NOT EXISTS events_decision_created ON events (decision, created_at);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    norm TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    source_event_ids TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rules_norm ON rules (norm);
"""
# Columns added for rules from experiments; old databases get them on first use.
RULE_EXTRA_COLUMNS = {
    "source": "TEXT NOT NULL DEFAULT 'review'",
    "exp_key": "TEXT",
    "variable": "TEXT",
    "winner": "TEXT",
    "loser": "TEXT",
    "support": "INTEGER NOT NULL DEFAULT 0",
    "contradicts": "INTEGER NOT NULL DEFAULT 0",
    "replicated": "INTEGER NOT NULL DEFAULT 0",
    "source_experiment_ids": "TEXT NOT NULL DEFAULT '[]'",
    "contradicting_experiment_ids": "TEXT NOT NULL DEFAULT '[]'",
    "evidence": "TEXT NOT NULL DEFAULT '[]'",
    "last_confirmed_at": "TEXT",
}


# ---------- time: stored as "YYYY-MM-DDTHH:MM:SSZ" so strings sort correctly


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    return fmt(datetime.now(timezone.utc))


def parse_since(value: str) -> str:
    # An unencoded "+02:00" in a query string arrives as " 02:00"; put the plus back.
    value = re.sub(r"(\d) (\d{2}:?\d{2})$", r"\1+\2", value.strip())
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"since: not an ISO 8601 date/time: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return fmt(dt)


# ---------- storage


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/learning.sqlite")


def gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://llm-gateway:8000").rstrip("/")


def gateway_timeout() -> float:
    return float(os.environ.get("GATEWAY_TIMEOUT", "300"))


@contextmanager
def db():
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        have = {r[1] for r in conn.execute("PRAGMA table_info(rules)")}
        for col, decl in RULE_EXTRA_COLUMNS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE rules ADD COLUMN {col} {decl}")
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_event(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in EVENT_COLUMNS}


def row_to_rule(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "text": row["text"],
        "scope": row["scope"],
        "status": row["status"],
        "source_event_ids": json.loads(row["source_event_ids"]),
        "created_at": row["created_at"],
        "source": row["source"],
        "support": row["support"],
        "contradicts": row["contradicts"],
        "replicated": bool(row["replicated"]),
        "source_experiment_ids": json.loads(row["source_experiment_ids"]),
        "contradicting_experiment_ids": json.loads(row["contradicting_experiment_ids"]),
        "evidence": json.loads(row["evidence"]),
        "last_confirmed_at": row["last_confirmed_at"],
    }


def normalize_rule(text: str) -> str:
    """Dedupe key: whitespace collapsed, case folded."""
    return " ".join(text.split()).casefold()


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- models


def _blank_to_none(v):
    if v is None:
        return None
    v = v.strip()
    return v or None


class NewEvent(BaseModel):
    item_id: int
    channel: str
    campaign_id: int | None = None
    decision: str
    draft: str
    final: str | None = None
    reason: str | None = None
    reviewer: str | None = None

    @field_validator("channel", "draft")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("decision")
    @classmethod
    def known_decision(cls, v: str) -> str:
        if v not in DECISIONS:
            raise ValueError(f"must be one of {list(DECISIONS)}")
        return v

    @field_validator("final", "reason", "reviewer")
    @classmethod
    def blank_none(cls, v):
        return _blank_to_none(v)


class ReflectRequest(BaseModel):
    since_days: int = Field(7, ge=1, le=3650)
    max_events: int = Field(20, ge=1, le=200)


class RuleStatus(BaseModel):
    status: str


class ExperimentResult(BaseModel):
    """A decided experiment from 45 (POST /experiments/{id}/decide)."""
    experiment_id: int = Field(ge=1)
    decision: Literal["winner", "no_practical_difference", "inconclusive"]
    variable: str = Field(min_length=1, max_length=40)
    channels: list[str] = Field(min_length=1, max_length=20)
    values: list[str] = Field(min_length=2, max_length=2)
    winner: str | None = None
    loser: str | None = None
    lift_hdi: list[float] | None = None
    decided_at: str | None = None
    summary: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def check(self):
        self.channels = sorted({c.strip().lower() for c in self.channels if c.strip()})
        self.values = [v.strip() for v in self.values]
        if not self.channels or any(not v for v in self.values):
            raise ValueError("channels and both values must not be empty")
        if self.decision == "winner":
            if not self.winner or not self.loser:
                raise ValueError("a winner needs winner and loser")
            if {self.winner.casefold(), self.loser.casefold()} != {v.casefold() for v in self.values}:
                raise ValueError("winner and loser must be the two values")
        return self


# ---------- reflection


class ReflectError(Exception):
    pass


def reflect_one(event: dict) -> dict:
    """Ask the gateway for a rule. Returns {"generalizable","rule","scope"} or raises ReflectError."""
    variables = {k: (event[k] or None) for k in ("draft", "final", "reason", "channel")}
    try:
        r = httpx.post(
            f"{gateway_url()}/v1/run",
            json={"prompt": "reflect_rule", "vars": variables},
            headers={"X-Caller": "46 learning service",
                     **({"X-API-Key": os.environ["INTERNAL_API_KEY"]} if os.getenv("INTERNAL_API_KEY") else {})},
            timeout=gateway_timeout(),
        )
    except httpx.HTTPError as exc:
        raise ReflectError(f"gateway unreachable: {exc}") from exc
    if r.status_code != 200:
        raise ReflectError(f"gateway {r.status_code}: {r.text[:300]}")
    try:
        output = r.json().get("output")
    except ValueError as exc:
        raise ReflectError("gateway returned non-JSON") from exc
    if not isinstance(output, dict) or not isinstance(output.get("generalizable"), bool):
        raise ReflectError(f"unexpected gateway output: {str(output)[:300]}")
    return output


def rule_from_output(output: dict, event: dict) -> tuple[str, str] | None:
    """(text, scope) for a usable rule, or None."""
    if not output.get("generalizable"):
        return None
    text = output.get("rule")
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())[:RULE_MAX_CHARS].strip()
    if not text:
        return None
    scope = output.get("scope")
    scope = scope.strip().lower() if isinstance(scope, str) and scope.strip() else "all"
    return text, scope


# ---------- endpoints


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/events", status_code=201, dependencies=[Depends(require_key)])
def create_event(req: NewEvent):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO events (item_id, channel, campaign_id, decision, draft, final, reason,"
            " reviewer, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (req.item_id, req.channel, req.campaign_id, req.decision, req.draft, req.final,
             req.reason, req.reviewer, now_utc()),
        )
        row = conn.execute("SELECT * FROM events WHERE id = ?", (cur.lastrowid,)).fetchone()
        return row_to_event(row)


@app.get("/events")
def list_events(decision: str | None = None, since: str | None = None):
    where, args = [], []
    if decision:
        if decision not in DECISIONS:
            raise HTTPException(422, f"unknown decision {decision!r}; use {list(DECISIONS)}")
        where.append("decision = ?")
        args.append(decision)
    if since:
        where.append("created_at >= ?")
        args.append(parse_since(since))
    sql = "SELECT * FROM events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    with db() as conn:
        return [row_to_event(r) for r in conn.execute(sql, args)]


@app.get("/items/{item_id}/attempts")
def attempts(item_id: int):
    with db() as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM events WHERE item_id = ? AND decision = 'rejected'", (item_id,)
        ).fetchone()
    return {"item_id": item_id, "rejections": n}


def campaigns_url() -> str:
    return os.environ.get("CAMPAIGNS_URL", "http://campaign-service:8000").rstrip("/")


def perf_params() -> performance.Params:
    def num(name, default, cast=float):
        try:
            return cast(os.environ.get(name, default))
        except ValueError:
            return cast(default)
    return performance.Params(
        min_clicks=num("PERF_MIN_CLICKS", 5, int), min_posts=num("PERF_MIN_POSTS", 5, int),
        alpha=num("PERF_ALPHA", 0.1), settle_days=num("PERF_SETTLE_DAYS", 2.0),
        max_age_days=num("PERF_MAX_AGE_DAYS", 90, int), half_life_days=num("PERF_HALF_LIFE_DAYS", 45.0),
        dup_jaccard=num("PERF_DUP_JACCARD", 0.5))


def approval_examples(channel: str | None, k: int) -> list[dict]:
    where = ["((decision = 'edited' AND final IS NOT NULL) OR decision = 'approved')"]
    args: list = []
    if channel and channel.strip():
        where.append("lower(channel) = lower(?)")
        args.append(channel.strip())
    sql = (
        "SELECT item_id, decision, channel, COALESCE(final, draft) AS text FROM events WHERE "
        + " AND ".join(where)
        + " ORDER BY CASE decision WHEN 'edited' THEN 0 ELSE 1 END, id DESC LIMIT ?"
    )
    with db() as conn:
        rows = conn.execute(sql, (*args, k)).fetchall()
    return [{"text": r["text"], "channel": r["channel"], "decision": r["decision"], "item_id": r["item_id"]}
            for r in rows]


def approved_texts(channel: str | None) -> dict[int, dict]:
    """item_id -> its latest approved or edited text (the words a reviewer let through)."""
    sql = ("SELECT item_id, decision, channel, COALESCE(final, draft) AS text FROM events"
           " WHERE ((decision = 'edited' AND final IS NOT NULL) OR decision = 'approved')")
    args: list = []
    if channel and channel.strip():
        sql += " AND lower(channel) = lower(?)"
        args.append(channel.strip())
    out: dict[int, dict] = {}
    with db() as conn:
        for r in conn.execute(sql + " ORDER BY id", args):
            out[r["item_id"]] = {"text": r["text"], "decision": r["decision"], "channel": r["channel"]}
    return out


def performance_examples(channel: str | None, k: int) -> tuple[list[dict], str]:
    """(examples, note). Raises RuntimeError when 45 cannot be read."""
    p = perf_params()
    params = {"days": p.max_age_days}
    if channel and channel.strip():
        params["channel"] = channel.strip()
    key = os.environ.get("INTERNAL_API_KEY")
    try:
        r = httpx.get(f"{campaigns_url()}/insights/posts", params=params, timeout=float(os.environ.get("CAMPAIGNS_TIMEOUT", "8")),
                      headers={"X-API-Key": key} if key else {})
    except httpx.HTTPError as exc:
        raise RuntimeError(f"campaign service unreachable: {type(exc).__name__}") from None
    if r.status_code != 200:
        raise RuntimeError(f"campaign service {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        raise RuntimeError("campaign service returned non-JSON") from None
    posts = body.get("posts") if isinstance(body, dict) else None
    if not isinstance(posts, list):
        raise RuntimeError("campaign service returned no posts list")
    posts = [x for x in posts if isinstance(x, dict) and isinstance(x.get("item_id"), int)]
    approved = approved_texts(channel)
    picked, stats = performance.rank(posts, {i: a["text"] for i, a in approved.items()}, k,
                                     datetime.now(timezone.utc), p)
    note = (f"{stats['passed_evidence']} of {stats['settled_posts']} settled posts passed the evidence rule"
            f" (min {p.min_clicks} clicks, clearly above the channel's median, {p.min_posts}+ posts per channel)")
    return [{"text": c["text"], "channel": approved[c["item_id"]]["channel"],
             "decision": approved[c["item_id"]]["decision"], "item_id": c["item_id"],
             "basis": "performance", "clicks": c["clicks"], "hook_style": c.get("hook_style")}
            for c in picked], note


@app.get("/examples")
def examples(response: Response, channel: str | None = None, k: int = Query(3, ge=1, le=10),
             by: Literal["approval", "performance"] = "approval"):
    """Few-shot examples for the writers. `by=approval` (default): the reviewer's edited
    finals first, then approved posts, most recent first. `by=performance`: approved posts
    that earned clearly more clicks than the channel's typical post (app/performance.py),
    the rest of the k slots filled by approval; headers X-Examples-Basis
    (performance | mixed | approval) and X-Examples-Note say what happened."""
    perf: list[dict] = []
    note = ""
    if by == "performance":
        try:
            perf, note = performance_examples(channel, k)
        except RuntimeError as exc:
            note = f"fell back to approval examples: {exc}"
    if by == "approval":
        return [{k_: e[k_] for k_ in ("text", "channel", "decision")} for e in approval_examples(channel, k)]
    out = list(perf)
    if len(out) < k:
        for e in approval_examples(channel, k + len(perf)):
            if len(out) >= k:
                break
            if any(e["item_id"] == o["item_id"] or performance.jaccard(e["text"], o["text"]) >= 0.8 for o in perf):
                continue
            out.append({**e, "basis": "approval"})
    response.headers["X-Examples-Basis"] = ("performance" if perf and len(perf) == len(out)
                                            else "mixed" if perf else "approval")
    response.headers["X-Examples-Note"] = note[:300]
    return out


@app.post("/reflect", dependencies=[Depends(require_key)])
def reflect(req: ReflectRequest | None = None):
    req = req or ReflectRequest()
    cutoff = fmt(datetime.now(timezone.utc) - timedelta(days=req.since_days))
    with db() as conn:
        events = [row_to_event(r) for r in conn.execute(
            "SELECT * FROM events WHERE decision IN ('edited', 'rejected')"
            " AND reflected_at IS NULL AND created_at >= ? ORDER BY id LIMIT ?",
            (cutoff, req.max_events),
        )]

    # Gateway calls are slow (seconds each); don't hold a DB connection across them.
    results, errors = [], []
    for event in events:
        try:
            results.append((event, reflect_one(event)))
        except ReflectError as exc:
            log.warning("reflect event %s failed: %s", event["id"], exc)
            errors.append({"event_id": event["id"], "error": str(exc)})

    created_ids: list[int] = []
    with db() as conn:
        for event, output in results:
            conn.execute("UPDATE events SET reflected_at = ? WHERE id = ?", (now_utc(), event["id"]))
            rule = rule_from_output(output, event)
            if rule is None:
                continue
            text, scope = rule
            norm = normalize_rule(text)
            existing = conn.execute(
                "SELECT * FROM rules WHERE norm = ? ORDER BY id LIMIT 1", (norm,)
            ).fetchone()
            if existing is not None:
                # Already known. A pending one records another source; active/rejected
                # rules are left alone so a rejected rule is never proposed again.
                if existing["status"] == "pending":
                    sources = json.loads(existing["source_event_ids"])
                    if event["id"] not in sources:
                        sources.append(event["id"])
                        conn.execute("UPDATE rules SET source_event_ids = ? WHERE id = ?",
                                     (json.dumps(sources), existing["id"]))
                continue
            cur = conn.execute(
                "INSERT INTO rules (text, norm, scope, status, source_event_ids, created_at)"
                " VALUES (?, ?, ?, 'pending', ?, ?)",
                (text, norm, scope, json.dumps([event["id"]]), now_utc()),
            )
            created_ids.append(cur.lastrowid)
        created = [row_to_rule(conn.execute("SELECT * FROM rules WHERE id = ?", (i,)).fetchone())
                   for i in created_ids]
    return {"created": created, "reflected": len(results), "errors": errors}


@app.get("/rules")
def list_rules(status: str | None = None):
    sql, args = "SELECT * FROM rules", []
    if status:
        if status not in RULE_STATUSES:
            raise HTTPException(422, f"unknown status {status!r}; use {list(RULE_STATUSES)}")
        sql += " WHERE status = ?"
        args.append(status)
    with db() as conn:
        return [row_to_rule(r) for r in conn.execute(sql + " ORDER BY id", args)]


@app.get("/rules/summary")
def rules_summary():
    with db() as conn:
        rows = conn.execute("SELECT * FROM rules WHERE status = 'active' ORDER BY id").fetchall()
    if not rows:
        return {"summary": "", "count": 0}
    lines = ["Rules learned from your edits:"]
    for r in rows:
        prefix = "" if r["scope"] == "all" else f"[{r['scope']}] "
        lines.append(f"- {prefix}{r['text']}")
    return {"summary": "\n".join(lines), "count": len(rows)}


@app.get("/rules/review")
def rules_to_review():
    """What a person decides in form 51: pending rules from reviews, and provisional rules
    from experiments that a later experiment replicated."""
    with db() as conn:
        return [row_to_rule(r) for r in conn.execute(
            "SELECT * FROM rules WHERE status = 'pending' OR (status = 'provisional' AND replicated = 1)"
            " ORDER BY id")]


@app.post("/rules/{rule_id}/status", dependencies=[Depends(require_key)])
def set_rule_status(rule_id: int, req: RuleStatus):
    if req.status not in ("active", "rejected"):
        raise HTTPException(422, "status must be 'active' or 'rejected'")
    with db() as conn:
        row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"rule {rule_id} not found")
        if (req.status == "active" and row["source"] == "experiment" and row["status"] != "active"
                and not (row["status"] == "provisional" and row["replicated"])):
            raise HTTPException(409, f"rule {rule_id} is {row['status']} and not replicated: a rule from an "
                                     "experiment becomes active only after a later experiment agrees")
        conn.execute("UPDATE rules SET status = ? WHERE id = ?", (req.status, rule_id))
        return row_to_rule(conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone())


# ---------- rules from experiments (45)


def exp_key(variable: str, channels: list[str], values: list[str]) -> str:
    """Same variable, same channels, same two values (either order) = the same question."""
    return f"{variable.lower()}|{','.join(channels)}|{'/'.join(sorted(v.casefold() for v in values))}"


def exp_rule_text(r: ExperimentResult) -> str:
    what = {"hook_style": "hooks", "format": "format", "cta": "call to action", "length": "length",
            "time": "posting time (UTC)"}.get(r.variable, r.variable)
    return f"Prefer {r.winner} over {r.loser} ({what}) on {', '.join(r.channels)}."


@app.post("/rules/from-experiment", dependencies=[Depends(require_key)])
def rule_from_experiment(req: ExperimentResult):
    """Turn a decided experiment into playbook evidence (design s. 5, "from result to playbook").

    - winner, no rule yet: a PROVISIONAL rule "prefer X over Y on C" (support 1). Writers
      never see provisional rules (/rules/summary lists active ones only).
    - winner in the same direction from a later experiment: support + 1; a provisional rule
      is then `replicated` and waits for a person in form 51 (/rules/review).
    - winner the other way, or no_practical_difference: contradicts + 1; an active rule is
      demoted to provisional; with contradicts >= support it is retired. A contradicting
      winner then starts its own provisional rule.
    - inconclusive: nothing changes.
    The same experiment is counted once.
    """
    key = exp_key(req.variable, req.channels, req.values)
    ts = now_utc()
    item = {"experiment_id": req.experiment_id, "decision": req.decision, "winner": req.winner,
            "lift_hdi": req.lift_hdi, "decided_at": req.decided_at or ts, "summary": req.summary}
    with db() as conn:
        row = conn.execute("SELECT * FROM rules WHERE exp_key = ? AND status IN ('provisional', 'active', 'rejected')"
                           " ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        if row is not None and req.experiment_id in (json.loads(row["source_experiment_ids"])
                                                     + json.loads(row["contradicting_experiment_ids"])):
            return {"action": "already_counted", "rule": row_to_rule(row)}
        if req.decision == "inconclusive":
            return {"action": "none", "rule": row_to_rule(row) if row else None}

        def create() -> int:
            text = exp_rule_text(req)
            cur = conn.execute(
                "INSERT INTO rules (text, norm, scope, status, source_event_ids, created_at, source, exp_key,"
                " variable, winner, loser, support, contradicts, replicated, source_experiment_ids, evidence,"
                " last_confirmed_at) VALUES (?, ?, ?, 'provisional', '[]', ?, 'experiment', ?, ?, ?, ?, 1, 0, 0, ?, ?, ?)",
                (text, normalize_rule(text), req.channels[0] if len(req.channels) == 1 else "all", ts, key,
                 req.variable, req.winner, req.loser, json.dumps([req.experiment_id]), json.dumps([item]), ts))
            return cur.lastrowid

        if row is None:
            if req.decision != "winner":
                return {"action": "none", "rule": None}
            rid = create()
            return {"action": "created", "rule": row_to_rule(conn.execute("SELECT * FROM rules WHERE id = ?", (rid,)).fetchone())}

        evidence = json.loads(row["evidence"]) + [item]
        if req.decision == "winner" and req.winner.casefold() == (row["winner"] or "").casefold():
            status, replicated = row["status"], row["replicated"]
            if status == "provisional":
                replicated, action = 1, "replicated"
            else:
                action = "confirmed" if status == "active" else "counted"
            conn.execute("UPDATE rules SET support = support + 1, replicated = ?, last_confirmed_at = ?,"
                         " source_experiment_ids = ?, evidence = ? WHERE id = ?",
                         (replicated, ts, json.dumps(json.loads(row["source_experiment_ids"]) + [req.experiment_id]),
                          json.dumps(evidence), row["id"]))
            return {"action": action, "rule": row_to_rule(conn.execute("SELECT * FROM rules WHERE id = ?", (row["id"],)).fetchone())}

        # contradicting: the other arm won, or no practical difference
        contradicts = row["contradicts"] + 1
        status, replicated, action = row["status"], row["replicated"], "contradicted"
        if status == "active":
            status, replicated, action = "provisional", 0, "demoted"
        if contradicts >= row["support"] and status != "rejected":
            status, action = "retired", "retired"
        conn.execute("UPDATE rules SET contradicts = ?, status = ?, replicated = ?, contradicting_experiment_ids = ?,"
                     " evidence = ? WHERE id = ?",
                     (contradicts, status, replicated,
                      json.dumps(json.loads(row["contradicting_experiment_ids"]) + [req.experiment_id]),
                      json.dumps(evidence), row["id"]))
        out = {"action": action, "rule": row_to_rule(conn.execute("SELECT * FROM rules WHERE id = ?", (row["id"],)).fetchone())}
        if req.decision == "winner" and status in ("retired", "rejected"):
            rid = create()
            out["created"] = row_to_rule(conn.execute("SELECT * FROM rules WHERE id = ?", (rid,)).fetchone())
        return out
