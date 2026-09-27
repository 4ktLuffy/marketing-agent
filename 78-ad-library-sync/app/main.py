"""Ad library sync: the competitor registry, competitors' ads from official APIs only, and
links to the public ad libraries a person opens by hand.

- Registry (the single source of truth for competitors): name, website, key pages, social
  handles, Meta page id, markets, notes, status active | suggested | ignored. Active
  competitors' key pages are kept in sync as watches in 09-change-monitor (tag
  `competitor:<id>`), idempotently; removing or deactivating a competitor removes them.
- Ads: Meta Ad Library API (`GET https://graph.facebook.com/<version>/ads_archive`) with the
  user's own identity-verified token. Commercial ads come back only when they were delivered
  in the EU ("Ads that did not reach any location in the EU will only return if they are
  about social issues, elections or politics", Meta ads_archive reference). Deduped by ad id,
  first_seen / last_seen / stopped tracked here.
- No scraping and no headless browser anywhere: Google, LinkedIn and TikTok ad libraries (and
  Meta outside the EU) are offered as links for a person to open.
- Suggestions: domains that keep coming up in our own reviews (58), social-listening
  mentions (11) and trend-digest notes in the knowledge base (06) are stored as `suggested`,
  with the evidence. They are never tracked until a person accepts them.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="ad-library-sync")
log = logging.getLogger("ad-library-sync")
# httpx logs every request URL at INFO; Graph API paging URLs carry access_token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

GRAPH_HOST = "graph.facebook.com"
DEFAULT_GRAPH_VERSION = "v26.0"
FIELDS = ",".join([
    "id", "page_id", "page_name", "ad_creative_bodies", "ad_creative_link_titles",
    "ad_creative_link_descriptions", "ad_creative_link_captions", "ad_delivery_start_time",
    "ad_delivery_stop_time", "publisher_platforms", "languages",
])  # ad_snapshot_url is NOT requested: it embeds the access token.
PAGE_LIMIT = 100
MAX_PAGES = 20  # per competitor and sync: up to 2,000 ads
RETRY_MAX_WAIT = 5.0  # seconds; a longer Retry-After is returned to the caller instead
DEFAULT_RETRY_AFTER = 300
RATE_LIMIT_CODES = {4, 17, 32, 613, 80000, 80004}
PERMISSION_CODES = {10, 200}
EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
      "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
STATUSES = ("active", "suggested", "ignored")
PAGE_TYPES = ("home", "pricing", "product", "features", "about", "other")
TIMEOUT = 30.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS competitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    website TEXT,
    key_pages TEXT NOT NULL DEFAULT '[]',
    social TEXT NOT NULL DEFAULT '{}',
    meta_page_id TEXT,
    google_advertiser_id TEXT,
    linkedin_company_id TEXT,
    domains TEXT NOT NULL DEFAULT '[]',
    markets TEXT NOT NULL DEFAULT '[]',
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    evidence TEXT NOT NULL DEFAULT '[]',
    watch_state TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ads (
    ad_id TEXT PRIMARY KEY,
    competitor_id INTEGER NOT NULL,
    page_id TEXT, page_name TEXT,
    bodies TEXT NOT NULL, link_titles TEXT NOT NULL,
    link_descriptions TEXT NOT NULL, link_captions TEXT NOT NULL,
    platforms TEXT NOT NULL, languages TEXT NOT NULL,
    delivery_start TEXT, delivery_stop TEXT,
    status TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    changed_at TEXT, stopped_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS ads_by_competitor ON ads (competitor_id, first_seen);
CREATE TABLE IF NOT EXISTS syncs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at TEXT NOT NULL,
    summary TEXT NOT NULL
);
"""


# ---------- config, auth, scrubbing


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def token() -> str:
    return env("META_AD_LIBRARY_TOKEN")


def graph_version() -> str:
    v = env("META_GRAPH_VERSION", DEFAULT_GRAPH_VERSION)
    return v if re.fullmatch(r"v\d+\.\d+", v) else DEFAULT_GRAPH_VERSION


def countries() -> list[str]:
    raw = env("AD_COUNTRIES", "DE,FR,NL,IE")
    return [c.strip().upper() for c in raw.split(",") if re.fullmatch(r"[A-Za-z]{2}", c.strip())]


def lookback_days() -> int:
    try:
        return max(1, min(int(env("AD_LOOKBACK_DAYS", "90")), 365))
    except ValueError:
        return 90


def service_url(name: str) -> str:
    return env(name).rstrip("/")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scrub(text: str) -> str:
    """Remove every credential value from a message (the Meta token, the internal key)."""
    text = str(text)
    for v in (token(), env("INTERNAL_API_KEY")):
        if v:
            text = text.replace(v, "***")
    return re.sub(r"(access_token=)[^&\s\"']+", r"\1***", text)


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def db() -> sqlite3.Connection:
    path = env("DB_PATH", "/data/ads.sqlite")
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------- registry models


def host_of(url: str) -> str:
    h = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _check_http_url(v: str) -> str:
    v = v.strip()
    parts = urlsplit(v)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an http(s) URL: {v[:100]}")
    return v


