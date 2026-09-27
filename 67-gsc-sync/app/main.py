"""GSC sync: Google Search Console clicks, impressions and positions per page and query.

Syncs two consecutive windows (the last N days and the N days before) into SQLite, then
answers "which pages are losing clicks" (content refresh) and "which queries rank 5-20
with many impressions" (SEO briefs, 29). Uses no LLM.

Google API facts used here (see README "Google API used"):
- searchanalytics.query: POST https://searchconsole.googleapis.com/webmasters/v3/sites/
  {siteUrl}/searchAnalytics/query, body {startDate, endDate, dimensions, type, dataState,
  rowLimit (max 25000), startRow}; response {rows: [{keys, clicks, impressions, ctr,
  position}], responseAggregationType}; no "rows" key when there is no data.
- Service-account auth: RS256 JWT {iss, scope, aud, iat, exp <= iat+3600} posted to
  https://oauth2.googleapis.com/token as grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer.
"""
import csv
import hmac
import io
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

app = FastAPI(title="gsc-sync")
log = logging.getLogger("gsc-sync")

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API = "https://searchconsole.googleapis.com/webmasters/v3"
ROW_LIMIT = 5000
MAX_PAGES = 5  # at most 25,000 rows per dimension set and window
MAX_DAYS = 90
LAG_DAYS = 3  # "final" data is usually complete up to 2-3 days ago
LABEL = "gsc"  # rows land in 20 under source=gsc
CHANNEL = "google-search"
DIMENSIONS = {"page": ["page"], "query": ["query"], "page_query": ["page", "query"]}

SCHEMA = """
CREATE TABLE IF NOT EXISTS syncs (
    site TEXT PRIMARY KEY,
    days INTEGER NOT NULL,
    cur_start TEXT NOT NULL, cur_end TEXT NOT NULL,
    prev_start TEXT NOT NULL, prev_end TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rows (
    site TEXT NOT NULL,
    win TEXT NOT NULL,          -- current | previous
    dim TEXT NOT NULL,          -- page | query | page_query
    page TEXT NOT NULL,         -- '' for dim=query
    query TEXT NOT NULL,        -- '' for dim=page
    clicks REAL NOT NULL,
    impressions REAL NOT NULL,
    ctr REAL NOT NULL,
    position REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS rows_lookup ON rows (site, dim, win, page);
"""


# ---------- config, auth of this service, scrubbing


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def site_url() -> str:
    return env("GSC_SITE_URL")


def credentials_path() -> str:
    return env("GSC_CREDENTIALS_FILE", "/secrets/gsc.json")


def db_path() -> str:
    return env("DB_PATH", "/data/gsc.sqlite")


def analytics_url() -> str:
    return env("ANALYTICS_URL").rstrip("/")


_token_cache: dict[str, tuple[str, float]] = {}  # client_email -> (access_token, expires_at)
_token_lock = threading.Lock()


def scrub(text: str) -> str:
    """Remove any credential value (internal key, cached access tokens) from a message."""
    values = [os.environ.get("INTERNAL_API_KEY", "")] + [t for t, _ in _token_cache.values()]
    for v in values:
        if v:
            text = text.replace(v, "***")
    return text


class ConfigError(Exception):
    pass


def load_credentials() -> dict:
    """The service-account JSON. Raises ConfigError with a message that never holds key material."""
    path = credentials_path()
    if not os.path.isfile(path):
        raise ConfigError(f"GSC_CREDENTIALS_FILE not found at {path}")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        raise ConfigError(f"GSC_CREDENTIALS_FILE at {path} is not readable JSON")
    if not isinstance(data, dict) or data.get("type") != "service_account" \
            or not data.get("client_email") or not data.get("private_key"):
        raise ConfigError(f"GSC_CREDENTIALS_FILE at {path} is not a service-account key "
                          "(needs type=service_account, client_email, private_key)")
    return data


def config_errors() -> tuple[list[str], dict | None]:
    errors, creds = [], None
    if not site_url():
        errors.append("GSC_SITE_URL is not set")
    try:
        creds = load_credentials()
    except ConfigError as e:
        errors.append(str(e))
    return errors, creds


