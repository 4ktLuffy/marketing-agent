"""Change monitor: watch competitor pages and report text diffs since the last check."""
import difflib
import hmac
import os
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from app.net import BlockedURL, FetchError, check_url, fetch

app = FastAPI(title="change-monitor")

MAX_DIFF = 4000
DROP_TAGS = ["script", "style", "nav", "footer", "noscript", "template", "svg"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL,
    last_checked_at TEXT,
    snapshot TEXT
)
"""
PUBLIC_COLS = "id, url, label, created_at, last_checked_at"


class WatchIn(BaseModel):
    url: str
    label: str | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    path = Path(os.getenv("DB_PATH", "/data/monitor.sqlite"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not set on the server; write endpoints are disabled")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def visible_lines(html: bytes, encoding: str | None) -> list[str]:
    """Visible text, one normalized line per block, empty lines dropped."""
    soup = BeautifulSoup(html, "html.parser", from_encoding=encoding)
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    if soup.head:
        soup.head.decompose()
    lines = (" ".join(line.split()) for line in soup.get_text("\n").splitlines())
    return [line for line in lines if line]


def compare(old: list[str], new: list[str]) -> dict:
    diff = "\n".join(difflib.unified_diff(old, new, "before", "after", lineterm="", n=1))
    if len(diff) > MAX_DIFF:
        diff = diff[:MAX_DIFF] + "\n... (diff truncated)"
    old_words, new_words = Counter(" ".join(old).split()), Counter(" ".join(new).split())
    return {
        "diff": diff,
        "added_words": sum((new_words - old_words).values()),
        "removed_words": sum((old_words - new_words).values()),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/watches", status_code=201, dependencies=[Depends(require_key)])
def add_watch(body: WatchIn):
    url = body.url.strip()
    try:
        check_url(url)
    except (BlockedURL, FetchError) as exc:
        raise HTTPException(422, str(exc))
    with closing(db()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO watches (url, label, created_at) VALUES (?, ?, ?)", (url, body.label, now())
        )
        row = conn.execute(f"SELECT {PUBLIC_COLS} FROM watches WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


@app.get("/watches")
def list_watches():
    with closing(db()) as conn:
        return [dict(r) for r in conn.execute(f"SELECT {PUBLIC_COLS} FROM watches ORDER BY id")]


@app.delete("/watches/{watch_id}", status_code=204, dependencies=[Depends(require_key)])
def delete_watch(watch_id: int):
    with closing(db()) as conn, conn:
        if conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,)).rowcount == 0:
            raise HTTPException(404, f"watch {watch_id} not found")
    return Response(status_code=204)


@app.post("/check")
def check():
    changed, unchanged, errors = [], 0, []
    with closing(db()) as conn:
        watches = conn.execute("SELECT * FROM watches ORDER BY id").fetchall()
        for w in watches:
            try:
                page = fetch(w["url"])
            except (BlockedURL, FetchError) as exc:
                errors.append({"id": w["id"], "url": w["url"], "error": str(exc)})
                continue
            lines = visible_lines(page.content, page.encoding)
            # First check stores the baseline and counts as unchanged.
            old = w["snapshot"].split("\n") if w["snapshot"] else []
            if w["snapshot"] is not None and lines != old:
                changed.append({"id": w["id"], "url": w["url"], "label": w["label"], **compare(old, lines)})
            else:
                unchanged += 1
            with conn:
                conn.execute(
                    "UPDATE watches SET snapshot = ?, last_checked_at = ? WHERE id = ?",
                    ("\n".join(lines), now(), w["id"]),
                )
    return {"changed": changed, "unchanged": unchanged, "errors": errors}
