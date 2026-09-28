"""ads-sync: paid-ads reporting. Spend, CPL, ROAS and pacing alerts from the ad platforms'
official reporting APIs, READ-ONLY. Nothing here can change a budget, a bid or an ad.

- Meta Marketing API Insights (GET /act_<id>/insights, daily rows, campaign/adset/ad level).
- Google Ads API (REST googleAds:searchStream, GAQL over `campaign`, daily rows).
- LinkedIn Ads: not implemented (README says why).

Rows are normalized per platform / account / level / entity / day: spend, impressions, clicks,
conversions, revenue, currency. Every derived number (CTR, CPC, CPL, ROAS, deltas, pacing) is
computed here in code. Platform campaigns map to our campaigns (45) by an explicit mapping, the
utm_campaign in the campaign's URL settings, or our slug inside the campaign name.
"""
from __future__ import annotations

import csv
import hmac
import io
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import metrics as M
from .connectors import (DEFAULT_GOOGLE_ADS_VERSION, DEFAULT_GRAPH_VERSION, META_LEVELS, TIMEOUT, AdsError, Google,
                         Meta, meta_act)

app = FastAPI(title="ads-sync")
log = logging.getLogger("ads-sync")
# httpx logs request URLs at INFO. Our URLs carry no token, but keep third-party noise out anyway.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

PLATFORMS = ("meta", "google")
SECRET_VARS = ("META_ACCESS_TOKEN", "GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_SECRET",
               "GOOGLE_ADS_REFRESH_TOKEN", "INTERNAL_API_KEY")

SCHEMA = """
CREATE TABLE IF NOT EXISTS rows (
    platform TEXT NOT NULL, account_id TEXT NOT NULL, level TEXT NOT NULL,
    entity_id TEXT NOT NULL, date TEXT NOT NULL,
    entity_name TEXT NOT NULL, campaign_id TEXT NOT NULL, campaign_name TEXT NOT NULL,
    currency TEXT NOT NULL,
    spend REAL NOT NULL, impressions INTEGER NOT NULL, clicks INTEGER NOT NULL,
    conversions REAL NOT NULL, revenue REAL NOT NULL,
    utm_campaign TEXT, synced_at TEXT NOT NULL,
    PRIMARY KEY (platform, account_id, level, entity_id, date)
);
CREATE INDEX IF NOT EXISTS rows_by_date ON rows (level, date);
CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign TEXT NOT NULL, month TEXT NOT NULL,
    amount REAL NOT NULL, currency TEXT NOT NULL,
    weighting TEXT NOT NULL DEFAULT 'linear', weekday_weights TEXT,
    cpl_target REAL, roas_target REAL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (campaign, month)
);
CREATE TABLE IF NOT EXISTS mappings (
    platform TEXT NOT NULL, campaign_id TEXT NOT NULL, slug TEXT NOT NULL,
    PRIMARY KEY (platform, campaign_id)
);
CREATE TABLE IF NOT EXISTS known_slugs (slug TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS alert_log (key TEXT PRIMARY KEY, last_sent TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS syncs (id INTEGER PRIMARY KEY AUTOINCREMENT, synced_at TEXT NOT NULL, summary TEXT NOT NULL);
"""


# ---------------------------------------------------------------- config, auth, scrubbing


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_num(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(float(env(name, str(default))), hi))
    except ValueError:
        return default


def csv_list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


def today() -> date:
    return datetime.now(timezone.utc).date()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scrub(text) -> str:
    """Remove every credential value from a message."""
    text = str(text)
    for v in [env(k) for k in SECRET_VARS] + [t for t, _ in Google._cache.values()]:
        if v and len(v) >= 4:
            text = text.replace(v, "***")
    text = re.sub(r"(access_token=)[^&\s\"']+", r"\1***", text)
    return re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1***", text)


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def db() -> sqlite3.Connection:
    path = env("DB_PATH", "/data/ads-sync.sqlite")
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def graph_version() -> str:
    v = env("META_GRAPH_VERSION", DEFAULT_GRAPH_VERSION)
    return v if re.fullmatch(r"v\d+\.\d+", v) else DEFAULT_GRAPH_VERSION


def google_version() -> str:
    v = env("GOOGLE_ADS_API_VERSION", DEFAULT_GOOGLE_ADS_VERSION)
    return v if re.fullmatch(r"v\d+", v) else DEFAULT_GOOGLE_ADS_VERSION