class KeyPage(BaseModel):
    url: str
    type: str = "other"
    # Optional 09 overrides; the defaults per type are in default_watch().
    css: str | None = None
    xpath: str | None = None
    include_filters: list[str] | None = None
    ignore_patterns: list[str] | None = None
    trigger_text: str | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        return _check_http_url(v)

    @field_validator("type")
    @classmethod
    def _type(cls, v):
        v = v.strip().lower()
        if v not in PAGE_TYPES:
            raise ValueError(f"type must be one of {', '.join(PAGE_TYPES)}")
        return v


def _markets(v):
    out = []
    for m in v or []:
        m = str(m).strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", m):
            raise ValueError(f"market must be a 2-letter country code, got {m!r}")
        if m not in out:
            out.append(m)
    return out


def _domains(v):
    out = []
    for d in v or []:
        h = host_of(str(d).strip())
        if not h or "." not in h:
            raise ValueError(f"not a domain: {d!r}")
        if h not in out:
            out.append(h)
    return out


class CompetitorIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    website: str | None = None
    key_pages: list[KeyPage] = Field(default_factory=list, max_length=20)
    social: dict[str, str] = Field(default_factory=dict)
    meta_page_id: str | None = None
    google_advertiser_id: str | None = None
    linkedin_company_id: str | None = None
    domains: list[str] = Field(default_factory=list, max_length=20)
    markets: list[str] = Field(default_factory=list, max_length=40)
    notes: str | None = Field(default=None, max_length=2000)
    status: str = "active"

    _v_markets = field_validator("markets")(classmethod(lambda cls, v: _markets(v)))
    _v_domains = field_validator("domains")(classmethod(lambda cls, v: _domains(v)))

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = " ".join(v.split())
        if not v or v.isdigit():
            raise ValueError("name must be text (a number would clash with ids)")
        return v

    @field_validator("website")
    @classmethod
    def _website(cls, v):
        return _check_http_url(v) if v else None

    @field_validator("meta_page_id", "linkedin_company_id")
    @classmethod
    def _numeric_id(cls, v):
        if v in (None, ""):
            return None
        v = str(v).strip()
        if not v.isdigit():
            raise ValueError("must be the numeric id")
        return v

    @field_validator("google_advertiser_id")
    @classmethod
    def _google_id(cls, v):
        if v in (None, ""):
            return None
        v = v.strip()
        if not re.fullmatch(r"AR\d{10,30}", v):
            raise ValueError("Google advertiser ids look like AR01234567890123456789")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        return v


class CompetitorPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    website: str | None = None
    key_pages: list[KeyPage] | None = Field(default=None, max_length=20)
    social: dict[str, str] | None = None
    meta_page_id: str | None = None
    google_advertiser_id: str | None = None
    linkedin_company_id: str | None = None
    domains: list[str] | None = None
    markets: list[str] | None = None
    notes: str | None = Field(default=None, max_length=2000)
    status: str | None = None


