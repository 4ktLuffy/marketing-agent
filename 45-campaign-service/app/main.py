"""Campaign service: goals, targets and measured results for each campaign."""
import hmac
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

app = FastAPI(title="campaign-service")

GOAL_TYPES = ("awareness", "traffic", "leads", "sales", "retention")
STATUSES = ("planned", "active", "paused", "completed", "cancelled")
TRANSITIONS = {
    "planned": ["active", "cancelled"],
    "active": ["paused", "completed"],
    "paused": ["active", "completed", "cancelled"],
    "completed": [],
    "cancelled": [],
}
METRICS = ("clicks", "sessions", "conversions", "signups", "revenue", "ctr", "cvr", "open_rate")
SOURCES = ("shortener", "analytics", "manual")
DEFAULT_SOURCE = {
    "clicks": "shortener",
    "sessions": "analytics", "conversions": "analytics", "ctr": "analytics", "cvr": "analytics",
}
# Which metrics each automatic source can deliver. analytics totals also carry clicks.
SOURCE_METRICS = {
    "shortener": {"clicks"},
    "analytics": {"clicks", "sessions", "conversions", "ctr", "cvr"},
    "manual": set(METRICS),
}
MEDIUM_BY_CHANNEL = {"email": "email", "google_ads": "cpc", "blog": "referral"}
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")
SLUG_MAX = 40
LINK_LIMIT = 500

CAMPAIGN_FIELDS = (
    "id", "slug", "name", "goal_type", "goal_text", "audience", "offer", "landing_url",
    "channels", "start_date", "end_date", "status", "budget", "notes", "created_at", "updated_at",
)
KPI_FIELDS = (
    "id", "campaign_id", "metric", "target_value", "baseline_value", "source",
    "actual_value", "measured_at",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    goal_type TEXT NOT NULL,
    goal_text TEXT NOT NULL,
    audience TEXT NOT NULL,
    offer TEXT,
    landing_url TEXT,
    channels TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL,
    budget REAL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kpis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    target_value REAL NOT NULL,
    baseline_value REAL,
    source TEXT NOT NULL,
    actual_value REAL,
    measured_at TEXT,
    UNIQUE (campaign_id, metric)
);
"""


# ---------- time


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> date:
    """The current UTC date. Tests monkeypatch this."""
    return datetime.now(timezone.utc).date()


def parse_day(value: str, name: str) -> date:
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be a date YYYY-MM-DD, got {value!r}")


# ---------- text helpers


def slugify(value: str) -> str:
    """Like utm-builder's normalize, but only [a-z0-9-] and at most 40 characters."""
    value = value.strip().lower()
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:SLUG_MAX].strip("-")


def normalize(value: str) -> str:
    """utm-builder's normalize: lowercase, spaces to '-', keep a-z 0-9 _ - ."""
    value = value.strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9_\-.]", "", value)
    return re.sub(r"-{2,}", "-", value).strip("-")


def clean_channels(values: list[str]) -> list[str]:
    out = []
    for v in values:
        v = normalize(v)
        if v and v not in out:
            out.append(v)
    return out


# ---------- storage


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/campaigns.sqlite")


@contextmanager
def db():
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_kpi(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in KPI_FIELDS}


