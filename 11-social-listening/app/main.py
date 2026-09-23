"""Social listening: recent mentions of a query on Hacker News and Reddit."""
import asyncio
import html
import os
import re
import time
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="social-listening")

HN_URL = "https://hn.algolia.com/api/v1/search_by_date"
REDDIT_URL = "https://www.reddit.com/search.json"
REDDIT_USER_AGENT = os.getenv(
    "REDDIT_USER_AGENT", "marketing-agent-social-listening/1.0 (self-hosted brand monitoring)"
)
TEXT_LIMIT = 500

Source = Literal["hackernews", "reddit"]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    sources: list[Source] = Field(default=["hackernews", "reddit"], min_length=1)
    days: int = Field(7, ge=1, le=365)
    limit: int = Field(25, ge=1, le=100)  # per source


def plain(text: str | None) -> str:
    """HTML fragment -> collapsed plain text, capped at TEXT_LIMIT chars."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = " ".join(html.unescape(text).split())
    return text[:TEXT_LIMIT]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


async def search_hn(client: httpx.AsyncClient, req: SearchRequest, since: int) -> list[dict]:
    r = await client.get(HN_URL, params={
        "query": req.query,
        "tags": "(story,comment)",
        "numericFilters": f"created_at_i>{since}",
        "hitsPerPage": req.limit,
    })
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        item_url = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        is_comment = "comment" in (h.get("_tags") or [])
        out.append({
            "source": "hackernews",
            "title": h.get("title") or h.get("story_title") or "",
            "url": item_url if is_comment else (h.get("url") or item_url),
            "author": h.get("author") or "",
            "score": h.get("points") or 0,
            "comments": h.get("num_comments") or 0,
            "created_at": iso(h.get("created_at_i") or 0),
            "_ts": h.get("created_at_i") or 0,
            "text": plain(h.get("comment_text") or h.get("story_text")),
        })
    return out


def reddit_window(days: int) -> str:
    for limit, t in ((1, "day"), (7, "week"), (31, "month"), (365, "year")):
        if days <= limit:
            return t
    return "all"


async def search_reddit(client: httpx.AsyncClient, req: SearchRequest, since: int) -> list[dict]:
    r = await client.get(
        REDDIT_URL,
        params={"q": req.query, "sort": "new", "t": reddit_window(req.days), "limit": req.limit},
        headers={"User-Agent": REDDIT_USER_AGENT},
    )
    if r.status_code in (403, 429):
        raise RuntimeError(f"HTTP {r.status_code}: Reddit refused the unauthenticated request")
    r.raise_for_status()
    out = []
    for child in r.json().get("data", {}).get("children", []):
        d = child.get("data", {})
        ts = int(d.get("created_utc") or 0)
        out.append({
            "source": "reddit",
            "title": d.get("title") or "",
            "url": f"https://www.reddit.com{d['permalink']}" if d.get("permalink") else d.get("url", ""),
            "author": d.get("author") or "",
            "score": d.get("score") or 0,
            "comments": d.get("num_comments") or 0,
            "created_at": iso(ts),
            "_ts": ts,
            "text": plain(d.get("selftext")),
        })
    return out


SEARCHERS = {"hackernews": search_hn, "reddit": search_reddit}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search")
async def search(req: SearchRequest):
    since = int(time.time()) - req.days * 86400
    sources = list(dict.fromkeys(req.sources))
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(SEARCHERS[s](client, req, since) for s in sources), return_exceptions=True
        )

    mentions, errors = [], []
    for source, result in zip(sources, results):
        if isinstance(result, Exception):
            errors.append({"source": source, "error": str(result) or type(result).__name__})
        else:
            mentions += [m for m in result if m["_ts"] >= since]
    mentions.sort(key=lambda m: m["_ts"], reverse=True)
    for m in mentions:
        del m["_ts"]
    return {"mentions": mentions, "errors": errors}