def platform_config() -> dict[str, list[str]]:
    """{platform: [missing or bad settings]}; an empty list = configured. Names only, never values."""
    meta, google = [], []
    if not env("META_ACCESS_TOKEN"):
        meta.append("META_ACCESS_TOKEN is empty")
    accts = csv_list("META_AD_ACCOUNT_IDS")
    if not accts:
        meta.append("META_AD_ACCOUNT_IDS is empty")
    for a in accts:
        try:
            meta_act(a)
        except ValueError as e:
            meta.append(str(e))
    for k in ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET",
              "GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_ADS_CUSTOMER_IDS"):
        if not env(k):
            google.append(f"{k} is empty")
    return {"meta": meta, "google": google}


def enabled(cfg: dict) -> list[str]:
    """A platform counts as wanted when any of its settings is set; configured when none is missing."""
    return [p for p in PLATFORMS if not cfg[p]]


def started(p: str) -> bool:
    names = {"meta": ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_IDS"),
             "google": ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_ADS_CUSTOMER_IDS")}[p]
    return any(env(n) for n in names)


# ---------------------------------------------------------------- campaign mapping (45)


def slugify(value: str) -> str:
    """The campaign service's slug rule (45): lowercase, [a-z0-9-], at most 40 characters."""
    value = re.sub(r"[\s_]+", "-", value.strip().lower())
    value = re.sub(r"[^a-z0-9-]", "", value)
    return re.sub(r"-{2,}", "-", value).strip("-")[:40].strip("-")


def slug_for(platform: str, campaign_id: str, name: str, utm: str | None, mappings: dict, known: list[str]) -> tuple[str | None, str]:
    """(slug, how). Order: explicit mapping, utm_campaign in the URL settings, our slug in the name."""
    m = mappings.get((platform, campaign_id))
    if m:
        return m, "mapping"
    if utm:
        s = slugify(utm)
        if s and (not known or s in known):
            return s, "utm_campaign"
    n = slugify(name)
    hits = [k for k in known if n == k or n.startswith(k + "-") or n.endswith("-" + k) or f"-{k}-" in n]
    if hits:
        return max(hits, key=len), "name"
    return None, "unmapped"


def load_mapping_state(conn) -> tuple[dict, list[str]]:
    mappings = {(r["platform"], r["campaign_id"]): r["slug"] for r in conn.execute("SELECT * FROM mappings")}
    known = [r["slug"] for r in conn.execute("SELECT slug FROM known_slugs")]
    return mappings, known


def refresh_known_slugs(conn, warnings: list[str]) -> int | None:
    base = env("CAMPAIGNS_URL").rstrip("/")
    if not base:
        return None
    try:
        with httpx.Client(timeout=15.0, trust_env=False) as c:
            r = c.get(f"{base}/campaigns")
        data = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as e:
        warnings.append(f"campaign-service (45) unreachable ({type(e).__name__}); kept the last known slugs")
        return None
    if not isinstance(data, list):
        warnings.append(f"campaign-service (45) answered HTTP {r.status_code}; kept the last known slugs")
        return None
    slugs = sorted({str(c["slug"]) for c in data if isinstance(c, dict) and c.get("slug")})
    conn.execute("DELETE FROM known_slugs")
    conn.executemany("INSERT INTO known_slugs (slug) VALUES (?)", [(s,) for s in slugs])
    return len(slugs)


# ---------------------------------------------------------------- health


@app.get("/health")
def health():
    cfg = platform_config()
    with closing(db()) as conn:
        last = conn.execute("SELECT synced_at, summary FROM syncs ORDER BY id DESC LIMIT 1").fetchone()
        latest = conn.execute("SELECT MAX(date) AS d FROM rows").fetchone()["d"]
    errors = [e for p in PLATFORMS if started(p) for e in cfg[p]]
    return {
        "status": "ok",
        "configured": bool(enabled(cfg)),
        "platforms": {p: {"configured": not cfg[p], "missing": cfg[p]} for p in PLATFORMS},
        "linkedin": "not implemented (see README)",
        "config_errors": errors if errors or enabled(cfg) else
        ["no platform configured: set META_ACCESS_TOKEN + META_AD_ACCOUNT_IDS, or the GOOGLE_ADS_* settings"],
        "meta_graph_version": graph_version(), "google_ads_api_version": google_version(),
        "last_sync": last["synced_at"] if last else None,
        "latest_data_date": latest,
    }


# ---------------------------------------------------------------- sync


class SyncIn(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    end_date: date | None = None
    platforms: list[str] | None = None
    meta_levels: list[str] = Field(default_factory=lambda: ["campaign"])

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v):
        if v is not None and any(p not in PLATFORMS for p in v):
            raise ValueError(f"platforms: some of {', '.join(PLATFORMS)}")
        return v

    @field_validator("meta_levels")
    @classmethod
    def _levels(cls, v):
        if not v or any(lv not in META_LEVELS for lv in v):
            raise ValueError(f"meta_levels: some of {', '.join(META_LEVELS)}")
        if "campaign" not in v:
            v = ["campaign"] + v   # summary, pacing and alerts read campaign rows
        return list(dict.fromkeys(v))


def fetch_platform(p: str, client: httpx.Client, since: date, until: date, levels: list[str],
                   warnings: list[str]) -> tuple[list[dict], dict]:
    if p == "meta":
        m = Meta(env("META_ACCESS_TOKEN"), graph_version(), csv_list("META_CONVERSION_ACTIONS", "lead"),
                 csv_list("META_REVENUE_ACTIONS", "omni_purchase"), scrub)
        rows = []
        for acct in csv_list("META_AD_ACCOUNT_IDS"):
            for lv in levels:
                rows += m.insights(client, acct, since, until, lv, warnings)
        if m.usage["max_pct"] >= 90:
            warnings.append(f"meta: rate-limit usage at {m.usage['max_pct']:.0f}% ({', '.join(m.usage['types']) or 'ads'}); "
                            "sync less often or fewer days")
        return rows, {"calls": m.calls, "rate_usage_pct": m.usage["max_pct"],
                      "regain_minutes": m.usage["regain_minutes"]}
    g = Google(env("GOOGLE_ADS_DEVELOPER_TOKEN"), env("GOOGLE_ADS_CLIENT_ID"), env("GOOGLE_ADS_CLIENT_SECRET"),
               env("GOOGLE_ADS_REFRESH_TOKEN"), env("GOOGLE_ADS_LOGIN_CUSTOMER_ID"), google_version(), scrub)
    rows = []
    for cust in csv_list("GOOGLE_ADS_CUSTOMER_IDS"):
        rows += g.campaigns(client, cust, since, until)
    return rows, {"calls": g.calls}


def store(conn, p: str, rows: list[dict], since: date, until: date, levels: list[str], ts: str) -> int:
    """Replace the platform's rows for the window (spend is restated for days as attribution settles)."""
    lv = levels if p == "meta" else ["campaign"]
    conn.execute(f"DELETE FROM rows WHERE platform = ? AND date BETWEEN ? AND ? AND level IN ({','.join('?' * len(lv))})",
                 (p, since.isoformat(), until.isoformat(), *lv))
    conn.executemany(
        "INSERT OR REPLACE INTO rows (platform, account_id, level, entity_id, date, entity_name, campaign_id, campaign_name,"
        " currency, spend, impressions, clicks, conversions, revenue, utm_campaign, synced_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["platform"], r["account_id"], r["level"], r["entity_id"], r["date"], r["entity_name"], r["campaign_id"],
          r["campaign_name"], r["currency"], r["spend"], r["impressions"], r["clicks"], r["conversions"],
          r["revenue"], r["utm_campaign"], ts) for r in rows])
    return len(rows)


