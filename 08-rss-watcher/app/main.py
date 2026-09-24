"""RSS watcher: poll feeds and return only the items not seen before."""
import hmac
import io
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import feedparser
from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.net import BlockedURL, FetchError, fetch

app = FastAPI(title="rss-watcher")

MAX_SUMMARY = 500
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    feed TEXT NOT NULL,
    guid TEXT NOT NULL,
    title TEXT,
    link TEXT,
    published TEXT,
    summary TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (feed, guid)
)
"""
ITEM_COLS = "feed, title, link, published, summary"


class PollRequest(BaseModel):
    feeds: list[str] | None = None
    max_items_per_feed: int = Field(20, ge=1, le=200)


def db() -> sqlite3.Connection:
    path = Path(os.getenv("DB_PATH", "/data/rss.sqlite"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def default_feeds() -> list[str]:
    path = Path(os.getenv("FEEDS_FILE", "/config/feeds.txt"))
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def plain_text(html: str) -> str:
    text = " ".join(BeautifulSoup(html or "", "html.parser").get_text(" ").split())
    return text if len(text) <= MAX_SUMMARY else text[: MAX_SUMMARY - 3].rstrip() + "..."


def published_iso(entry) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat() if parsed else None


def read_feed(url: str, limit: int) -> list[dict]:
    page = fetch(url, html_only=False)
    # Parse bytes we fetched ourselves (SSRF guard, size cap); feedparser never touches the network.
    parsed = feedparser.parse(
        io.BytesIO(page.content), response_headers={"content-type": page.headers.get("content-type", "")}
    )
    if parsed.bozo and not parsed.entries:
        raise FetchError(f"not a valid feed: {parsed.get('bozo_exception', 'parse error')}")
    items = []
    for entry in parsed.entries[:limit]:
        link = entry.get("link")
        guid = entry.get("id") or link or entry.get("title")
        if not guid:
            continue
        items.append({
            "guid": guid,
            "feed": url,
            "title": " ".join((entry.get("title") or "").split()) or None,
            "link": link,
            "published": published_iso(entry),
            "summary": plain_text(entry.get("summary", "")),
        })
    return items


@app.get("/health")
def health():
    return {"status": "ok"}


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not set on the server; write endpoints are disabled")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# Polling marks items as seen, so an unauthenticated caller could consume "new" items
# before the morning digest runs (security audit, low).
@app.post("/poll", dependencies=[Depends(require_key)])
def poll(req: PollRequest):
    feeds = list(dict.fromkeys(f.strip() for f in (req.feeds or default_feeds()) if f.strip()))
    if not feeds:
        raise HTTPException(422, "no feeds: pass 'feeds' or put URLs in FEEDS_FILE")
    new_items, errors = [], []
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as conn:
        for feed in feeds:
            # One bad feed is reported, never fatal.
            try:
                items = read_feed(feed, req.max_items_per_feed)
            except (BlockedURL, FetchError) as exc:
                errors.append({"feed": feed, "error": str(exc)})
                continue
            except Exception as exc:  # malformed feed content we did not anticipate
                errors.append({"feed": feed, "error": f"{type(exc).__name__}: {exc}"})
                continue
            with conn:
                for item in items:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO items (feed, guid, title, link, published, summary, fetched_at)"
                        " VALUES (:feed, :guid, :title, :link, :published, :summary, :fetched_at)",
                        {**item, "fetched_at": fetched_at},
                    )
                    if cur.rowcount:
                        new_items.append({k: item[k] for k in ("feed", "title", "link", "published", "summary")})
    return {"new_items": new_items, "checked": len(feeds), "errors": errors}


@app.get("/items")
def list_items(limit: int = Query(50, ge=1, le=500)):
    with closing(db()) as conn:
        rows = conn.execute(
            f"SELECT {ITEM_COLS} FROM items ORDER BY COALESCE(published, fetched_at) DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]