class StatusIn(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        return v


JSON_COLS = ("key_pages", "social", "domains", "markets", "evidence", "watch_state")


def competitor_out(row: sqlite3.Row) -> dict:
    c = dict(row)
    for k in JSON_COLS:
        c[k] = json.loads(c[k])
    return c


def all_domains(c: dict) -> list[str]:
    out = list(c.get("domains") or [])
    for u in [c.get("website")] + [p["url"] for p in c.get("key_pages") or []]:
        h = host_of(u) if u else ""
        if h and h not in out:
            out.append(h)
    return out


def find_competitor(conn, ref: str) -> sqlite3.Row:
    ref = ref.strip()
    row = None
    if ref.isdigit():
        row = conn.execute("SELECT * FROM competitors WHERE id = ?", (int(ref),)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM competitors WHERE name = ? COLLATE NOCASE", (ref,)).fetchone()
    if row is None:
        raise HTTPException(404, f"competitor {ref!r} not found")
    return row


def by_website(conn, website: str | None) -> sqlite3.Row | None:
    if not website:
        return None
    h = host_of(website)
    for row in conn.execute("SELECT * FROM competitors WHERE website IS NOT NULL"):
        if host_of(row["website"]) == h:
            return row
    return None


# ---------- 09 watches (the change monitor keeps working on its own)

# Lines that change without meaning anything: dates, copyright, cookie notes (all pages);
# urgency counters and countdowns (pricing and product pages). Regexes, case-insensitive (09).
IGNORE_COMMON = [
    r"^\W*(last )?updated\b",
    r"^\W*(©|\(c\)|copyright)",
    r"^\s*(\d{1,2}[./-]){2}\d{2,4}\s*$",
    r"\bcookies?\b",
]
IGNORE_URGENCY = [
    r"\bonly \d+ (left|remaining|spots?|seats?)\b",
    r"\b\d+ (people|others|customers|shoppers) (are )?(viewing|looking|bought|watching)\b",
    r"\b(offer|sale|deal|discount) ends in\b",
    r"^\s*\d+\s*(d|h|m|s|days?|hours?|hrs?|mins?|minutes?|secs?|seconds?)"
    r"(\s*:?\s*\d+\s*(d|h|m|s|hours?|hrs?|mins?|minutes?|secs?|seconds?))*\s*(left)?\s*$",
]
IGNORE_COUNTERS = [r"\b(trusted by|join(ed)?( by)?|loved by) (over )?[\d,.]+\s*[km+]?\b"]


def default_watch(c: dict, page: dict) -> dict:
    t = page.get("type") or "other"
    ignore = IGNORE_COMMON + (IGNORE_URGENCY if t in ("pricing", "product") else []) \
        + (IGNORE_COUNTERS if t in ("home", "about") else [])
    w = {"url": page["url"], "label": f"{c['name']} · {t}", "tag": f"competitor:{c['id']}",
         "css": page.get("css") or None, "xpath": page.get("xpath") or None,
         "include_filters": page.get("include_filters") or [],
         "ignore_patterns": ignore + list(page.get("ignore_patterns") or []),
         "trigger_text": page.get("trigger_text") or None}
    return w


def desired_watches(c: dict) -> list[dict]:
    if c["status"] != "active":
        return []
    pages = list(c.get("key_pages") or [])
    if c.get("website") and not any(p["url"].rstrip("/") == c["website"].rstrip("/") for p in pages):
        pages.insert(0, {"url": c["website"], "type": "home"})
    seen, out = set(), []
    for p in pages:
        if p["url"] in seen:
            continue
        seen.add(p["url"])
        out.append(default_watch(c, p))
    return out


WATCH_KEYS = ("url", "label", "css", "xpath", "include_filters", "ignore_patterns", "trigger_text")


def _same(existing: dict, want: dict) -> bool:
    norm = lambda d, k: d.get(k) or ([] if k in ("include_filters", "ignore_patterns") else None)
    return all(norm(existing, k) == norm(want, k) for k in WATCH_KEYS)


def sync_watches(c: dict) -> dict:
    """Make 09's watches tagged competitor:<id> match the competitor's key pages.

    Idempotent: unchanged watches are kept (their snapshots too); a changed page setting is
    delete + add (a new baseline). Never raises: the result has `error` when 09 failed."""
    base, key = service_url("MONITOR_URL"), env("INTERNAL_API_KEY")
    state = {"synced_at": now(), "watches": [], "added": 0, "removed": 0, "kept": 0, "errors": []}
    if not base:
        state["error"] = "MONITOR_URL is not set: key pages are not watched"
        return state
    tag = f"competitor:{c['id']}"
    headers = {"X-API-Key": key}
    try:
        with httpx.Client(timeout=TIMEOUT, trust_env=False) as client:
            r = client.get(f"{base}/watches", params={"tag": tag})
            r.raise_for_status()
            existing = [w for w in r.json() if isinstance(w, dict) and w.get("tag") == tag]
            want = desired_watches(c)
            keep_ids = set()
            for w in want:
                match = next((e for e in existing if e["id"] not in keep_ids and _same(e, w)), None)
                if match:
                    keep_ids.add(match["id"])
                    state["kept"] += 1
                    state["watches"].append({"id": match["id"], "url": w["url"], "label": w["label"]})
            for e in existing:
                if e["id"] not in keep_ids:
                    d = client.delete(f"{base}/watches/{e['id']}", headers=headers)
                    if d.status_code not in (204, 404):
                        d.raise_for_status()
                    state["removed"] += 1
            kept_urls = {x["url"] for x in state["watches"]}
            for w in want:
                if w["url"] in kept_urls:
                    continue
                body = {k: v for k, v in w.items() if v not in (None, [])}
                a = client.post(f"{base}/watches", json=body, headers=headers)
                if a.status_code == 201:
                    state["added"] += 1
                    state["watches"].append({"id": a.json()["id"], "url": w["url"], "label": w["label"]})
                else:
                    detail = ""
                    try:
                        detail = str(a.json().get("detail"))[:200]
                    except ValueError:
                        pass
                    state["errors"].append({"url": w["url"], "error": f"09 returned {a.status_code} {detail}".strip()})
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        state["error"] = scrub(f"change-monitor (09) failed: {type(e).__name__}: {e}")[:300]
    return state


def save_watch_state(conn, cid: int, state: dict):
    with conn:
        conn.execute("UPDATE competitors SET watch_state = ? WHERE id = ?", (json.dumps(state), cid))


# ---------- endpoints: health and registry


@app.get("/health")
def health():
    errors = []
    if not token():
        errors.append("META_AD_LIBRARY_TOKEN is not set (see README: Meta access)")
    with closing(db()) as conn:
        last = conn.execute("SELECT synced_at, summary FROM syncs ORDER BY id DESC LIMIT 1").fetchone()
        counts = dict(conn.execute("SELECT status, COUNT(*) FROM competitors GROUP BY status").fetchall())
    return {"status": "ok", "configured": not errors, "config_errors": errors,
            "graph_version": graph_version(), "countries": countries(),
            "non_eu_countries": [c for c in countries() if c not in EU],
            "monitor_sync": bool(service_url("MONITOR_URL")),
            "competitors": {s: counts.get(s, 0) for s in STATUSES},
            "last_sync": (dict(last["summary"] and json.loads(last["summary"]), synced_at=last["synced_at"])
                          if last else None)}


def _insert(conn, body: CompetitorIn, evidence=None) -> int:
    ts = now()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO competitors (name, website, key_pages, social, meta_page_id, google_advertiser_id,"
                " linkedin_company_id, domains, markets, notes, status, evidence, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (body.name, body.website, json.dumps([p.model_dump(exclude_none=True) for p in body.key_pages]),
                 json.dumps(body.social), body.meta_page_id, body.google_advertiser_id, body.linkedin_company_id,
                 json.dumps(body.domains), json.dumps(body.markets), body.notes, body.status,
                 json.dumps(evidence or []), ts, ts))
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"a competitor named {body.name!r} exists; use PATCH or ?upsert=true")
    return cur.lastrowid