@app.post("/sync", dependencies=[Depends(require_key)])
def sync(body: SyncIn | None = None):
    """Pull daily rows for the last `days` days ending `end_date` (default: yesterday, UTC) from
    every configured platform. Per platform all-or-nothing: a failing platform keeps its old rows."""
    body = body or SyncIn()
    cfg = platform_config()
    wanted = body.platforms or [p for p in PLATFORMS if not cfg[p]]
    missing = {p: cfg[p] for p in wanted if cfg[p]}
    if not wanted or (body.platforms and missing):
        detail = "; ".join(f"{p}: {', '.join(v)}" for p, v in missing.items()) or \
                 "no platform configured: set META_ACCESS_TOKEN + META_AD_ACCOUNT_IDS, or the GOOGLE_ADS_* settings"
        raise HTTPException(503, detail + " (see README)")
    skipped = [p for p in PLATFORMS if p not in wanted and started(p) and cfg[p]]
    until = body.end_date or today() - timedelta(days=1)
    if until > today():
        raise HTTPException(422, "end_date is in the future")
    since = until - timedelta(days=body.days - 1)
    ts, results, errors = now(), {}, []
    warnings = [f"{p}: not synced, settings incomplete ({', '.join(cfg[p])})" for p in skipped]
    with closing(db()) as conn:
        with conn:
            slugs = refresh_known_slugs(conn, warnings)
        with httpx.Client(timeout=TIMEOUT, trust_env=False) as client:
            for p in wanted:
                try:
                    rows, info = fetch_platform(p, client, since, until, body.meta_levels, warnings)
                except AdsError as e:
                    log.warning("sync %s failed: %s", p, scrub(e.message))
                    errors.append(e)
                    results[p] = {"ok": False, "error": scrub(e.message)}
                    continue
                with conn:
                    n = store(conn, p, rows, since, until, body.meta_levels, ts)
                currencies = sorted({r["currency"] for r in rows if r["currency"]})
                results[p] = {"ok": True, "rows": n, "currencies": currencies, **info}
        pushed = push_analytics(conn, since, until, warnings) if not errors or len(errors) < len(wanted) else None
        summary = {"window": {"start": since.isoformat(), "end": until.isoformat()}, "platforms": results,
                   "known_slugs": slugs, "analytics_rows": pushed, "warnings": warnings}
        with conn:
            conn.execute("INSERT INTO syncs (synced_at, summary) VALUES (?, ?)", (ts, json.dumps(summary)))
    for w in warnings:
        log.warning("sync: %s", scrub(w))
    if errors and len(errors) == len(wanted):
        e = max(errors, key=lambda x: x.status == 429)  # a rate limit is the more useful answer
        headers = {"Retry-After": str(e.retry_after)} if e.retry_after is not None else None
        return JSONResponse({"detail": scrub(e.message), **{"synced_at": ts, **summary}},
                            status_code=e.status, headers=headers)
    return {"synced_at": ts, **summary}


