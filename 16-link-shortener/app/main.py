"""Link shortener: short slugs that redirect and count clicks (no IPs stored)."""
import hmac
import os
import re
import secrets
import sqlite3
import string
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

app = FastAPI(title="link-shortener")

SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]{3,40}$")
RESERVED = {"health", "links", "docs", "openapi.json", "redoc"}
ALPHABET = string.ascii_letters + string.digits

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (slug TEXT PRIMARY KEY, url TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clicks (slug TEXT NOT NULL, ts TEXT NOT NULL, referrer_host TEXT);
CREATE INDEX IF NOT EXISTS clicks_slug ON clicks (slug);
"""


def db() -> sqlite3.Connection:
    # Env read per call so tests (and restarts) can point at a different file.
    path = os.getenv("DB_PATH", "/data/links.sqlite")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def base_url() -> str:
    return os.getenv("BASE_URL", "http://localhost:8116").rstrip("/")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_key(x_api_key: str | None) -> None:
    expected = os.getenv("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "invalid or missing X-API-Key")


class LinkRequest(BaseModel):
    url: str = Field(max_length=2048)
    slug: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/links", status_code=201)
def create_link(req: LinkRequest, x_api_key: str | None = Header(None)):
    require_key(x_api_key)
    parts = urlsplit(req.url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(422, "url must be absolute http(s)")
    url = req.url.strip()

    with closing(db()) as conn, conn:
        if req.slug is not None:
            if not SLUG_RE.match(req.slug):
                raise HTTPException(422, "slug must match ^[a-zA-Z0-9_-]{3,40}$")
            if req.slug.lower() in RESERVED:
                raise HTTPException(422, f"slug '{req.slug}' is reserved")
            try:
                conn.execute("INSERT INTO links VALUES (?, ?, ?)", (req.slug, url, now()))
            except sqlite3.IntegrityError:
                raise HTTPException(409, f"slug '{req.slug}' already exists")
            slug = req.slug
        else:
            for _ in range(10):  # 62^6 space; a collision twice in a row is already unlikely
                slug = "".join(secrets.choice(ALPHABET) for _ in range(6))
                try:
                    conn.execute("INSERT INTO links VALUES (?, ?, ?)", (slug, url, now()))
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise HTTPException(500, "could not allocate a free slug")
    return {"slug": slug, "short_url": f"{base_url()}/{slug}", "url": url}


def query_matches(url: str, wanted: dict[str, str]) -> bool:
    """True if the target URL's query has every wanted param with exactly that value."""
    params = parse_qs(urlsplit(url).query, keep_blank_values=True)
    return all(value in params.get(key, []) for key, value in wanted.items())


@app.get("/links")
def list_links(
    utm_campaign: str | None = None,
    utm_content: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
):
    """Links newest first, optionally filtered on the target URL's utm_* params (no key)."""
    wanted = {k: v for k, v in (("utm_campaign", utm_campaign), ("utm_content", utm_content)) if v}
    out = []
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT l.slug, l.url, l.created_at, "
            "(SELECT COUNT(*) FROM clicks c WHERE c.slug = l.slug) "
            "FROM links l ORDER BY l.created_at DESC, l.rowid DESC"
        )
        for slug, url, created_at, clicks in rows:
            if wanted and not query_matches(url, wanted):
                continue
            out.append({"slug": slug, "short_url": f"{base_url()}/{slug}", "url": url,
                        "clicks": clicks, "created_at": created_at})
            if len(out) >= limit:
                break
    return out


@app.get("/links/{slug}/stats")
def stats(slug: str):
    with closing(db()) as conn:
        row = conn.execute("SELECT url FROM links WHERE slug = ?", (slug,)).fetchone()
        if not row:
            raise HTTPException(404, "unknown slug")
        by_day = dict(conn.execute(
            "SELECT substr(ts, 1, 10) AS d, COUNT(*) FROM clicks WHERE slug = ? GROUP BY d ORDER BY d",
            (slug,),
        ).fetchall())
        referrers = dict(conn.execute(
            "SELECT COALESCE(referrer_host, 'direct') AS h, COUNT(*) FROM clicks WHERE slug = ? "
            "GROUP BY h ORDER BY COUNT(*) DESC",
            (slug,),
        ).fetchall())
    return {"slug": slug, "url": row[0], "clicks": sum(by_day.values()),
            "by_day": by_day, "referrers": referrers}


# Declared last so /health and /links/... are matched first.
@app.get("/{slug}")
def follow(slug: str, request: Request):
    referrer_host = urlsplit(request.headers.get("referer", "")).hostname
    with closing(db()) as conn, conn:
        row = conn.execute("SELECT url FROM links WHERE slug = ?", (slug,)).fetchone()
        if not row:
            raise HTTPException(404, "unknown slug")
        conn.execute("INSERT INTO clicks VALUES (?, ?, ?)", (slug, now(), referrer_host))
    # no-store so browsers and proxies don't skip us and lose the click
    return RedirectResponse(row[0], status_code=302, headers={"Cache-Control": "no-store"})