def _apply_patch(conn, row: sqlite3.Row, patch: dict) -> dict:
    cur = competitor_out(row)
    merged = {k: cur[k] for k in CompetitorIn.model_fields if k in cur}
    merged.update({k: v for k, v in patch.items()})
    body = CompetitorIn(**merged)  # full validation of the result
    try:
        with conn:
            conn.execute(
                "UPDATE competitors SET name=?, website=?, key_pages=?, social=?, meta_page_id=?,"
                " google_advertiser_id=?, linkedin_company_id=?, domains=?, markets=?, notes=?, status=?,"
                " updated_at=? WHERE id=?",
                (body.name, body.website, json.dumps([p.model_dump(exclude_none=True) for p in body.key_pages]),
                 json.dumps(body.social), body.meta_page_id, body.google_advertiser_id, body.linkedin_company_id,
                 json.dumps(body.domains), json.dumps(body.markets), body.notes, body.status, now(), row["id"]))
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"a competitor named {body.name!r} exists")
    return competitor_out(conn.execute("SELECT * FROM competitors WHERE id = ?", (row["id"],)).fetchone())


def _with_watches(conn, c: dict) -> dict:
    state = sync_watches(c)
    save_watch_state(conn, c["id"], state)
    c["watch_state"] = state
    return c


@app.post("/competitors", status_code=201, dependencies=[Depends(require_key)])
def create_competitor(body: CompetitorIn, response: Response, upsert: bool = False):
    """Add a competitor. ?upsert=true updates the one with the same name or website host
    instead of 409 (key pages are merged by URL). Active competitors' pages go to 09."""
    with closing(db()) as conn:
        existing = None
        if upsert:
            existing = conn.execute("SELECT * FROM competitors WHERE name = ? COLLATE NOCASE",
                                    (body.name,)).fetchone() or by_website(conn, body.website)
        if existing is not None:
            cur = competitor_out(existing)
            pages = {p["url"]: p for p in cur["key_pages"]}
            for p in body.key_pages:
                pages[p.url] = p.model_dump(exclude_none=True)
            patch = body.model_dump(exclude_unset=True, exclude={"key_pages", "name"})
            patch["key_pages"] = list(pages.values())
            c = _apply_patch(conn, existing, patch)
            response.status_code = 200
        else:
            cid = _insert(conn, body)
            c = competitor_out(conn.execute("SELECT * FROM competitors WHERE id = ?", (cid,)).fetchone())
        return _with_watches(conn, c)


@app.get("/competitors")
def list_competitors(status: str | None = None):
    with closing(db()) as conn:
        if status:
            rows = conn.execute("SELECT * FROM competitors WHERE status = ? ORDER BY id", (status,))
        else:
            rows = conn.execute("SELECT * FROM competitors ORDER BY id")
        return [competitor_out(r) for r in rows]


@app.get("/competitors/{ref}")
def get_competitor(ref: str):
    with closing(db()) as conn:
        return competitor_out(find_competitor(conn, ref))


@app.patch("/competitors/{ref}", dependencies=[Depends(require_key)])
def patch_competitor(ref: str, body: CompetitorPatch):
    with closing(db()) as conn:
        row = find_competitor(conn, ref)
        c = _apply_patch(conn, row, body.model_dump(exclude_unset=True))
        return _with_watches(conn, c)


@app.post("/competitors/{ref}/status", dependencies=[Depends(require_key)])
def set_status(ref: str, body: StatusIn):
    """Accept (active) or ignore a suggestion, or pause tracking (ignored)."""
    with closing(db()) as conn:
        row = find_competitor(conn, ref)
        c = _apply_patch(conn, row, {"status": body.status})
        return _with_watches(conn, c)


@app.delete("/competitors/{ref}", dependencies=[Depends(require_key)])
def delete_competitor(ref: str):
    with closing(db()) as conn:
        row = find_competitor(conn, ref)
        c = competitor_out(row)
        c["status"] = "ignored"  # desired watches: none
        state = sync_watches(c)
        with conn:
            conn.execute("DELETE FROM ads WHERE competitor_id = ?", (row["id"],))
            conn.execute("DELETE FROM competitors WHERE id = ?", (row["id"],))
    return {"deleted": row["name"], "watches_removed": state.get("removed", 0),
            "watch_error": state.get("error")}