def push_analytics(conn, since: date, until: date, warnings: list[str]) -> int | None:
    """Optional: daily spend per platform and campaign slug -> 20-analytics-ingest (source=ads)."""
    base = env("ANALYTICS_URL").rstrip("/")
    if not base:
        return None
    mappings, known = load_mapping_state(conn)
    acc: dict[tuple, dict] = {}
    for r in conn.execute("SELECT * FROM rows WHERE level = 'campaign' AND date BETWEEN ? AND ?",
                          (since.isoformat(), until.isoformat())):
        slug, _ = slug_for(r["platform"], r["campaign_id"], r["campaign_name"], r["utm_campaign"], mappings, known)
        M.add(acc.setdefault((r["date"], f"{r['platform']}_ads", slug or ""), {}), dict(r))
    if not acc:
        return 0
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["date", "channel", "campaign", "impressions", "clicks", "conversions", "spend"])
    for (d, ch, camp), t in sorted(acc.items()):
        w.writerow([d, ch, camp, int(t["impressions"]), int(t["clicks"]), M.clean(t["conversions"]), round(t["spend"], 2)])
    try:
        with httpx.Client(timeout=30.0, trust_env=False) as c:
            r = c.post(f"{base}/upload", params={"source": "generic", "label": "ads"}, content=buf.getvalue().encode(),
                       headers={"X-API-Key": env("INTERNAL_API_KEY"), "content-type": "text/csv"})
        if r.status_code == 200:
            return int(r.json().get("rows_imported", len(acc)))
        warnings.append(scrub(f"analytics-ingest upload returned HTTP {r.status_code}: {r.text[:200]}"))
    except (httpx.HTTPError, ValueError) as e:
        warnings.append(f"analytics-ingest unreachable ({type(e).__name__})")
    return None


# ---------------------------------------------------------------- reading rows


def campaign_rows(conn, start: date, end: date) -> list[dict]:
    """Campaign-level daily rows in [start, end], each with its key (our slug, or platform:id)."""
    mappings, known = load_mapping_state(conn)
    out = []
    for r in conn.execute("SELECT * FROM rows WHERE level = 'campaign' AND date BETWEEN ? AND ?",
                          (start.isoformat(), end.isoformat())):
        d = dict(r)
        d["slug"], d["mapped_by"] = slug_for(d["platform"], d["campaign_id"], d["campaign_name"], d["utm_campaign"],
                                             mappings, known)
        d["key"] = d["slug"] or f"{d['platform']}:{d['campaign_id']}"
        out.append(d)
    return out