# ---------- Google


class GoogleError(Exception):
    pass


def google_message(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or "")[:300]
    if isinstance(err, str):  # token endpoint: {"error": "invalid_grant", "error_description": ...}
        return f"{err}: {body.get('error_description', '')}"[:300]
    return ""


def access_token(client: httpx.Client, creds: dict, force: bool = False) -> str:
    """OAuth2 JWT-bearer flow for a service account; cached until 60 s before expiry."""
    email = creds["client_email"]
    with _token_lock:
        cached = _token_cache.get(email)
        if cached and not force and cached[1] - 60 > time.time():
            return cached[0]
        now = int(time.time())
        claims = {"iss": email, "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3600}
        headers = {"kid": creds["private_key_id"]} if creds.get("private_key_id") else None
        try:
            assertion = jwt.encode(claims, creds["private_key"], algorithm="RS256", headers=headers)
        except Exception as e:  # bad PEM; never include the key in the message
            raise ConfigError(f"GSC_CREDENTIALS_FILE private_key cannot sign ({type(e).__name__})")
        try:
            r = client.post(TOKEN_URL, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
        except httpx.HTTPError as e:
            raise GoogleError(f"Google token endpoint unreachable ({type(e).__name__})")
        if r.status_code != 200:
            msg = google_message(r)
            hint = ""
            if "invalid_grant" in msg:
                hint = (" The key may be deleted or disabled in Google Cloud, or the server clock is"
                        " off by more than a few minutes.")
            raise GoogleError(f"Google refused the service-account key (HTTP {r.status_code}"
                              f"{': ' + msg if msg else ''}).{hint}")
        try:
            body = r.json()
            token, ttl = body["access_token"], float(body.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError):
            raise GoogleError("Google token endpoint returned an unexpected response")
        _token_cache[email] = (token, time.time() + ttl)
        return token


class GSC:
    def __init__(self, client: httpx.Client, creds: dict, site: str):
        self.client, self.creds, self.site = client, creds, site
        self.url = f"{API}/sites/{quote(site, safe='')}/searchAnalytics/query"
        self.calls = 0

    def _post(self, body: dict) -> httpx.Response:
        for attempt in (1, 2):
            token = access_token(self.client, self.creds, force=attempt == 2)
            self.calls += 1
            try:
                r = self.client.post(self.url, json=body, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as e:
                raise GoogleError(f"Search Console API unreachable ({type(e).__name__})")
            if r.status_code == 401 and attempt == 1:
                continue  # token revoked or expired early: fetch a new one once
            return r
        return r

    def query(self, start: date, end: date, dims: list[str]) -> tuple[list[dict], bool]:
        """All rows for one dimension set; returns (rows, complete)."""
        out: list[dict] = []
        for page in range(MAX_PAGES):
            r = self._post({"startDate": start.isoformat(), "endDate": end.isoformat(),
                            "dimensions": dims, "type": "web", "dataState": "final",
                            "rowLimit": ROW_LIMIT, "startRow": page * ROW_LIMIT})
            if r.status_code != 200:
                raise GoogleError(self.explain(r))
            try:
                body = r.json()
            except ValueError:
                raise GoogleError("Search Console returned non-JSON")
            rows = body.get("rows", []) if isinstance(body, dict) else None
            if not isinstance(rows, list):
                raise GoogleError("Search Console returned an unexpected response shape")
            out.extend(rows)
            if len(rows) < ROW_LIMIT:
                return out, True
        return out, False

    def explain(self, r: httpx.Response) -> str:
        msg = google_message(r)
        email = self.creds.get("client_email", "")
        low = msg.lower()
        if r.status_code == 403 and ("has not been used" in low or "disabled" in low
                                     or "accessnotconfigured" in low):
            return ("Search Console API is not enabled for this service account's Google Cloud "
                    "project. Enable 'Google Search Console API' in APIs & Services, wait a few "
                    f"minutes, then sync again. (Google: {msg})")
        if r.status_code in (403, 404):
            return (f"Search Console refused access to {self.site} (HTTP {r.status_code}). Add the "
                    f"service account {email} as a user of this property (Search Console > "
                    "Settings > Users and permissions > Add user; Restricted is enough), and check "
                    "GSC_SITE_URL matches the property exactly: a domain property is "
                    "sc-domain:example.com, a URL-prefix property is the full URL with its "
                    f"trailing slash, e.g. https://example.com/. (Google: {msg or 'no message'})")
        if r.status_code == 429:
            return f"Search Console quota exceeded (HTTP 429); try again later. (Google: {msg})"
        return f"Search Console returned HTTP {r.status_code}{': ' + msg if msg else ''}"


# ---------- windows and storage


def windows(days: int, end: date) -> dict[str, tuple[date, date]]:
    """current = the `days` days ending on `end` (both included); previous = the `days` before."""
    cur_start = end - timedelta(days=days - 1)
    prev_end = cur_start - timedelta(days=1)
    return {"current": (cur_start, end), "previous": (prev_end - timedelta(days=days - 1), prev_end)}


def today() -> date:
    return datetime.now(timezone.utc).date()


@contextmanager
def db():
    path = db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def last_sync(conn, site: str) -> dict | None:
    row = conn.execute("SELECT * FROM syncs WHERE site = ?", (site,)).fetchone()
    if not row:
        return None
    return {"days": row["days"], "synced_at": row["synced_at"],
            "current": {"start": row["cur_start"], "end": row["cur_end"]},
            "previous": {"start": row["prev_start"], "end": row["prev_end"]}}


def require_sync(conn) -> tuple[str, dict]:
    site = site_url()
    if not site:
        raise HTTPException(503, "not configured: GSC_SITE_URL is not set")
    info = last_sync(conn, site)
    if not info:
        raise HTTPException(409, f"no sync yet for {site}; call POST /sync first")
    return site, info


def num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------- endpoints


class SyncRequest(BaseModel):
    days: int = Field(28, ge=1, le=MAX_DAYS)
    end_date: date | None = None


@app.get("/health")
def health():
    errors, creds = config_errors()
    last = None
    site = site_url()
    if site:
        try:
            with db() as conn:
                last = last_sync(conn, site)
        except (sqlite3.Error, OSError):
            errors.append("database not writable at DB_PATH")
    return {
        "status": "ok",
        "configured": not errors,
        "site": site or None,
        # The e-mail is what you add in Search Console; it is not a secret. The key never leaves the file.
        "service_account": creds.get("client_email") if creds else None,
        "analytics_push": bool(analytics_url()),
        "last_sync": last,
        "config_errors": errors,
    }


@app.post("/sync", dependencies=[Depends(require_key)])
def sync(req: SyncRequest):
    end = req.end_date or today() - timedelta(days=LAG_DAYS)
    if end > today():
        raise HTTPException(422, "end_date is in the future")
    errors, creds = config_errors()
    if errors:
        raise HTTPException(503, "not configured: " + "; ".join(errors))
    site = site_url()
    wins = windows(req.days, end)
    warnings: list[str] = []
    fetched: list[tuple] = []
    counts = {"current": {}, "previous": {}}
    with httpx.Client(timeout=30) as client:
        gsc = GSC(client, creds, site)
        try:
            for win, (start, stop) in wins.items():
                for dim, dims in DIMENSIONS.items():
                    rows, complete = gsc.query(start, stop, dims)
                    if not complete:
                        warnings.append(f"{win} {dim}: more than {MAX_PAGES * ROW_LIMIT} rows; "
                                        "the smallest ones are missing")
                    counts[win][dim] = len(rows)
                    for r in rows:
                        keys = r.get("keys") or []
                        page = keys[0] if dim in ("page", "page_query") and keys else ""
                        query = keys[-1] if dim in ("query", "page_query") and keys else ""
                        fetched.append((site, win, dim, str(page), str(query), num(r.get("clicks")),
                                        num(r.get("impressions")), num(r.get("ctr")),
                                        num(r.get("position"))))
        except ConfigError as e:
            raise HTTPException(503, "not configured: " + str(e))
        except GoogleError as e:
            msg = scrub(str(e))
            log.warning("sync %s: %s", site, msg)
            raise HTTPException(502, msg)

        pushed = None
        if analytics_url():
            pushed = push_daily(client, gsc, wins, warnings)
    calls = gsc.calls

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db() as conn:  # replace the site's previous sync in one transaction
        conn.execute("DELETE FROM rows WHERE site = ?", (site,))
        conn.executemany("INSERT INTO rows VALUES (?,?,?,?,?,?,?,?,?)", fetched)
        (cs, ce), (ps, pe) = wins["current"], wins["previous"]
        conn.execute("INSERT OR REPLACE INTO syncs VALUES (?,?,?,?,?,?,?)",
                     (site, req.days, cs.isoformat(), ce.isoformat(), ps.isoformat(),
                      pe.isoformat(), now))
    for w in warnings:
        log.warning("sync %s: %s", site, w)
    log.info("sync %s %s..%s: %d row(s) stored, %d Search Console call(s)",
             site, wins["previous"][0], end, len(fetched), calls)
    return {"site": site, "synced_at": now,
            "windows": {k: {"start": s.isoformat(), "end": e.isoformat()} for k, (s, e) in wins.items()},
            "rows": counts, "calls": calls, "analytics_rows": pushed, "warnings": warnings}


def push_daily(client: httpx.Client, gsc: GSC, wins: dict, warnings: list[str]) -> int | None:
    """Daily site totals (clicks, impressions) for both windows -> 20 under source=gsc."""
    start, end = wins["previous"][0], wins["current"][1]
    try:
        rows, _ = gsc.query(start, end, ["date"])
    except (GoogleError, ConfigError) as e:
        warnings.append(scrub(f"analytics push skipped: {e}"))
        return None
    if not rows:
        return 0
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["date", "channel", "campaign", "impressions", "clicks"])
    for r in sorted(rows, key=lambda r: (r.get("keys") or [""])[0]):
        w.writerow([(r.get("keys") or [""])[0], CHANNEL, "", int(num(r.get("impressions"))),
                    int(num(r.get("clicks")))])
    try:
        r = client.post(f"{analytics_url()}/upload", params={"source": "generic", "label": LABEL},
                        content=buf.getvalue().encode(),
                        headers={"X-API-Key": os.environ.get("INTERNAL_API_KEY", ""),
                                 "content-type": "text/csv"})
        if r.status_code == 200:
            return int(r.json().get("rows_imported", len(rows)))
        warnings.append(scrub(f"analytics-ingest upload returned HTTP {r.status_code}: {r.text[:300]}"))
    except (httpx.HTTPError, ValueError) as e:
        warnings.append(f"analytics-ingest unreachable ({type(e).__name__})")
    return None


def metric(row) -> dict:
    if row is None:
        return {"clicks": 0, "impressions": 0, "ctr": None, "position": None}
    return {"clicks": int(row["clicks"]), "impressions": int(row["impressions"]),
            "ctr": round(row["ctr"], 4), "position": round(row["position"], 1)}


def page_queries(conn, site: str, page: str, win: str, limit: int) -> list[dict]:
    """Top queries for one page in `win` (by clicks, then impressions), with the other window beside."""
    other = "previous" if win == "current" else "current"
    rows = conn.execute(
        "SELECT query, clicks, impressions, ctr, position FROM rows WHERE site=? AND dim='page_query' "
        "AND win=? AND page=? ORDER BY clicks DESC, impressions DESC, query LIMIT ?",
        (site, win, page, limit)).fetchall()
    out = []
    for r in rows:
        o = conn.execute("SELECT clicks, impressions, ctr, position FROM rows WHERE site=? AND "
                         "dim='page_query' AND win=? AND page=? AND query=?",
                         (site, other, page, r["query"])).fetchone()
        out.append({"query": r["query"], win: metric(r), other: metric(o)})
    return out


@app.get("/pages/declining")
def declining(min_clicks: int = Query(20, ge=1), drop: float = Query(0.3, gt=0, le=1),
              limit: int = Query(20, ge=1, le=500)):
    """Pages whose clicks fell by at least `drop` (fraction) from the previous window.

    Only pages with at least `min_clicks` in the previous window count, so new pages (no
    previous clicks) never appear. A page with no clicks at all in the current window has drop 1.0.
    """
    with db() as conn:
        site, info = require_sync(conn)
        prev = conn.execute("SELECT * FROM rows WHERE site=? AND dim='page' AND win='previous' "
                            "AND clicks >= ?", (site, min_clicks)).fetchall()
        out = []
        for p in prev:
            cur = conn.execute("SELECT * FROM rows WHERE site=? AND dim='page' AND win='current' "
                               "AND page=?", (site, p["page"])).fetchone()
            cur_clicks = cur["clicks"] if cur else 0.0
            d = (p["clicks"] - cur_clicks) / p["clicks"]
            if d + 1e-9 < drop:
                continue
            out.append({"page": p["page"], "drop": round(d, 4),
                        "clicks_lost": int(p["clicks"] - cur_clicks),
                        "current": metric(cur), "previous": metric(p),
                        # queries the page ranked for before the fall, with their current numbers
                        "top_queries": page_queries(conn, site, p["page"], "previous", 5)})
    out.sort(key=lambda x: (-x["clicks_lost"], -x["drop"], x["page"]))
    return {"site": site, "windows": {"current": info["current"], "previous": info["previous"]},
            "synced_at": info["synced_at"], "min_clicks": min_clicks, "drop": drop,
            "pages": out[:limit]}


@app.get("/queries/opportunities")
def opportunities(min_impressions: int = Query(100, ge=0), min_position: float = Query(5, ge=1),
                  max_position: float = Query(20, ge=1), limit: int = Query(50, ge=1, le=1000)):
    """Striking distance: queries with many impressions whose average position (current
    window) is between min_position and max_position, with the page that earns them most."""
    if min_position > max_position:
        raise HTTPException(422, "min_position is greater than max_position")
    with db() as conn:
        site, info = require_sync(conn)
        rows = conn.execute(
            "SELECT * FROM rows WHERE site=? AND dim='query' AND win='current' AND impressions >= ? "
            "AND position >= ? AND position <= ? ORDER BY impressions DESC, query LIMIT ?",
            (site, min_impressions, min_position, max_position, limit)).fetchall()
        out = []
        for q in rows:
            best = conn.execute(
                "SELECT page, clicks, impressions, ctr, position FROM rows WHERE site=? AND "
                "dim='page_query' AND win='current' AND query=? "
                "ORDER BY clicks DESC, impressions DESC, position ASC, page LIMIT 1",
                (site, q["query"])).fetchone()
            prev = conn.execute("SELECT * FROM rows WHERE site=? AND dim='query' AND win='previous' "
                                "AND query=?", (site, q["query"])).fetchone()
            out.append({"query": q["query"], **metric(q),
                        "previous_position": round(prev["position"], 1) if prev else None,
                        "best_page": ({"page": best["page"], **metric(best)} if best else None)})
    return {"site": site, "window": info["current"], "synced_at": info["synced_at"],
            "min_impressions": min_impressions, "min_position": min_position,
            "max_position": max_position, "queries": out}


@app.get("/pages/{page:path}/queries")
def queries_for_page(page: str, window: str = Query("current", pattern="^(current|previous)$"),
                     limit: int = Query(20, ge=1, le=500)):
    """Top queries for one page. Pass the page URL percent-encoded (encodeURIComponent)."""
    with db() as conn:
        site, info = require_sync(conn)
        pages = conn.execute("SELECT * FROM rows WHERE site=? AND dim='page' AND page=? "
                             "ORDER BY win", (site, page)).fetchall()
        if not pages:
            raise HTTPException(404, "page not in the last sync (check the exact URL, incl. trailing slash)")
        by_win = {r["win"]: r for r in pages}
        return {"site": site, "page": page, "window": window, "windows": {
                    "current": info["current"], "previous": info["previous"]},
                "current": metric(by_win.get("current")), "previous": metric(by_win.get("previous")),
                "queries": page_queries(conn, site, page, window, limit)}