@app.post("/watches/sync", dependencies=[Depends(require_key)])
def sync_all_watches():
    """Re-apply every competitor's pages to 09 (e.g. after 09's database was reset)."""
    out = []
    with closing(db()) as conn:
        for row in conn.execute("SELECT * FROM competitors ORDER BY id").fetchall():
            c = _with_watches(conn, competitor_out(row))
            s = c["watch_state"]
            out.append({"competitor": c["name"], "status": c["status"], **{k: s.get(k) for k in
                        ("added", "removed", "kept", "error")}, "errors": s.get("errors", [])})
    return {"competitors": out}


# ---------- public ad library links (opened by a person; never fetched here)


def links_for(c: dict) -> list[dict]:
    markets = c.get("markets") or countries()
    out = []
    meta_note = ("Meta's API returns commercial ads only when they were delivered in the EU; "
                 "for other markets open this page by hand.")
    for country in ["ALL"] + markets:
        q = {"active_status": "all", "ad_type": "all", "country": country, "media_type": "all"}
        if c.get("meta_page_id"):
            q.update(view_all_page_id=c["meta_page_id"], search_type="page")
        else:
            q.update(q=c["name"], search_type="keyword_unordered")
        out.append({"platform": "meta", "market": country, "api": country in EU,
                    "url": "https://www.facebook.com/ads/library/?" + urlencode(q), "note": meta_note})
    g_note = "Google has no API for commercial ads in the Transparency Center; open by hand."
    if c.get("google_advertiser_id"):
        out.append({"platform": "google", "market": "ALL", "api": False, "note": g_note,
                    "url": f"https://adstransparency.google.com/advertiser/{c['google_advertiser_id']}?region=anywhere"})
    for d in all_domains(c):
        out.append({"platform": "google", "market": "ALL", "api": False, "note": g_note,
                    "url": "https://adstransparency.google.com/?" + urlencode({"region": "anywhere", "domain": d})})
    li = ({"companyIds": c["linkedin_company_id"]} if c.get("linkedin_company_id")
          else {"accountOwner": c["name"]})
    out.append({"platform": "linkedin", "market": "ALL", "api": False,
                "url": "https://www.linkedin.com/ad-library/search?" + urlencode(li),
                "note": "LinkedIn has no API for other companies' ads and forbids automated access; open by hand."})
    out.append({"platform": "tiktok", "market": "EU", "api": False,
                "url": "https://library.tiktok.com/ads?" + urlencode({"region": "all", "adv_name": c["name"]}),
                "note": "TikTok's Commercial Content API needs approved access and is not implemented; "
                        "open by hand (type the name if the search box is empty)."})
    return out


@app.get("/links")
def all_links(status: str = "active"):
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM competitors WHERE status = ? ORDER BY id", (status,)).fetchall()
    return [{"competitor": r["name"], "links": links_for(competitor_out(r))} for r in rows]


@app.get("/links/{ref}")
def competitor_links(ref: str):
    with closing(db()) as conn:
        c = competitor_out(find_competitor(conn, ref))
    return {"competitor": c["name"], "links": links_for(c)}


# ---------- Meta Ad Library API