def parse_day(v: str | None, name: str) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise HTTPException(422, f"{name} must be YYYY-MM-DD")


def fmt(n: float, money: bool = False) -> str:
    if money:
        return f"{n:,.2f}"
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,.2f}"


def signed(d: float | None) -> str:
    return "" if d is None else f" ({'+' if d > 0 else ''}{d}%)"


@app.get("/campaigns")
def campaigns(days: int = Query(30, ge=1, le=366), end: str | None = None):
    """Platform campaigns seen in the window, with the slug they map to and how."""
    until = parse_day(end, "end") or today() - timedelta(days=1)
    since = until - timedelta(days=days - 1)
    with closing(db()) as conn:
        rows = campaign_rows(conn, since, until)
    acc: dict[tuple, dict] = {}
    for r in rows:
        k = (r["platform"], r["account_id"], r["campaign_id"])
        a = acc.setdefault(k, {"platform": r["platform"], "account_id": r["account_id"], "campaign_id": r["campaign_id"],
                               "campaign_name": r["campaign_name"], "currency": r["currency"], "slug": r["slug"],
                               "mapped_by": r["mapped_by"], "key": r["key"], "t": {}})
        M.add(a["t"], r)
    out = [{**{k: v for k, v in a.items() if k != "t"}, **M.derived(a["t"])} for a in acc.values()]
    return {"window": {"start": since.isoformat(), "end": until.isoformat()},
            "campaigns": sorted(out, key=lambda c: -c["spend"])}


def totals(rows, group) -> dict:
    acc: dict = {}
    for r in rows:
        M.add(acc.setdefault(group(r), {}), r)
    return acc


def compare(cur: dict, prev: dict) -> dict:
    c, p = M.derived(cur), M.derived(prev)
    return {"current": c, "previous": p,
            "delta_pct": {k: M.delta_pct(c[k], p[k]) for k in ("spend", "impressions", "clicks", "conversions",
                                                                "revenue", "ctr", "cpc", "cpl", "roas")}}


def facts_for(label: str, cur_label: str, prev_label: str, cur: str, x: dict) -> list[str]:
    """Finished sentences with every number computed here (41 feeds them to its LLM prompt)."""
    c, p, d = x["current"], x["previous"], x["delta_pct"]
    out = [f"{label}: spend {fmt(c['spend'], True)} {cur} {cur_label}"
           + (f", {fmt(p['spend'], True)} {cur} {prev_label}" if p["spend"] else "") + signed(d["spend"])]
    conv = f"{label}: {fmt(c['conversions'])} conversions {cur_label}"
    if c["cpl"] is not None:
        conv += f", CPL {fmt(c['cpl'], True)} {cur}"
        if p["cpl"] is not None:
            conv += f" ({fmt(p['cpl'], True)} {cur} {prev_label}{', ' + ('+' if d['cpl'] > 0 else '') + str(d['cpl']) + '%' if d['cpl'] is not None else ''})"
    out.append(conv)
    if c["revenue"] or p["revenue"]:
        out.append(f"{label}: ROAS {c['roas'] if c['roas'] is not None else 'n/a'} {cur_label}"
                   + (f", {p['roas']} {prev_label}" if p["roas"] is not None else "")
                   + f" (revenue {fmt(c['revenue'], True)} {cur})")
    if c["ctr"] is not None:
        out.append(f"{label}: CTR {round(c['ctr'] * 100, 2)}%, CPC {fmt(c['cpc'], True) if c['cpc'] is not None else 'n/a'} {cur} {cur_label}")
    return out


