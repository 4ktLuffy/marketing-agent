"""Experiments: fixed two-arm tests of one variable, decided in code at preset weekly looks.

Lifecycle: proposed (by the agent or a person) -> approved (by a person, approver key) ->
running (the content engine, 61, assigns planned slots to the arms) -> decided (at a look,
by the HDI + ROPE rule in expstats) | stopped (by a person, any time before a decision).

The metric is clicks within 72 h of publishing, per post, from the link shortener (16):
a post is the calendar item a slot became (utm_content = item id on its short link) and
"published" is when its short link was created (the publisher, 39, creates it then).
Design: _dev/research/experiment-loop.md section 5.
"""
from __future__ import annotations

import hmac
import json
import math
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app import expstats
from app.main import (HOOK_STYLES, as_list, clean_channels, db, get_json, http_client, number,
                      require_key, shortener_url, upstream)

router = APIRouter()

VARIABLES = ("hook_style", "format", "cta", "length", "time")
FORMATS = ("post", "thread", "carousel_text", "blog", "email", "video_script")
METRICS = ("clicks_72h",)
WINDOW_HOURS = 72
STATUSES = ("proposed", "approved", "running", "decided", "stopped")
# status -> statuses a request may move it to. "decided" is set only by /decide.
EXP_TRANSITIONS = {
    "proposed": ["approved", "stopped"],
    "approved": ["running", "stopped"],
    "running": ["stopped"],
    "decided": [],
    "stopped": [],
}
MAX_RUNNING_PER_CHANNEL = 2   # so variants don't collide in the same slots (design s. 5.6)
LINK_LIMIT = 1000
LABELS = ("A", "B")