class MetaError(Exception):
    def __init__(self, status: int, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.message, self.retry_after = status, message, retry_after


def _meta_error(r: httpx.Response) -> MetaError:
    try:
        err = r.json().get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    code, sub = err.get("code"), err.get("error_subcode")
    msg = scrub(str(err.get("message") or ""))[:300]
    fb = f" (Meta code {code}{'/' + str(sub) if sub else ''}: {msg})" if code else f" (HTTP {r.status_code})"
    if code == 190 or r.status_code == 401:
        return MetaError(502, "Meta rejected META_AD_LIBRARY_TOKEN: it is expired or invalid" + fb +
                         ". Generate a new token for your app (Graph API Explorer, then extend it to a "
                         "long-lived token), put it in .env and restart ad-library-sync.")
    if code in RATE_LIMIT_CODES or r.status_code == 429:
        try:
            ra = int(float(r.headers.get("retry-after", "")))
        except ValueError:
            ra = DEFAULT_RETRY_AFTER
        return MetaError(429, f"Meta rate limit reached{fb}. Retry after {ra} s.", retry_after=ra)
    if code in PERMISSION_CODES or r.status_code == 403:
        return MetaError(502, "Meta refused access to the Ad Library API" + fb + ". The token's user must "
                         "have confirmed identity and location (facebook.com/ID) and the app must have "
                         "Ad Library API access (see README: Meta access).")
    return MetaError(502, "Meta Ad Library API error" + fb)


def _get(client: httpx.Client, url: str, params: dict | None) -> dict:
    for attempt in (1, 2):
        try:
            r = client.get(url, params=params)
        except httpx.HTTPError as e:
            raise MetaError(502, f"Meta Graph API unreachable ({type(e).__name__})")
        if r.status_code == 200:
            try:
                body = r.json()
            except ValueError:
                raise MetaError(502, "Meta returned a response that is not JSON")
            if isinstance(body, dict) and "error" in body:
                raise _meta_error(r)
            return body
        err = _meta_error(r)
        if err.status == 429 and attempt == 1 and (err.retry_after or 0) <= RETRY_MAX_WAIT:
            time.sleep(err.retry_after or 0)
            continue
        raise err
    raise MetaError(502, "unreachable")  # pragma: no cover


def fetch_ads(client: httpx.Client, c: dict, market_list: list[str], warnings: list[str]) -> list[dict]:
    """Every page of ads_archive for one competitor. Follows paging.next on graph.facebook.com only."""
    params = {
        "access_token": token(),
        "ad_reached_countries": json.dumps(market_list),
        "ad_active_status": "ALL",
        "ad_type": "ALL",
        "ad_delivery_date_min": (date.today() - timedelta(days=lookback_days())).isoformat(),
        "fields": FIELDS,
        "limit": PAGE_LIMIT,
    }
    if c.get("meta_page_id"):
        params["search_page_ids"] = json.dumps([int(c["meta_page_id"])])
    else:
        params["search_terms"] = c["name"][:100]
        params["search_type"] = "KEYWORD_EXACT_PHRASE"
    url: str | None = f"https://{GRAPH_HOST}/{graph_version()}/ads_archive"
    ads, pages = [], 0
    while url:
        body = _get(client, url, params)
        params = None  # paging.next carries every parameter
        data = body.get("data") or []
        if not data:
            break
        ads += [a for a in data if isinstance(a, dict) and a.get("id")]
        pages += 1
        nxt = (body.get("paging") or {}).get("next")
        if nxt and urlsplit(nxt).scheme == "https" and urlsplit(nxt).hostname == GRAPH_HOST:
            url = nxt
        else:
            if nxt:
                warnings.append(f"{c['name']}: paging link not on {GRAPH_HOST}; stopped")
            url = None
        if url and pages >= MAX_PAGES:
            warnings.append(f"{c['name']}: stopped after {MAX_PAGES} pages ({len(ads)} ads)")
            break
    if not c.get("meta_page_id"):
        name = c["name"].lower()
        before = len(ads)
        ads = [a for a in ads if name in str(a.get("page_name", "")).lower()]
        if before != len(ads):
            warnings.append(f"{c['name']}: no meta_page_id, searched by name; {before - len(ads)} ads "
                            "from other pages were left out. Set meta_page_id for exact results.")
    return ads


def _texts(a: dict, key: str) -> list[str]:
    v = a.get(key) or []
    return [str(x) for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []


def _stopped(a: dict) -> bool:
    stop = a.get("ad_delivery_stop_time")
    if not stop:
        return False
    try:
        return date.fromisoformat(str(stop)[:10]) < date.today()
    except ValueError:
        return True


def store_ads(conn, cid: int, ads: list[dict], ts: str) -> dict:
    counts = {"seen": 0, "new": 0, "changed": 0, "stopped": 0}
    for a in ads:
        counts["seen"] += 1
        rec = {k: _texts(a, f) for k, f in (("bodies", "ad_creative_bodies"), ("link_titles", "ad_creative_link_titles"),
                                            ("link_descriptions", "ad_creative_link_descriptions"),
                                            ("link_captions", "ad_creative_link_captions"))}
        h = hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()
        status = "stopped" if _stopped(a) else "active"
        vals = (str(a.get("page_id") or ""), str(a.get("page_name") or ""),
                json.dumps(rec["bodies"]), json.dumps(rec["link_titles"]), json.dumps(rec["link_descriptions"]),
                json.dumps(rec["link_captions"]), json.dumps(a.get("publisher_platforms") or []),
                json.dumps(a.get("languages") or []), a.get("ad_delivery_start_time"),
                a.get("ad_delivery_stop_time"), status, h)
        old = conn.execute("SELECT * FROM ads WHERE ad_id = ?", (str(a["id"]),)).fetchone()
        if old is None:
            conn.execute("INSERT INTO ads (page_id, page_name, bodies, link_titles, link_descriptions, link_captions,"
                         " platforms, languages, delivery_start, delivery_stop, status, text_hash, ad_id,"
                         " competitor_id, first_seen, last_seen, stopped_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         vals + (str(a["id"]), cid, ts, ts, ts if status == "stopped" else None))
            counts["new"] += 1
            continue
        changed_at = ts if old["text_hash"] != h else old["changed_at"]
        stopped_at = old["stopped_seen_at"]
        if status == "stopped" and old["status"] != "stopped":
            stopped_at = ts
            counts["stopped"] += 1
        elif status == "active":
            stopped_at = None
        if old["text_hash"] != h:
            counts["changed"] += 1
        conn.execute("UPDATE ads SET page_id=?, page_name=?, bodies=?, link_titles=?, link_descriptions=?,"
                     " link_captions=?, platforms=?, languages=?, delivery_start=?, delivery_stop=?, status=?,"
                     " text_hash=?, last_seen=?, changed_at=?, stopped_seen_at=?, competitor_id=? WHERE ad_id=?",
                     vals + (ts, changed_at, stopped_at, cid, str(a["id"])))
    return counts


@app.post("/sync", dependencies=[Depends(require_key)])
def sync(competitor: str | None = None):
    """Fetch every active competitor's ads from the Meta Ad Library API (EU markets only).
    All or nothing: if any call fails, nothing is written."""
    if not token():
        raise HTTPException(503, "META_AD_LIBRARY_TOKEN is not set; ads cannot be synced. "
                                 "The links endpoints still work (see README: Meta access).")
    ts, warnings, fetched = now(), [], []
    with closing(db()) as conn:
        if competitor:
            rows = [find_competitor(conn, competitor)]
        else:
            rows = conn.execute("SELECT * FROM competitors WHERE status = 'active' ORDER BY id").fetchall()
        try:
            with httpx.Client(timeout=TIMEOUT, trust_env=False) as client:
                for row in rows:
                    c = competitor_out(row)
                    wanted = c["markets"] or countries()
                    eu = [m for m in wanted if m in EU]
                    non_eu = [m for m in wanted if m not in EU]
                    if non_eu:
                        warnings.append(f"{c['name']}: {', '.join(non_eu)} outside the EU: Meta's API returns "
                                        "no commercial ads there; use GET /links/{competitor}")
                    if not eu:
                        continue
                    fetched.append((c, fetch_ads(client, c, eu, warnings)))
        except MetaError as e:
            log.warning("sync failed: %s", scrub(e.message))
            headers = {"Retry-After": str(e.retry_after)} if e.retry_after is not None else None
            return JSONResponse({"detail": scrub(e.message)}, status_code=e.status, headers=headers)
        totals = {"seen": 0, "new": 0, "changed": 0, "stopped": 0}
        per = []
        with conn:
            for c, ads in fetched:
                counts = store_ads(conn, c["id"], ads, ts)
                per.append({"competitor": c["name"], **counts})
                for k in totals:
                    totals[k] += counts[k]
            summary = {"competitors": per, **totals, "warnings": warnings}
            conn.execute("INSERT INTO syncs (synced_at, summary) VALUES (?, ?)", (ts, json.dumps(summary)))
    return {"synced_at": ts, **summary}


def parse_since(since: str | None) -> str:
    if not since:
        return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    try:
        if len(since) == 10:
            return datetime.combine(date.fromisoformat(since), datetime.min.time(), timezone.utc).isoformat(
                timespec="seconds")
        d = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(
            timespec="seconds")
    except ValueError:
        raise HTTPException(422, "since must be YYYY-MM-DD or an ISO 8601 date-time")


@app.get("/ads")
def list_ads(competitor: str | None = None, since: str | None = None,
             limit: int = Query(default=100, ge=1, le=1000)):
    """Ads that are new, changed or stopped since `since` (default: 7 days ago)."""
    s = parse_since(since)
    with closing(db()) as conn:
        where, args = ["(a.first_seen >= ? OR a.changed_at >= ? OR a.stopped_seen_at >= ?)"], [s, s, s]
        if competitor:
            where.append("a.competitor_id = ?")
            args.append(find_competitor(conn, competitor)["id"])
        rows = conn.execute(
            "SELECT a.*, c.name AS competitor FROM ads a JOIN competitors c ON c.id = a.competitor_id"
            f" WHERE {' AND '.join(where)} ORDER BY a.first_seen DESC, a.ad_id LIMIT ?", args + [limit]).fetchall()
    ads = []
    for r in rows:
        a = dict(r)
        for k in ("bodies", "link_titles", "link_descriptions", "link_captions", "platforms", "languages"):
            a[k] = json.loads(a[k])
        a["change"] = ("new" if a["first_seen"] >= s else "stopped" if (a["stopped_seen_at"] or "") >= s
                       else "changed")
        a["texts"] = a.pop("bodies")
        a.pop("text_hash")
        a["library_url"] = f"https://www.facebook.com/ads/library/?id={quote(a['ad_id'])}"
        ads.append(a)
    return {"since": s, "count": len(ads), "ads": ads}


# ---------- suggestions from our own sources (never auto-tracked)

TLDS = {"com", "net", "org", "io", "co", "ai", "app", "dev", "shop", "store", "coffee", "eu", "de", "fr",
        "nl", "ie", "uk", "es", "it", "be", "at", "ch", "se", "dk", "no", "fi", "pl", "pt", "us", "ca", "au"}
SECOND_LEVEL = {"co.uk", "com.au", "co.nz", "com.br", "co.jp", "org.uk"}
PLATFORMS = {
    "reddit.com", "redd.it", "ycombinator.com", "github.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
    "facebook.com", "fb.com", "instagram.com", "linkedin.com", "tiktok.com", "medium.com", "substack.com",
    "wikipedia.org", "google.com", "apple.com", "t.co", "bit.ly", "imgur.com", "trustpilot.com",
    "example.com", "example.org", "example.net", "archive.org", "pinterest.com", "threads.net", "mastodon.social",
}
DOMAIN_RE = re.compile(r"(?i)(?<![@\w.-])(?:https?://)?(?:www\.)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?![\w-])")


def registrable(host: str) -> str:
    host = host.lower().strip(".")
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def domains_in(text: str) -> list[tuple[str, str]]:
    """(domain, verbatim snippet) for each domain mentioned in text."""
    out = []
    for m in DOMAIN_RE.finditer(text or ""):
        host = m.group(1).lower()
        if host.rsplit(".", 1)[-1] not in TLDS:
            continue
        d = registrable(host)
        start, end = max(0, m.start() - 80), min(len(text), m.end() + 80)
        out.append((d, text[start:end].strip()))
    return out


def gather_sources(client: httpx.Client) -> tuple[list[dict], dict, list[str]]:
    """Items {source, ref, text} from 58 reviews, 11 mentions and 06 trend-digest notes."""
    items, counts, errors = [], {}, []
    key = {"X-API-Key": env("INTERNAL_API_KEY")}
    reviews = service_url("REVIEWS_URL")
    if reviews:
        try:
            r = client.get(f"{reviews}/reviews", headers=key)
            r.raise_for_status()
            rows = [x for x in r.json() if isinstance(x, dict)]
            items += [{"source": "reviews", "ref": f"review {x.get('id')}", "text": str(x.get("text") or "")}
                      for x in rows]
            counts["reviews"] = len(rows)
        except (httpx.HTTPError, ValueError) as e:
            errors.append(f"reviews (58): {type(e).__name__}")
    listening, q = service_url("LISTENING_URL"), env("LISTENING_QUERY")
    if listening and q:
        try:
            r = client.post(f"{listening}/search", json={"query": q, "days": 30, "limit": 50})
            r.raise_for_status()
            rows = [x for x in r.json().get("mentions") or [] if isinstance(x, dict)]
            for x in rows:
                ref = str(x.get("url") or x.get("title") or "")[:300]
                items.append({"source": "social", "ref": ref,
                              "text": " ".join(str(x.get(k) or "") for k in ("url", "title", "text"))})
            counts["social"] = len(rows)
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            errors.append(f"social listening (11): {type(e).__name__}")
    kb = service_url("KB_URL")
    if kb:
        try:
            r = client.post(f"{kb}/search", json={"query": "competitor alternative launch pricing", "k": 20})
            r.raise_for_status()
            rows = [x for x in r.json().get("results") or [] if isinstance(x, dict)
                    and x.get("source") == "trend-digest"]
            items += [{"source": "trend-digest", "ref": str(x.get("doc_id")), "text": str(x.get("chunk") or "")}
                      for x in rows]
            counts["trend_digest"] = len(rows)
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            errors.append(f"knowledge base (06): {type(e).__name__}")
    return items, counts, errors


@app.post("/suggestions/scan", dependencies=[Depends(require_key)])
def scan_suggestions(min_mentions: int | None = Query(default=None, ge=1, le=50)):
    """Find domains named repeatedly in our reviews, social mentions and trend digests and
    store them as `suggested` (with verbatim evidence). Active and ignored ones are left alone."""
    need = min_mentions or int(env("SUGGEST_MIN_MENTIONS", "2") or 2)
    own = {registrable(host_of(d)) for d in env("OWN_DOMAINS").split(",") if d.strip()}
    with httpx.Client(timeout=TIMEOUT, trust_env=False) as client:
        items, counts, errors = gather_sources(client)
    evidence: dict[str, list[dict]] = {}
    for it in items:
        for d, snippet in domains_in(it["text"]):
            if d in PLATFORMS or d in own:
                continue
            ev = evidence.setdefault(d, [])
            if not any(e["source"] == it["source"] and e["ref"] == it["ref"] for e in ev):
                ev.append({"source": it["source"], "ref": it["ref"], "snippet": snippet[:240]})
    new, updated = [], []
    with closing(db()) as conn:
        known = {}
        for row in conn.execute("SELECT * FROM competitors").fetchall():
            for d in all_domains(competitor_out(row)):
                known[registrable(d)] = row
        for d, ev in sorted(evidence.items(), key=lambda kv: -len(kv[1])):
            if len(ev) < need:
                continue
            row = known.get(d)
            if row is None:
                name = d
                if conn.execute("SELECT 1 FROM competitors WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
                    continue
                body = CompetitorIn(name=name, website=f"https://{d}/", domains=[d], status="suggested",
                                    notes="suggested by /suggestions/scan")
                cid = _insert(conn, body, evidence=ev[:10])
                new.append({"id": cid, "name": name, "mentions": len(ev)})
            elif row["status"] == "suggested":
                old = json.loads(row["evidence"])
                merged = old + [e for e in ev if not any(o["source"] == e["source"] and o["ref"] == e["ref"]
                                                         for o in old)]
                with conn:
                    conn.execute("UPDATE competitors SET evidence = ?, updated_at = ? WHERE id = ?",
                                 (json.dumps(merged[:10]), now(), row["id"]))
                updated.append({"id": row["id"], "name": row["name"], "mentions": len(merged)})
        pending = conn.execute("SELECT COUNT(*) FROM competitors WHERE status = 'suggested'").fetchone()[0]
    return {"scanned": counts, "errors": errors, "min_mentions": need, "new": new, "updated": updated,
            "pending": pending}


@app.get("/suggestions")
def suggestions():
    return list_competitors(status="suggested")