@app.get("/summary")
def summary(days: int = Query(7, ge=1, le=90), end: str | None = None, top: int = Query(10, ge=0, le=50)):
    """Last `days` days ending `end` (default yesterday, UTC) vs the `days` days before: per
    platform and currency, per currency in total, the top campaigns, current alerts and facts."""
    until = parse_day(end, "end") or today() - timedelta(days=1)
    since = until - timedelta(days=days - 1)
    p_until, p_since = since - timedelta(days=1), since - timedelta(days=days)
    with closing(db()) as conn:
        cur_rows, prev_rows = campaign_rows(conn, since, until), campaign_rows(conn, p_since, p_until)
        last = conn.execute("SELECT synced_at FROM syncs ORDER BY id DESC LIMIT 1").fetchone()
        alerts = compute_alerts(conn, until)
    by = lambda rows, g: totals(rows, g)
    plat_c, plat_p = by(cur_rows, lambda r: (r["platform"], r["currency"])), by(prev_rows, lambda r: (r["platform"], r["currency"]))
    tot_c, tot_p = by(cur_rows, lambda r: r["currency"]), by(prev_rows, lambda r: r["currency"])
    camp_c, camp_p = by(cur_rows, lambda r: (r["key"], r["currency"])), by(prev_rows, lambda r: (r["key"], r["currency"]))
    cl, pl = f"in the last {days} days", f"the {days} days before"
    platforms, total, camps, facts = [], [], [], []
    for (pf, cur) in sorted(set(plat_c) | set(plat_p)):
        x = compare(plat_c.get((pf, cur), {}), plat_p.get((pf, cur), {}))
        platforms.append({"platform": pf, "currency": cur, **x})
        facts += facts_for(f"{pf} ads", cl, pl, cur, x)
    for cur in sorted(set(tot_c) | set(tot_p)):
        x = compare(tot_c.get(cur, {}), tot_p.get(cur, {}))
        total.append({"currency": cur, **x})
        if len(platforms) > 1:
            facts += facts_for(f"all ads ({cur})", cl, pl, cur, x)[:2]
    for (key, cur), t in sorted(camp_c.items(), key=lambda kv: -kv[1].get("spend", 0))[:top]:
        x = compare(t, camp_p.get((key, cur), {}))
        src = sorted({r["platform"] for r in cur_rows if r["key"] == key})
        camps.append({"key": key, "slug": None if ":" in key else key, "platforms": src, "currency": cur, **x})
    for c in camps[:3]:
        cc = c["current"]
        facts.append(f"campaign {c['key']}: spend {fmt(cc['spend'], True)} {c['currency']} {cl}, "
                     f"{fmt(cc['conversions'])} conversions" + (f", CPL {fmt(cc['cpl'], True)} {c['currency']}" if cc["cpl"] is not None else "")
                     + (f", ROAS {cc['roas']}" if cc["revenue"] else ""))
    unmapped = {}
    for r in cur_rows:
        if not r["slug"]:
            unmapped[r["currency"]] = unmapped.get(r["currency"], 0) + r["spend"]
    for a in alerts:
        facts.append(f"alert {a['rule']}: {a['message']}")
    return {"window": {"current": {"start": since.isoformat(), "end": until.isoformat()},
                       "previous": {"start": p_since.isoformat(), "end": p_until.isoformat()}},
            "last_sync": last["synced_at"] if last else None,
            "has_data": bool(cur_rows or prev_rows),
            "platforms": platforms, "total": total, "campaigns": camps,
            "unmapped_spend": {k: round(v, 2) for k, v in unmapped.items()},
            "alerts": alerts, "facts": facts,
            "note": "CPL = spend / conversions (the conversion actions configured per platform). Totals are per "
                    "currency; currencies are never converted."}


# ---------------------------------------------------------------- mappings


class MappingIn(BaseModel):
    platform: str
    campaign_id: str = Field(min_length=1, max_length=40)
    slug: str = Field(min_length=1, max_length=60)

    @field_validator("platform")
    @classmethod
    def _p(cls, v):
        if v not in PLATFORMS:
            raise ValueError(f"platform: one of {', '.join(PLATFORMS)}")
        return v

    @field_validator("campaign_id")
    @classmethod
    def _id(cls, v):
        if not v.strip().isdigit():
            raise ValueError("campaign_id: the platform's numeric campaign id")
        return v.strip()


@app.post("/mappings", dependencies=[Depends(require_key)])
def put_mapping(body: MappingIn):
    slug = slugify(body.slug)
    if not slug:
        raise HTTPException(422, "slug is empty after slugifying")
    with closing(db()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO mappings (platform, campaign_id, slug) VALUES (?,?,?)",
                     (body.platform, body.campaign_id, slug))
    return {"platform": body.platform, "campaign_id": body.campaign_id, "slug": slug}