def load_kpis(conn: sqlite3.Connection, campaign_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM kpis WHERE campaign_id = ? ORDER BY id", (campaign_id,))
    return [row_to_kpi(r) for r in rows]


def row_to_campaign(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    c = {k: row[k] for k in CAMPAIGN_FIELDS}
    c["channels"] = json.loads(c["channels"])
    c["kpis"] = load_kpis(conn, c["id"])
    return c


def get_or_404(conn: sqlite3.Connection, campaign_id: int) -> dict:
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"campaign {campaign_id} not found")
    return row_to_campaign(conn, row)


def kpi_or_404(conn: sqlite3.Connection, campaign_id: int, kpi_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM kpis WHERE id = ? AND campaign_id = ?", (kpi_id, campaign_id)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"kpi {kpi_id} not found on campaign {campaign_id}")
    return row_to_kpi(row)


def update_campaign(conn: sqlite3.Connection, campaign_id: int, fields: dict) -> dict:
    fields = {**fields, "updated_at": now_utc()}
    if "channels" in fields:
        fields["channels"] = json.dumps(fields["channels"])
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE campaigns SET {sets} WHERE id = ?", (*fields.values(), campaign_id))
    return get_or_404(conn, campaign_id)


def slug_taken(conn: sqlite3.Connection, slug: str, except_id: int | None = None) -> bool:
    row = conn.execute("SELECT id FROM campaigns WHERE slug = ?", (slug,)).fetchone()
    return row is not None and row["id"] != except_id


def insert_kpi(conn: sqlite3.Connection, campaign_id: int, k: "NewKpi") -> dict:
    exists = conn.execute(
        "SELECT id FROM kpis WHERE campaign_id = ? AND metric = ?", (campaign_id, k.metric)
    ).fetchone()
    if exists:
        raise HTTPException(409, f"campaign {campaign_id} already has a {k.metric} KPI (id {exists['id']})")
    cur = conn.execute(
        "INSERT INTO kpis (campaign_id, metric, target_value, baseline_value, source)"
        " VALUES (?, ?, ?, ?, ?)",
        (campaign_id, k.metric, k.target_value, k.baseline_value, k.source),
    )
    return row_to_kpi(conn.execute("SELECT * FROM kpis WHERE id = ?", (cur.lastrowid,)).fetchone())


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- upstream services


def upstream(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


def shortener_url() -> str:
    return upstream("SHORTENER_URL", "http://link-shortener:8000")


def analytics_url() -> str:
    return upstream("ANALYTICS_URL", "http://analytics-ingest:8000")


def calendar_url() -> str:
    return upstream("CALENDAR_URL", "http://content-calendar:8000")


def http_client() -> httpx.Client:
    key = os.environ.get("INTERNAL_API_KEY")
    headers = {"X-API-Key": key} if key else {}
    return httpx.Client(timeout=10, headers=headers)


def get_json(client: httpx.Client, url: str, params: dict | None = None):
    """GET and parse JSON. Raises RuntimeError with a short reason on any failure."""
    try:
        r = client.get(url, params=params)
    except httpx.HTTPError as e:
        raise RuntimeError(f"{type(e).__name__}: {e}") from None
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError("response is not JSON") from None


def as_list(data, key: str) -> list:
    """Accept a bare list or {key: [...]}."""
    if isinstance(data, dict):
        data = data.get(key, [])
    if not isinstance(data, list):
        raise RuntimeError("unexpected response shape")
    return [x for x in data if isinstance(x, dict)]


def number(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- models


class NewKpi(BaseModel):
    metric: str
    target_value: float
    baseline_value: float | None = None
    source: str | None = None

    @field_validator("metric")
    @classmethod
    def known_metric(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in METRICS:
            raise ValueError(f"unknown metric {v!r}; use one of {list(METRICS)}")
        return v

    @model_validator(mode="after")
    def pick_source(self):
        if self.source is None:
            self.source = DEFAULT_SOURCE.get(self.metric, "manual")
        self.source = self.source.strip().lower()
        if self.source not in SOURCES:
            raise ValueError(f"unknown source {self.source!r}; use one of {list(SOURCES)}")
        if self.metric not in SOURCE_METRICS[self.source]:
            raise ValueError(f"source {self.source!r} cannot measure {self.metric!r}")
        return self


class KpiPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_value: float | None = None
    baseline_value: float | None = None
    actual_value: float | None = None


def _check_url(v):
    if v is None or v == "":
        return None
    parts = urlsplit(v.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("must be an absolute http(s) URL")
    return v.strip()


class CampaignFields(BaseModel):
    """Validators shared by create and patch."""

    @field_validator("goal_type", check_fields=False)
    @classmethod
    def known_goal(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if v not in GOAL_TYPES:
            raise ValueError(f"unknown goal_type {v!r}; use one of {list(GOAL_TYPES)}")
        return v

    @field_validator("start_date", "end_date", check_fields=False)
    @classmethod
    def iso_day(cls, v, info):
        if v is not None:
            parse_day(v, info.field_name)
        return v

    @field_validator("landing_url", check_fields=False)
    @classmethod
    def http_url(cls, v):
        return _check_url(v)

    @field_validator("channels", check_fields=False)
    @classmethod
    def lower_channels(cls, v):
        return None if v is None else clean_channels(v)


class NewCampaign(CampaignFields):
    name: str
    slug: str | None = None
    goal_type: str
    goal_text: str
    audience: str
    offer: str | None = None
    landing_url: str | None = None
    channels: list[str]
    start_date: str
    end_date: str
    budget: float | None = None
    notes: str | None = None
    kpis: list[NewKpi] = []

    @field_validator("name", "goal_text", "audience")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def date_order(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class CampaignPatch(CampaignFields):
    model_config = ConfigDict(extra="forbid")  # status via /status, kpis via /kpis

    name: str | None = None
    slug: str | None = None
    goal_type: str | None = None
    goal_text: str | None = None
    audience: str | None = None
    offer: str | None = None
    landing_url: str | None = None
    channels: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    budget: float | None = None
    notes: str | None = None


class StatusChange(BaseModel):
    status: str


class LinkRequest(BaseModel):
    channel: str
    content: str | None = None
    url: str | None = None


# ---------- scorecard logic


def elapsed_pct(c: dict) -> float:
    """Share of the campaign's days that have passed, 0..100. Before the start it is 0."""
    start, end = date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"])
    total = (end - start).days + 1
    done = (today() - start).days
    return round(min(max(done / total * 100, 0.0), 100.0), 1)


def kpi_state(k: dict, elapsed: float) -> tuple[float | None, str]:
    actual, target = k["actual_value"], k["target_value"]
    if actual is None:
        return None, "not_measured"
    progress = round(actual / target * 100, 1) if target else None
    if actual >= target:
        return progress, "met"
    if progress is not None and progress >= elapsed:
        return progress, "on_track"
    return progress, "behind"


def scorecard(campaign_id: int, errors: list | None = None) -> dict:
    errors = list(errors or [])
    with db() as conn:
        c = get_or_404(conn, campaign_id)
    elapsed = elapsed_pct(c)
    kpis = []
    for k in c["kpis"]:
        progress, state = kpi_state(k, elapsed)
        kpis.append({
            "id": k["id"], "metric": k["metric"], "source": k["source"],
            "target_value": k["target_value"], "baseline_value": k["baseline_value"],
            "actual_value": k["actual_value"], "measured_at": k["measured_at"],
            "progress_pct": progress, "state": state,
        })
    assets: dict[str, int] = {}
    try:
        with http_client() as client:
            items = as_list(get_json(client, f"{calendar_url()}/items",
                                     {"campaign_id": campaign_id}), "items")
        for it in items:
            s = str(it.get("status") or "unknown")
            assets[s] = assets.get(s, 0) + 1
    except RuntimeError as e:
        errors.append({"source": "calendar", "error": str(e)})
    return {"campaign": c, "elapsed_pct": elapsed, "kpis": kpis, "assets": assets, "errors": errors}


# ---------- endpoints


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/campaigns", status_code=201, dependencies=[Depends(require_key)])
def create_campaign(req: NewCampaign):
    slug = slugify(req.slug if req.slug is not None else req.name)
    if not slug:
        raise HTTPException(422, "slug is empty after slugifying; send a slug with a-z 0-9 -")
    ts = now_utc()
    with db() as conn:
        if slug_taken(conn, slug):
            raise HTTPException(409, f"slug {slug!r} is already used by another campaign")
        cur = conn.execute(
            "INSERT INTO campaigns (slug, name, goal_type, goal_text, audience, offer, landing_url,"
            " channels, start_date, end_date, status, budget, notes, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?)",
            (slug, req.name, req.goal_type, req.goal_text, req.audience, req.offer,
             req.landing_url, json.dumps(req.channels), req.start_date, req.end_date,
             req.budget, req.notes, ts, ts),
        )
        for k in req.kpis:
            insert_kpi(conn, cur.lastrowid, k)
        return get_or_404(conn, cur.lastrowid)


@app.get("/campaigns")
def list_campaigns(status: str | None = None):
    wanted = [s.strip() for s in (status or "").split(",") if s.strip()]
    unknown = [s for s in wanted if s not in STATUSES]
    if unknown:
        raise HTTPException(422, f"unknown status {unknown}; use {list(STATUSES)}")
    sql, args = "SELECT * FROM campaigns", []
    if wanted:
        sql += f" WHERE status IN ({','.join('?' * len(wanted))})"
        args = wanted
    sql += " ORDER BY start_date, id"
    with db() as conn:
        return [row_to_campaign(conn, r) for r in conn.execute(sql, args).fetchall()]


@app.get("/campaigns/by-slug/{slug}")
def get_by_slug(slug: str):
    with db() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            raise HTTPException(404, f"campaign {slug!r} not found")
        return row_to_campaign(conn, row)


@app.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int):
    with db() as conn:
        return get_or_404(conn, campaign_id)


@app.patch("/campaigns/{campaign_id}", dependencies=[Depends(require_key)])
def patch_campaign(campaign_id: int, req: CampaignPatch):
    changes = {k: getattr(req, k) for k in req.model_fields_set}
    for k in ("name", "goal_type", "goal_text", "audience", "channels", "start_date", "end_date"):
        if k in changes and (changes[k] is None or (isinstance(changes[k], str) and not changes[k].strip())):
            raise HTTPException(422, f"{k} must not be empty")
    with db() as conn:
        c = get_or_404(conn, campaign_id)
        if not changes:
            return c
        if "slug" in changes:
            slug = slugify(changes["slug"] or "")
            if not slug:
                raise HTTPException(422, "slug is empty after slugifying")
            if slug == c["slug"]:
                changes.pop("slug")
            else:
                if c["status"] != "planned":
                    raise HTTPException(409, {
                        "message": f"slug is locked once a campaign leaves planned; this one is {c['status']}",
                        "current": c["status"],
                    })
                if slug_taken(conn, slug, except_id=campaign_id):
                    raise HTTPException(409, f"slug {slug!r} is already used by another campaign")
                changes["slug"] = slug
        start = changes.get("start_date", c["start_date"])
        end = changes.get("end_date", c["end_date"])
        if end < start:
            raise HTTPException(422, "end_date must be on or after start_date")
        if not changes:
            return c
        return update_campaign(conn, campaign_id, changes)


@app.post("/campaigns/{campaign_id}/status", dependencies=[Depends(require_key)])
def change_status(campaign_id: int, req: StatusChange):
    target = req.status.strip().lower()
    if target not in STATUSES:
        raise HTTPException(422, f"unknown status {req.status!r}; use {list(STATUSES)}")
    with db() as conn:
        c = get_or_404(conn, campaign_id)
        current, allowed = c["status"], TRANSITIONS[c["status"]]
        if target not in allowed:
            raise HTTPException(409, {
                "message": f"cannot move from {current} to {target}",
                "current": current,
                "allowed": allowed,
            })
        if target == "active" and not c["kpis"]:
            raise HTTPException(409, {
                "message": "add at least one KPI (a target) before activating the campaign",
                "current": current,
                "allowed": allowed,
            })
        return update_campaign(conn, campaign_id, {"status": target})


@app.post("/campaigns/{campaign_id}/kpis", status_code=201, dependencies=[Depends(require_key)])
def add_kpi(campaign_id: int, req: NewKpi):
    with db() as conn:
        get_or_404(conn, campaign_id)
        kpi = insert_kpi(conn, campaign_id, req)
        conn.execute("UPDATE campaigns SET updated_at = ? WHERE id = ?", (now_utc(), campaign_id))
        return kpi


@app.patch("/campaigns/{campaign_id}/kpis/{kpi_id}", dependencies=[Depends(require_key)])
def patch_kpi(campaign_id: int, kpi_id: int, req: KpiPatch):
    changes = {k: getattr(req, k) for k in req.model_fields_set}
    if "target_value" in changes and changes["target_value"] is None:
        raise HTTPException(422, "target_value must not be empty")
    with db() as conn:
        get_or_404(conn, campaign_id)
        kpi = kpi_or_404(conn, campaign_id, kpi_id)
        if not changes:
            return kpi
        if "actual_value" in changes:
            changes["measured_at"] = now_utc() if changes["actual_value"] is not None else None
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE kpis SET {sets} WHERE id = ?", (*changes.values(), kpi_id))
        conn.execute("UPDATE campaigns SET updated_at = ? WHERE id = ?", (now_utc(), campaign_id))
        return kpi_or_404(conn, campaign_id, kpi_id)


@app.delete("/campaigns/{campaign_id}/kpis/{kpi_id}", dependencies=[Depends(require_key)])
def delete_kpi(campaign_id: int, kpi_id: int):
    with db() as conn:
        c = get_or_404(conn, campaign_id)
        kpi_or_404(conn, campaign_id, kpi_id)
        if c["status"] == "active" and len(c["kpis"]) == 1:
            raise HTTPException(409, "an active campaign must keep at least one KPI")
        conn.execute("DELETE FROM kpis WHERE id = ?", (kpi_id,))
        conn.execute("UPDATE campaigns SET updated_at = ? WHERE id = ?", (now_utc(), campaign_id))
        return {"deleted": kpi_id}


@app.post("/campaigns/{campaign_id}/link")
def build_link(campaign_id: int, req: LinkRequest):
    with db() as conn:
        c = get_or_404(conn, campaign_id)
    target = (req.url or "").strip() or c["landing_url"]
    if not target:
        raise HTTPException(422, "campaign has no landing_url; send url")
    parts = urlsplit(target)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(422, "url must be absolute http(s)")
    channel = normalize(req.channel)
    if not channel:
        raise HTTPException(422, "channel is empty after normalizing")
    params = {
        "utm_source": channel,
        "utm_medium": MEDIUM_BY_CHANNEL.get(channel, "social"),
        "utm_campaign": c["slug"],
    }
    if req.content is not None and normalize(req.content):
        params["utm_content"] = normalize(req.content)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in UTM_KEYS]
    query = urlencode(kept + list(params.items()))
    url = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", query, parts.fragment))
    return {"url": url, "params": params}


@app.post("/campaigns/{campaign_id}/measure", dependencies=[Depends(require_key)])
def measure(campaign_id: int):
    with db() as conn:
        c = get_or_404(conn, campaign_id)
    by_source: dict[str, list[dict]] = {}
    for k in c["kpis"]:
        by_source.setdefault(k["source"], []).append(k)

    errors: list[dict] = []
    actuals: dict[int, float] = {}
    with http_client() as client:
        if "shortener" in by_source:
            try:
                links = as_list(get_json(client, f"{shortener_url()}/links",
                                         {"utm_campaign": c["slug"], "limit": LINK_LIMIT}), "links")
                total = sum(number(l.get("clicks")) or 0 for l in links)
                for k in by_source["shortener"]:
                    actuals[k["id"]] = total
            except RuntimeError as e:
                errors.append({"source": "shortener", "metrics": [k["metric"] for k in by_source["shortener"]],
                               "error": str(e)})
        if "analytics" in by_source:
            metrics = [k["metric"] for k in by_source["analytics"]]
            start = date.fromisoformat(c["start_date"])
            to = min(date.fromisoformat(c["end_date"]), today())
            if to < start:
                errors.append({"source": "analytics", "metrics": metrics,
                               "error": f"campaign starts {c['start_date']}; nothing to measure yet"})
            else:
                try:
                    data = get_json(client, f"{analytics_url()}/kpis", {
                        "from": start.isoformat(), "to": to.isoformat(), "campaign": c["slug"]})
                    totals = data.get("totals") if isinstance(data, dict) else None
                    if not isinstance(totals, dict):
                        raise RuntimeError("response has no totals")
                    missing = []
                    for k in by_source["analytics"]:
                        v = number(totals.get(k["metric"]))
                        if v is None:
                            missing.append(k["metric"])
                        else:
                            actuals[k["id"]] = v
                    if missing:
                        errors.append({"source": "analytics", "metrics": missing,
                                       "error": "no value in totals"})
                except RuntimeError as e:
                    errors.append({"source": "analytics", "metrics": metrics, "error": str(e)})

    if actuals:
        ts = now_utc()
        with db() as conn:
            for kid, v in actuals.items():
                conn.execute("UPDATE kpis SET actual_value = ?, measured_at = ? WHERE id = ?", (v, ts, kid))
            conn.execute("UPDATE campaigns SET updated_at = ? WHERE id = ?", (ts, campaign_id))
    return scorecard(campaign_id, errors)


@app.get("/campaigns/{campaign_id}/scorecard")
def get_scorecard(campaign_id: int):
    return scorecard(campaign_id)


@app.get("/report/unmeasured")
def unmeasured():
    day = today().isoformat()
    out = []
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE status != 'cancelled'"
            " AND (end_date < ? OR status = 'completed') ORDER BY end_date, id",
            (day,),
        ).fetchall()
        for r in rows:
            c = row_to_campaign(conn, r)
            missing = [k["metric"] for k in c["kpis"] if k["actual_value"] is None]
            if missing:
                out.append({**c, "unmeasured": missing})
    return out


@app.get("/insights")
def insights(days: int = 90):
    if days < 1:
        raise HTTPException(422, "days must be at least 1")
    since = (today() - timedelta(days=days)).isoformat()  # links created on/after this day
    errors: list[dict] = []
    posts: dict[int, dict] = {}
    with http_client() as client:
        try:
            links = as_list(get_json(client, f"{shortener_url()}/links", {"limit": LINK_LIMIT}), "links")
        except RuntimeError as e:
            return {"by_channel": [], "top_posts": [], "errors": [{"source": "shortener", "error": str(e)}]}
        for link in links:
            created = str(link.get("created_at") or "")
            if created and created[:10] < since:
                continue
            q = dict(parse_qsl(urlsplit(str(link.get("url") or "")).query))
            content = q.get("utm_content", "")
            if not content.isdigit():
                continue
            item_id = int(content)
            p = posts.setdefault(item_id, {"item_id": item_id, "title": None,
                                           "channel": q.get("utm_source") or None, "clicks": 0})
            p["clicks"] += int(number(link.get("clicks")) or 0)

        cache: dict[int, dict | None] = {}
        for item_id, p in posts.items():
            if item_id not in cache:
                try:
                    item = get_json(client, f"{calendar_url()}/items/{item_id}")
                    cache[item_id] = item if isinstance(item, dict) else None
                except RuntimeError as e:
                    cache[item_id] = None
                    errors.append({"source": "calendar", "item_id": item_id, "error": str(e)})
            item = cache[item_id]
            if item:
                p["title"] = item.get("title")
                p["channel"] = item.get("channel") or p["channel"]

    channels: dict[str, dict] = {}
    for p in posts.values():
        ch = p["channel"] or "unknown"
        agg = channels.setdefault(ch, {"channel": ch, "posts": 0, "clicks": 0})
        agg["posts"] += 1
        agg["clicks"] += p["clicks"]
    by_channel = [{**a, "avg_clicks": round(a["clicks"] / a["posts"], 1)} for a in channels.values()]
    by_channel.sort(key=lambda a: (-a["clicks"], a["channel"]))
    top = sorted(posts.values(), key=lambda p: (-p["clicks"], p["item_id"]))[:10]
    return {"by_channel": by_channel, "top_posts": top, "errors": errors}