EXP_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis TEXT NOT NULL,
    variable TEXT NOT NULL,
    channels TEXT NOT NULL,
    metric TEXT NOT NULL,
    min_posts_per_arm INTEGER NOT NULL,
    max_weeks INTEGER NOT NULL,
    rope REAL NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    winner_arm TEXT,
    created_by TEXT NOT NULL,
    approved_by TEXT,
    stop_reason TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    started_at TEXT,
    decided_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    value TEXT NOT NULL,
    brief TEXT,
    UNIQUE (experiment_id, label)
);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    slot_id INTEGER NOT NULL,
    item_id INTEGER,
    channel TEXT NOT NULL,
    date TEXT NOT NULL,
    time_utc TEXT NOT NULL,
    block TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (experiment_id, slot_id)
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    look INTEGER NOT NULL,
    cutoff TEXT NOT NULL,
    decision TEXT NOT NULL,
    analysis TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (experiment_id, look)
);
"""


# ---------- time (tests monkeypatch now())


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def look_days() -> float:
    try:
        return float(os.environ.get("EXPERIMENT_LOOK_DAYS") or 7)
    except ValueError:
        return 7.0


def prior_posts() -> float:
    try:
        return float(os.environ.get("EXPERIMENT_PRIOR_POSTS") or 2)
    except ValueError:
        return 2.0


def engine_url() -> str:
    return upstream("ENGINE_URL", "http://content-engine:8000")


# ---------- storage


@contextmanager
def edb():
    """The campaign DB with the experiment tables."""
    with db() as conn:
        conn.executescript(EXP_SCHEMA)
        yield conn


EXP_FIELDS = ("id", "hypothesis", "variable", "channels", "metric", "min_posts_per_arm", "max_weeks",
              "rope", "status", "decision", "winner_arm", "created_by", "approved_by", "stop_reason",
              "created_at", "approved_at", "started_at", "decided_at", "updated_at")


def load(conn, exp_id: int) -> dict:
    row = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"experiment {exp_id} not found")
    e = {k: row[k] for k in EXP_FIELDS}
    e["channels"] = json.loads(e["channels"])
    e["arms"] = [{"label": v["label"], "value": v["value"], "brief": v["brief"]} for v in conn.execute(
        "SELECT * FROM variants WHERE experiment_id = ? ORDER BY label", (exp_id,))]
    counts = dict(conn.execute("SELECT label, COUNT(*) FROM assignments WHERE experiment_id = ? GROUP BY label",
                               (exp_id,)).fetchall())
    e["assigned"] = {lab: counts.get(lab, 0) for lab in LABELS}
    e["looks"] = [{"look": r["look"], "cutoff": r["cutoff"], "decision": r["decision"], "created_at": r["created_at"]}
                  for r in conn.execute("SELECT look, cutoff, decision, created_at FROM results"
                                        " WHERE experiment_id = ? ORDER BY look", (exp_id,))]
    e["next_look_at"] = next_look(e)
    arm = {a["label"]: a for a in e["arms"]}
    if e["winner_arm"]:
        loser = "B" if e["winner_arm"] == "A" else "A"
        e["winner"] = {"label": e["winner_arm"], "value": arm[e["winner_arm"]]["value"]}
        e["loser"] = {"label": loser, "value": arm[loser]["value"]}
    else:
        e["winner"] = e["loser"] = None
    return e


def next_look(e: dict) -> str | None:
    """When the next look is (or was, if it is due and not recorded yet)."""
    if e["status"] != "running" or not e["started_at"]:
        return None
    k, _ = due_look(e)
    if k >= 1 and k not in {l["look"] for l in e.get("looks", [])}:
        k -= 1   # that look is due now
    if k + 1 > e["max_weeks"]:
        return None
    return iso(parse_ts(e["started_at"]) + timedelta(days=look_days() * (k + 1)))


def due_look(e: dict) -> tuple[int, datetime | None]:
    """(k, cutoff) of the latest preset look that has passed: 0 when none has."""
    if not e["started_at"]:
        return 0, None
    start = parse_ts(e["started_at"])
    elapsed = (now() - start).total_seconds() / 86400.0
    k = min(int(math.floor(elapsed / look_days() + 1e-9)), e["max_weeks"])
    if k < 1:
        return 0, None
    return k, start + timedelta(days=look_days() * k)


# ---------- models


class NewArm(BaseModel):
    value: str = Field(min_length=1, max_length=200)
    brief: str | None = Field(default=None, max_length=1000)


class NewExperiment(BaseModel):
    hypothesis: str = Field(min_length=10, max_length=1000)
    variable: str
    arms: list[NewArm]
    channels: list[str] = Field(min_length=1)
    metric: str = "clicks_72h"
    min_posts_per_arm: int = Field(default=12, ge=4, le=200)
    max_weeks: int = Field(default=8, ge=1, le=26)
    rope: float = Field(default=0.15, gt=0, le=1)
    created_by: str = "human"

    @field_validator("variable")
    @classmethod
    def known_variable(cls, v):
        v = v.strip().lower()
        if v not in VARIABLES:
            raise ValueError(f"unknown variable {v!r}; use one of {list(VARIABLES)}")
        return v

    @field_validator("metric")
    @classmethod
    def known_metric(cls, v):
        v = v.strip().lower()
        if v not in METRICS:
            raise ValueError(f"metric must be one of {list(METRICS)}: clicks per post within 72 h of publishing")
        return v

    @field_validator("created_by")
    @classmethod
    def who(cls, v):
        v = v.strip().lower()
        if v not in ("agent", "human"):
            raise ValueError("created_by must be 'agent' or 'human'")
        return v

    @field_validator("channels")
    @classmethod
    def lower_channels(cls, v):
        v = clean_channels(v)
        if not v:
            raise ValueError("name at least one channel")
        return v

    @model_validator(mode="after")
    def arm_values(self):
        if len(self.arms) != 2:
            raise ValueError("an experiment has exactly 2 arms (one variable, two values)")
        for a in self.arms:
            a.value = a.value.strip()
            if self.variable in ("hook_style", "format"):
                a.value = a.value.lower()
            if self.variable == "hook_style" and a.value not in HOOK_STYLES:
                raise ValueError(f"hook_style arm {a.value!r} is not one of {list(HOOK_STYLES)}")
            if self.variable == "format" and a.value not in FORMATS:
                raise ValueError(f"format arm {a.value!r} is not one of {list(FORMATS)}")
            if self.variable == "time" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", a.value):
                raise ValueError(f"time arm {a.value!r} must be HH:MM (UTC)")
        if self.arms[0].value.casefold() == self.arms[1].value.casefold():
            raise ValueError("the two arms must differ")
        return self


class ExpStatus(BaseModel):
    status: str
    by: str | None = Field(default=None, max_length=100)
    reason: str | None = Field(default=None, max_length=1000)


class SlotAssignment(BaseModel):
    slot_id: int = Field(ge=1)
    arm: str
    channel: str
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    time_utc: str = Field(pattern=r"^\d{2}:\d{2}$")
    item_id: int | None = Field(default=None, ge=1)

    @field_validator("arm")
    @classmethod
    def label(cls, v):
        v = v.strip().upper()
        if v not in LABELS:
            raise ValueError("arm must be 'A' or 'B'")
        return v


class Assign(BaseModel):
    slots: list[SlotAssignment] = Field(default_factory=list, max_length=500)
    remove_slot_ids: list[int] = Field(default_factory=list, max_length=1000)


# ---------- helpers


def require_approver(x_approver_key: str | None) -> None:
    """Approving an experiment needs APPROVER_KEY too, when it is set (as in 19)."""
    expected = os.environ.get("APPROVER_KEY")
    if expected and not (x_approver_key and hmac.compare_digest(x_approver_key, expected)):
        raise HTTPException(403, "approving an experiment needs the approver key (X-Approver-Key)")


def same_test(conn, variable: str, values: set[str], channels: list[str]) -> list[dict]:
    """Earlier experiments of the same variable, the same two values and a shared channel."""
    out = []
    for row in conn.execute("SELECT id FROM experiments WHERE variable = ? ORDER BY id", (variable,)):
        e = load(conn, row["id"])
        if {a["value"].casefold() for a in e["arms"]} == values and set(e["channels"]) & set(channels):
            out.append(e)
    return out


def block_of(date_s: str, time_s: str) -> str:
    """Randomisation block: weekday and hour bucket (morning < 12 <= afternoon < 17 <= evening)."""
    from datetime import date as _date
    wd = _date.fromisoformat(date_s).strftime("%a").lower()
    h = int(time_s[:2])
    return f"{wd}-{'am' if h < 12 else 'pm' if h < 17 else 'eve'}"


def resolve_items(conn, exp_id: int, errors: list[dict]) -> None:
    """Fill item_id of assignments from the content engine (61): the calendar item a slot became."""
    rows = conn.execute("SELECT id, slot_id FROM assignments WHERE experiment_id = ? AND item_id IS NULL",
                        (exp_id,)).fetchall()
    if not rows:
        return
    with http_client() as client:
        for r in rows:
            try:
                slot = get_json(client, f"{engine_url()}/slots/{r['slot_id']}")
            except RuntimeError as e:
                errors.append({"source": "engine", "slot_id": r["slot_id"], "error": str(e)})
                if len(errors) > 5:
                    return
                continue
            item = slot.get("calendar_item_id") if isinstance(slot, dict) else None
            if isinstance(item, int) and slot.get("status") != "dropped":
                conn.execute("UPDATE assignments SET item_id = ? WHERE id = ?", (item, r["id"]))
    conn.commit()


def window_clicks(errors: list[dict]) -> dict[int, dict]:
    """item id -> {published (earliest link created_at), clicks (in the first 72 h, all links)}."""
    out: dict[int, dict] = {}
    with http_client() as client:
        try:
            links = as_list(get_json(client, f"{shortener_url()}/links",
                                     {"limit": LINK_LIMIT, "window_hours": WINDOW_HOURS}), "links")
        except RuntimeError as e:
            errors.append({"source": "shortener", "error": str(e)})
            return out
    for link in links:
        content = dict(parse_qsl(urlsplit(str(link.get("url") or "")).query)).get("utm_content", "")
        created = parse_ts(link.get("created_at") or "")
        if not content.isdigit() or created is None:
            continue
        w = number(link.get("clicks_window"))
        if w is None:
            errors.append({"source": "shortener", "error": "links have no clicks_window (update 16)"})
            return {}
        p = out.setdefault(int(content), {"published": created, "clicks": 0})
        p["published"] = min(p["published"], created)
        p["clicks"] += int(w)
    return out


def compute(conn, e: dict, k: int, cutoff: datetime, errors: list[dict]) -> dict:
    """The analysis at look k: only posts whose 72 h window closed before the cutoff."""
    resolve_items(conn, e["id"], errors)
    clicks = window_clicks(errors)
    per_arm: dict[str, list[int]] = {lab: [] for lab in LABELS}
    pending = {lab: 0 for lab in LABELS}
    unpublished = {lab: 0 for lab in LABELS}
    posts = []
    for r in conn.execute("SELECT * FROM assignments WHERE experiment_id = ? ORDER BY date, time_utc, slot_id",
                          (e["id"],)):
        p = clicks.get(r["item_id"]) if r["item_id"] else None
        if p is None:
            unpublished[r["label"]] += 1
            continue
        if p["published"] + timedelta(hours=WINDOW_HOURS) > cutoff:
            pending[r["label"]] += 1   # window still open at this look: counted at a later one
            continue
        per_arm[r["label"]].append(p["clicks"])
        posts.append({"arm": r["label"], "slot_id": r["slot_id"], "item_id": r["item_id"],
                      "published": iso(p["published"]), "clicks_72h": p["clicks"]})
    final = k >= e["max_weeks"]
    res = expstats.analyze(per_arm["A"], per_arm["B"], rope=e["rope"], min_posts=e["min_posts_per_arm"],
                           final=final, prior_posts=prior_posts(), seed=e["id"] * 1000 + k)
    arm = {a["label"]: a for a in e["arms"]}
    for lab in LABELS:
        res["arms"][lab].update(value=arm[lab]["value"], pending=pending[lab], unpublished=unpublished[lab])
    if res["winner"]:
        loser = "B" if res["winner"] == "A" else "A"
        res["winner_value"], res["loser_value"] = arm[res["winner"]]["value"], arm[loser]["value"]
    res.update(look=k, cutoff=iso(cutoff), posts=posts)
    return res


def summary_line(e: dict, res: dict) -> str:
    a, b = res["arms"]["A"], res["arms"]["B"]
    lo, hi = res["lift_hdi"]
    base = (f"{e['variable']} {a['value']} (A, {a['posts']} posts, {a['clicks_per_post']} clicks/post) vs "
            f"{b['value']} (B, {b['posts']} posts, {b['clicks_per_post']} clicks/post); "
            f"lift of B {lo:+.0%} to {hi:+.0%} (95% HDI), ROPE ±{e['rope']:.0%}")
    if res["decision"] == "winner":
        return f"winner: {res['winner_value']} over {res['loser_value']}. {base}"
    if res["decision"] == "no_practical_difference":
        return f"no practical difference: the choice is free. {base}"
    if res["decision"] == "inconclusive":
        return f"inconclusive at the last look. {base}"
    return f"no verdict yet (look {res['look']} of {e['max_weeks']}). {base}"


# ---------- endpoints


@router.post("/experiments", status_code=201, dependencies=[Depends(require_key)])
def create_experiment(req: NewExperiment):
    ts = iso(now())
    values = {a.value.casefold() for a in req.arms}
    with edb() as conn:
        for old in same_test(conn, req.variable, values, req.channels):
            if old["status"] in ("proposed", "approved", "running"):
                raise HTTPException(409, {"message": f"experiment {old['id']} already tests this ({old['status']})",
                                          "experiment_id": old["id"], "status": old["status"]})
            if old["decision"] == "no_practical_difference":
                # Stored so the agent doesn't re-test it: the choice is free.
                raise HTTPException(409, {"message": f"experiment {old['id']} found no practical difference "
                                                     f"between these; not re-running it",
                                          "experiment_id": old["id"], "decision": old["decision"]})
        cur = conn.execute(
            "INSERT INTO experiments (hypothesis, variable, channels, metric, min_posts_per_arm, max_weeks, rope,"
            " status, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)",
            (req.hypothesis.strip(), req.variable, json.dumps(req.channels), req.metric, req.min_posts_per_arm,
             req.max_weeks, req.rope, req.created_by, ts, ts))
        for lab, a in zip(LABELS, req.arms):
            conn.execute("INSERT INTO variants (experiment_id, label, value, brief) VALUES (?, ?, ?, ?)",
                         (cur.lastrowid, lab, a.value, (a.brief or "").strip() or None))
        return load(conn, cur.lastrowid)


@router.get("/experiments")
def list_experiments(status: str | None = None, channel: str | None = None):
    wanted = [s.strip().lower() for s in (status or "").split(",") if s.strip()]
    bad = [s for s in wanted if s not in STATUSES]
    if bad:
        raise HTTPException(422, f"unknown status {bad[0]!r}; use {list(STATUSES)}")
    with edb() as conn:
        out = [load(conn, r["id"]) for r in conn.execute("SELECT id FROM experiments ORDER BY id")]
    if wanted:
        out = [e for e in out if e["status"] in wanted]
    if channel:
        out = [e for e in out if channel.strip().lower() in e["channels"]]
    return out


@router.get("/experiments/{exp_id}")
def get_experiment(exp_id: int):
    with edb() as conn:
        return load(conn, exp_id)


def running_on(conn, channels: list[str], except_id: int) -> dict[str, int]:
    n = {}
    for r in conn.execute("SELECT id, channels FROM experiments WHERE status = 'running' AND id != ?", (except_id,)):
        for ch in json.loads(r["channels"]):
            if ch in channels:
                n[ch] = n.get(ch, 0) + 1
    return n


def start(conn, e: dict) -> None:
    full = {ch: n for ch, n in running_on(conn, e["channels"], e["id"]).items() if n >= MAX_RUNNING_PER_CHANNEL}
    if full:
        raise HTTPException(409, f"{', '.join(sorted(full))} already has {MAX_RUNNING_PER_CHANNEL} running "
                                 f"experiments; wait for one to be decided or stopped")
    ts = iso(now())
    conn.execute("UPDATE experiments SET status = 'running', started_at = ?, updated_at = ? WHERE id = ?",
                 (ts, ts, e["id"]))


@router.post("/experiments/{exp_id}/status", dependencies=[Depends(require_key)])
def set_status(exp_id: int, req: ExpStatus, x_approver_key: str | None = Header(default=None)):
    target = req.status.strip().lower()
    if target not in STATUSES:
        raise HTTPException(422, f"unknown status {target!r}; use {list(STATUSES)}")
    if target == "decided":
        raise HTTPException(422, "an experiment is decided by POST /experiments/{id}/decide at a look, never by hand")
    if target == "approved":
        require_approver(x_approver_key)
    with edb() as conn:
        e = load(conn, exp_id)
        if target not in EXP_TRANSITIONS[e["status"]]:
            raise HTTPException(409, {"message": f"experiment {exp_id} is {e['status']}; it can't become {target}",
                                      "current": e["status"], "allowed": EXP_TRANSITIONS[e["status"]]})
        ts = iso(now())
        if target == "approved":
            conn.execute("UPDATE experiments SET status = 'approved', approved_by = ?, approved_at = ?, updated_at = ?"
                         " WHERE id = ?", ((req.by or "").strip() or "reviewer", ts, ts, exp_id))
        elif target == "running":
            start(conn, e)
        else:
            conn.execute("UPDATE experiments SET status = 'stopped', stop_reason = ?, updated_at = ? WHERE id = ?",
                         ((req.reason or "").strip() or None, ts, exp_id))
        return load(conn, exp_id)


@router.post("/experiments/{exp_id}/assign", dependencies=[Depends(require_key)])
def assign(exp_id: int, req: Assign):
    """Record which arm each planned slot got (the planner, 61, balances them).

    Upsert on (experiment, slot). `remove_slot_ids`: slots a re-plan deleted. The first
    assignment starts an approved experiment (status running, started_at = now): its
    weekly looks count from then.
    """
    with edb() as conn:
        e = load(conn, exp_id)
        if e["status"] not in ("approved", "running"):
            raise HTTPException(409, f"experiment {exp_id} is {e['status']}; only approved or running "
                                     f"experiments get slots")
        bad = sorted({s.channel.lower() for s in req.slots} - set(e["channels"]))
        if bad:
            raise HTTPException(422, f"experiment {exp_id} does not run on {', '.join(bad)} "
                                     f"(channels: {', '.join(e['channels'])})")
        if e["status"] == "approved" and req.slots:
            start(conn, e)
        removed = 0
        for sid in req.remove_slot_ids:
            removed += conn.execute("DELETE FROM assignments WHERE experiment_id = ? AND slot_id = ?",
                                    (exp_id, sid)).rowcount
        ts = iso(now())
        for s in req.slots:
            conn.execute(
                "INSERT INTO assignments (experiment_id, label, slot_id, item_id, channel, date, time_utc, block,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (experiment_id, slot_id) DO UPDATE SET label = excluded.label,"
                " item_id = COALESCE(excluded.item_id, assignments.item_id), channel = excluded.channel,"
                " date = excluded.date, time_utc = excluded.time_utc, block = excluded.block",
                (exp_id, s.arm, s.slot_id, s.item_id, s.channel.lower(), s.date, s.time_utc,
                 block_of(s.date, s.time_utc), ts))
        e = load(conn, exp_id)
    return {"experiment_id": exp_id, "status": e["status"], "assigned": len(req.slots), "removed": removed,
            "by_arm": e["assigned"], "started_at": e["started_at"]}


@router.get("/experiments/{exp_id}/assignments")
def list_assignments(exp_id: int):
    with edb() as conn:
        load(conn, exp_id)
        return [dict(r) for r in conn.execute(
            "SELECT experiment_id, label AS arm, slot_id, item_id, channel, date, time_utc, block FROM assignments"
            " WHERE experiment_id = ? ORDER BY date, time_utc, slot_id", (exp_id,))]


def analysis_of(conn, e: dict) -> dict:
    """The analysis at the latest preset look, or why there is none yet."""
    if e["status"] == "decided":
        row = conn.execute("SELECT analysis FROM results WHERE experiment_id = ? ORDER BY look DESC LIMIT 1",
                           (e["id"],)).fetchone()
        res = json.loads(row["analysis"])
        return {**res, "experiment_id": e["id"], "status": e["status"], "due": False, "recorded": True}
    if e["status"] != "running":
        raise HTTPException(409, f"experiment {e['id']} is {e['status']}; only running experiments are analysed")
    k, cutoff = due_look(e)
    recorded = {l["look"] for l in e["looks"]}
    if k == 0:
        # Never peek early: before the first preset look there is no verdict and no lift.
        return {"experiment_id": e["id"], "status": e["status"], "look": 0, "due": False, "recorded": False,
                "decision": "not_due", "next_look_at": e["next_look_at"], "assigned": e["assigned"]}
    if k in recorded:
        row = conn.execute("SELECT analysis FROM results WHERE experiment_id = ? AND look = ?",
                           (e["id"], k)).fetchone()
        return {**json.loads(row["analysis"]), "experiment_id": e["id"], "status": e["status"], "due": False,
                "recorded": True, "next_look_at": e["next_look_at"]}
    errors: list[dict] = []
    res = compute(conn, e, k, cutoff, errors)
    res["summary"] = summary_line(e, res)
    return {**res, "experiment_id": e["id"], "status": e["status"], "due": True, "recorded": False,
            "next_look_at": e["next_look_at"], "errors": errors}


@router.get("/experiments/{exp_id}/analysis")
def analysis(exp_id: int):
    """Per-arm Gamma-Poisson posteriors, the 95% HDI of the relative lift, and the rule's
    verdict, as of the latest preset look (weekly from started_at, and at max_weeks)."""
    with edb() as conn:
        return analysis_of(conn, load(conn, exp_id))


@router.post("/experiments/{exp_id}/decide", dependencies=[Depends(require_key)])
def decide(exp_id: int):
    """Record the verdict at the due look. winner / no_practical_difference / inconclusive
    end the experiment (decided); continue waits for the next look. 409 when no look is due."""
    with edb() as conn:
        e = load(conn, exp_id)
        res = analysis_of(conn, e)
        if not res.get("due"):
            raise HTTPException(409, {"message": f"no look due for experiment {exp_id}",
                                      "status": e["status"], "next_look_at": e["next_look_at"],
                                      "decision": res.get("decision")})
        if any(err["source"] == "shortener" for err in res.get("errors", [])):
            raise HTTPException(502, {"message": "the link shortener could not be read; nothing recorded",
                                      "errors": res["errors"]})
        ts = iso(now())
        stored = {k: v for k, v in res.items() if k not in ("due", "recorded", "status", "errors")}
        conn.execute("INSERT INTO results (experiment_id, look, cutoff, decision, analysis, created_at)"
                     " VALUES (?, ?, ?, ?, ?, ?)", (exp_id, res["look"], res["cutoff"], res["decision"],
                                                   json.dumps(stored), ts))
        if res["decision"] != "continue":
            conn.execute("UPDATE experiments SET status = 'decided', decision = ?, winner_arm = ?, decided_at = ?,"
                         " updated_at = ? WHERE id = ?", (res["decision"], res["winner"], ts, ts, exp_id))
        e = load(conn, exp_id)
    return {**res, "status": e["status"], "recorded": True, "due": False, "experiment": e}