@app.get("/mappings")
def list_mappings():
    with closing(db()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM mappings ORDER BY platform, campaign_id")]


@app.delete("/mappings/{platform}/{campaign_id}", dependencies=[Depends(require_key)])
def delete_mapping(platform: str, campaign_id: str):
    with closing(db()) as conn, conn:
        n = conn.execute("DELETE FROM mappings WHERE platform = ? AND campaign_id = ?", (platform, campaign_id)).rowcount
    if not n:
        raise HTTPException(404, "no such mapping")
    return {"deleted": True}


# ---------------------------------------------------------------- budgets and pacing


class BudgetIn(BaseModel):
    campaign: str = Field(min_length=1, max_length=60, description="our slug (45), or platform:campaign_id")
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    amount: float = Field(gt=0, le=1e9)
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    weighting: str = "linear"
    weekday_weights: list[float] | None = Field(default=None, min_length=7, max_length=7)
    cpl_target: float | None = Field(default=None, gt=0)
    roas_target: float | None = Field(default=None, gt=0)

    @field_validator("campaign")
    @classmethod
    def _c(cls, v):
        v = v.strip()
        m = re.fullmatch(r"(meta|google):(\d+)", v)
        if m:
            return v
        s = slugify(v)
        if not s:
            raise ValueError("campaign: our campaign slug (45) or meta:<id> / google:<id>")
        return s

    @field_validator("weighting")
    @classmethod
    def _w(cls, v):
        if v not in ("linear", "weekday"):
            raise ValueError("weighting: linear or weekday")
        return v

    @field_validator("weekday_weights")
    @classmethod
    def _ww(cls, v):
        if v is not None and (any(x < 0 for x in v) or sum(v) <= 0):
            raise ValueError("weekday_weights: 7 numbers (Monday..Sunday), none negative, not all 0")
        return v


def budget_out(r) -> dict:
    b = dict(r)
    b["weekday_weights"] = json.loads(b["weekday_weights"]) if b["weekday_weights"] else None
    return b


@app.post("/budgets", dependencies=[Depends(require_key)])
def put_budget(body: BudgetIn):
    """Create or replace the budget of one campaign for one month (and its CPL / ROAS targets)."""
    ts = now()
    with closing(db()) as conn, conn:
        conn.execute(
            "INSERT INTO budgets (campaign, month, amount, currency, weighting, weekday_weights, cpl_target, roas_target,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (campaign, month) DO UPDATE SET"
            " amount=excluded.amount, currency=excluded.currency, weighting=excluded.weighting,"
            " weekday_weights=excluded.weekday_weights, cpl_target=excluded.cpl_target,"
            " roas_target=excluded.roas_target, updated_at=excluded.updated_at",
            (body.campaign, body.month, body.amount, body.currency.upper(), body.weighting,
             json.dumps(body.weekday_weights) if body.weekday_weights else None, body.cpl_target, body.roas_target, ts, ts))
        r = conn.execute("SELECT * FROM budgets WHERE campaign = ? AND month = ?", (body.campaign, body.month)).fetchone()
    return budget_out(r)


@app.get("/budgets")
def list_budgets(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")):
    with closing(db()) as conn:
        sql, args = "SELECT * FROM budgets", []
        if month:
            sql, args = sql + " WHERE month = ?", [month]
        return [budget_out(r) for r in conn.execute(sql + " ORDER BY month, campaign", args)]


@app.delete("/budgets/{budget_id}", dependencies=[Depends(require_key)])
def delete_budget(budget_id: int):
    with closing(db()) as conn, conn:
        n = conn.execute("DELETE FROM budgets WHERE id = ?", (budget_id,)).rowcount
    if not n:
        raise HTTPException(404, "no such budget")
    return {"deleted": True}


def pacing_for(conn, as_of: date) -> list[dict]:
    month = as_of.strftime("%Y-%m")
    first, _ = M.month_bounds(month)
    rows = campaign_rows(conn, first, as_of)
    out = []
    for b in conn.execute("SELECT * FROM budgets WHERE month = ? ORDER BY campaign", (month,)):
        b = budget_out(b)
        mine = [r for r in rows if r["key"] == b["campaign"]]
        other = sorted({r["currency"] for r in mine if r["currency"].upper() != b["currency"]})
        spend = sum(r["spend"] for r in mine if r["currency"].upper() == b["currency"])
        p = M.pacing(b["amount"], spend, month, as_of, b["weighting"], b["weekday_weights"])
        out.append({"campaign": b["campaign"], "month": month, "currency": b["currency"], "weighting": b["weighting"],
                    "budget_id": b["id"], "platforms": sorted({r["platform"] for r in mine}), **p,
                    "cpl_target": b["cpl_target"], "roas_target": b["roas_target"],
                    "warning": f"spend in {', '.join(other)} is not counted (budget is in {b['currency']}; "
                               "currencies are never converted)" if other else None})
    return out


@app.get("/pacing")
def get_pacing(as_of: str | None = None):
    """Spend to date vs the plan for every budget of the month that contains `as_of` (default yesterday)."""
    d = parse_day(as_of, "as_of") or today() - timedelta(days=1)
    with closing(db()) as conn:
        return {"as_of": d.isoformat(), "budgets": pacing_for(conn, d)}


# ---------------------------------------------------------------- alerts


def thresholds() -> dict:
    return {"overspend_pct": env_num("ALERT_OVERSPEND_PCT", 15, 1, 500),
            "underspend_pct": env_num("ALERT_UNDERSPEND_PCT", 25, 1, 100),
            "min_days": int(env_num("ALERT_MIN_DAYS", 3, 1, 28)),
            "window_days": int(env_num("ALERT_WINDOW_DAYS", 7, 1, 60)),
            "zero_conv_days": int(env_num("ALERT_ZERO_CONV_DAYS", 3, 1, 30)),
            "zero_conv_min_spend": env_num("ALERT_ZERO_CONV_MIN_SPEND", 0, 0, 1e9),
            "repeat_days": int(env_num("ALERT_REPEAT_DAYS", 7, 0, 90))}


def compute_alerts(conn, as_of: date) -> list[dict]:
    t = thresholds()
    out = []
    for p in pacing_for(conn, as_of):
        for a in M.pacing_alerts(p["campaign"], p, t["overspend_pct"], t["underspend_pct"], t["min_days"]):
            out.append({**a, "currency": p["currency"]})
    w_start = as_of - timedelta(days=t["window_days"] - 1)
    rows = campaign_rows(conn, min(w_start, as_of - timedelta(days=t["zero_conv_days"] - 1)), as_of)
    targets = {p["campaign"]: p for p in pacing_for(conn, as_of) if p["cpl_target"] or p["roas_target"]}
    for key, p in targets.items():
        win = [r for r in rows if r["key"] == key and r["date"] >= w_start.isoformat()
               and r["currency"].upper() == p["currency"]]
        tot = totals(win, lambda r: 0).get(0, {})
        for a in M.efficiency_alerts(key, tot, t["window_days"], p["cpl_target"], p["roas_target"]):
            out.append({**a, "currency": p["currency"]})
    daily: dict[tuple, dict] = {}
    for r in rows:
        M.add(daily.setdefault((r["key"], r["currency"]), {}).setdefault(r["date"], {}), r)
    for (key, cur), days in sorted(daily.items()):
        a = M.zero_conversion_alert(key, days, as_of, t["zero_conv_days"], t["zero_conv_min_spend"])
        if a:
            out.append({**a, "currency": cur})
    order = {"high": 0, "medium": 1, "low": 2}
    for a in out:
        a["as_of"] = as_of.isoformat()
        a["message"] = a["message"] + (f" (amounts in {a['currency']})" if a.get("currency") else "")
    return sorted(out, key=lambda a: (order[a["severity"]], a["campaign"], a["rule"]))


@app.get("/alerts")
def get_alerts(as_of: str | None = None):
    """Current alerts (read-only; nothing is recorded)."""
    d = parse_day(as_of, "as_of") or today() - timedelta(days=1)
    with closing(db()) as conn:
        return {"as_of": d.isoformat(), "thresholds": thresholds(), "alerts": compute_alerts(conn, d)}


@app.post("/alerts/check", dependencies=[Depends(require_key)])
def check_alerts(as_of: str | None = None):
    """Current alerts, and which of them are new: not sent for the same rule and campaign in the
    last ALERT_REPEAT_DAYS days. The new ones are recorded as sent (the daily workflow notifies them)."""
    d = parse_day(as_of, "as_of") or today() - timedelta(days=1)
    repeat = thresholds()["repeat_days"]
    with closing(db()) as conn:
        alerts = compute_alerts(conn, d)
        new = []
        with conn:
            for a in alerts:
                key = f"{a['rule']}|{a['campaign']}"
                r = conn.execute("SELECT last_sent FROM alert_log WHERE key = ?", (key,)).fetchone()
                if r and repeat and date.fromisoformat(r["last_sent"]) > d - timedelta(days=repeat):
                    continue
                new.append(a)
                conn.execute("INSERT OR REPLACE INTO alert_log (key, last_sent, message) VALUES (?,?,?)",
                             (key, d.isoformat(), a["message"]))
    return {"as_of": d.isoformat(), "alerts": alerts, "new": new}
