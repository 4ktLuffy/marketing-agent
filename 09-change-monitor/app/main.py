"""Change monitor: watch competitor pages and report text diffs since the last check."""
import codecs
import copy
import difflib
import hmac
import json
import os
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import lxml.html
from bs4 import BeautifulSoup
from cssselect import SelectorError
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from lxml import etree
from lxml.cssselect import CSSSelector
from pydantic import BaseModel

from app.net import BlockedURL, FetchError, check_url, fetch

app = FastAPI(title="change-monitor")

MAX_DIFF = 4000
DROP_TAGS = ["script", "style", "nav", "footer", "noscript", "template", "svg"]
# Inside a css/xpath selection the user chose the region, so nav/footer stay.
SELECTED_DROP_TAGS = ["script", "style", "noscript", "template", "svg"]
MAX_SELECTOR = 1000
MAX_REGEX = 300
MAX_REGEXES = 20
MAX_TAG = 100
RE_FLAGS = re.IGNORECASE
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
# Columns added after the first release; db() adds them to an older database file.
NEW_COLUMNS = ["css", "xpath", "include_filters", "ignore_patterns", "trigger_text", "tag"]
LIST_COLUMNS = {"include_filters", "ignore_patterns"}  # stored as JSON arrays
PUBLIC_COLS = "id, url, label, created_at, last_checked_at, " + ", ".join(NEW_COLUMNS)
NO_MATCH = "css/xpath matched nothing on the page"


class WatchIn(BaseModel):
    url: str
    label: str | None = None
    css: str | None = None
    xpath: str | None = None
    include_filters: list[str] = []
    ignore_patterns: list[str] = []
    trigger_text: str | None = None
    tag: str | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    path = Path(os.getenv("DB_PATH", "/data/monitor.sqlite"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(watches)")}
    missing = [c for c in NEW_COLUMNS if c not in have]
    if missing:
        with conn:
            for col in missing:
                conn.execute(f"ALTER TABLE watches ADD COLUMN {col} TEXT")
    return conn


def watch_json(row: sqlite3.Row) -> dict:
    out = {k: row[k] for k in PUBLIC_COLS.split(", ")}
    for col in LIST_COLUMNS:
        out[col] = json.loads(out[col]) if out[col] else []
    return out


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not set on the server; write endpoints are disabled")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def _soup_lines(soup: BeautifulSoup, drop: list[str]) -> list[str]:
    for tag in soup.find_all(drop):
        tag.decompose()
    lines = (" ".join(line.split()) for line in soup.get_text("\n").splitlines())
    return [line for line in lines if line]


def visible_lines(html: bytes, encoding: str | None) -> list[str]:
    """Visible text, one normalized line per block, empty lines dropped."""
    soup = BeautifulSoup(html, "html.parser", from_encoding=encoding)
    if soup.head:
        soup.head.decompose()
    return _soup_lines(soup, DROP_TAGS)


def _text_lines(text: str) -> list[str]:
    lines = (" ".join(line.split()) for line in text.splitlines())
    return [line for line in lines if line]


def selected_lines(html: bytes, encoding: str | None, css: str | None, xpath: str | None) -> list[str] | None:
    """Lines of the nodes matched by css then xpath; None when nothing matched."""
    source: bytes | str = html
    if encoding:  # the header charset wins, as with BeautifulSoup in visible_lines
        try:
            source = html.decode(codecs.lookup(encoding).name, errors="replace")
        except LookupError:
            pass  # unknown charset: let lxml detect it from the bytes
    try:
        doc = lxml.html.document_fromstring(source)
    except ValueError:  # str input with an <?xml encoding=...?> declaration
        doc = lxml.html.document_fromstring(html)
    found: list = []  # elements and strings, in match order
    seen: set[int] = set()

    def take(item):
        if isinstance(item, etree._Element):
            if not isinstance(item.tag, str):  # comment or processing instruction
                return
            if id(item) in seen:
                return
            seen.add(id(item))
        found.append(item)

    if css:
        for el in CSSSelector(css)(doc):
            take(el)
    if xpath:
        result = doc.xpath(xpath)
        if isinstance(result, list):
            for item in result:
                take(item if isinstance(item, etree._Element) else str(item))
        elif isinstance(result, bool):
            take(str(result).lower())
        elif isinstance(result, float):
            take(str(int(result)) if result.is_integer() else str(result))
        elif result:  # a non-empty string, e.g. string(//h1)
            take(str(result))
    if not found:
        return None
    lines: list[str] = []
    for item in found:
        if isinstance(item, str):
            lines += _text_lines(item)
            continue
        el = copy.deepcopy(item)
        el.tail = None
        markup = lxml.html.tostring(el, encoding="unicode", with_tail=False)
        lines += _soup_lines(BeautifulSoup(markup, "html.parser"), SELECTED_DROP_TAGS)
    return lines


def filter_lines(lines: list[str], include: list[str], ignore: list[str]) -> list[str]:
    if include:
        keep = [re.compile(p, RE_FLAGS) for p in include]
        lines = [line for line in lines if any(p.search(line) for p in keep)]
    if ignore:
        drop = [re.compile(p, RE_FLAGS) for p in ignore]
        lines = [line for line in lines if not any(p.search(line) for p in drop)]
    return lines


def trigger_event(pattern: str, old: list[str], new: list[str]) -> str | None:
    """'appeared' if a matching line is new, else 'disappeared' if one went away, else None."""
    rx = re.compile(pattern, RE_FLAGS)
    before = {line for line in old if rx.search(line)}
    after = {line for line in new if rx.search(line)}
    if after - before:
        return "appeared"
    if before - after:
        return "disappeared"
    return None


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


def _blank_to_none(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


def validate_watch(body: WatchIn) -> dict:
    """Normalized selector/filter fields, or HTTPException(422) with a readable reason."""
    css, xpath = _blank_to_none(body.css), _blank_to_none(body.xpath)
    for name, sel in (("css", css), ("xpath", xpath)):
        if sel and len(sel) > MAX_SELECTOR:
            raise HTTPException(422, f"{name} is longer than {MAX_SELECTOR} characters")
    if css:
        try:
            CSSSelector(css)
        except SelectorError as exc:
            raise HTTPException(422, f"invalid css selector {css!r}: {exc}")
    if xpath:
        try:
            # Evaluating on an empty page also catches unknown functions and prefixes.
            etree.XPath(xpath)(lxml.html.document_fromstring("<html><body></body></html>"))
        except etree.XPathError as exc:
            raise HTTPException(422, f"invalid xpath {xpath!r}: {exc}")

    def check_regex(name: str, pattern: str):
        if len(pattern) > MAX_REGEX:
            raise HTTPException(422, f"{name}: regex longer than {MAX_REGEX} characters")
        try:
            re.compile(pattern, RE_FLAGS)
        except re.error as exc:
            raise HTTPException(422, f"{name}: invalid regex {pattern!r}: {exc}")

    for name in ("include_filters", "ignore_patterns"):
        patterns = getattr(body, name)
        if len(patterns) > MAX_REGEXES:
            raise HTTPException(422, f"{name}: at most {MAX_REGEXES} patterns")
        for pattern in patterns:
            if not pattern:
                raise HTTPException(422, f"{name}: empty pattern")
            check_regex(name, pattern)
    trigger = body.trigger_text or None
    if trigger:
        check_regex("trigger_text", trigger)
    if body.tag is not None and len(body.tag) > MAX_TAG:
        raise HTTPException(422, f"tag is longer than {MAX_TAG} characters")
    return {
        "css": css,
        "xpath": xpath,
        "include_filters": json.dumps(body.include_filters),
        "ignore_patterns": json.dumps(body.ignore_patterns),
        "trigger_text": trigger,
        "tag": body.tag,
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
    fields = validate_watch(body)
    cols = ["url", "label", "created_at", *fields]
    with closing(db()) as conn, conn:
        cur = conn.execute(
            f"INSERT INTO watches ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            (url, body.label, now(), *fields.values()),
        )
        row = conn.execute(f"SELECT {PUBLIC_COLS} FROM watches WHERE id = ?", (cur.lastrowid,)).fetchone()
    return watch_json(row)


@app.get("/watches")
def list_watches(tag: str | None = None):
    with closing(db()) as conn:
        if tag is None:
            rows = conn.execute(f"SELECT {PUBLIC_COLS} FROM watches ORDER BY id")
        else:
            rows = conn.execute(f"SELECT {PUBLIC_COLS} FROM watches WHERE tag = ? ORDER BY id", (tag,))
        return [watch_json(r) for r in rows]


@app.delete("/watches/{watch_id}", status_code=204, dependencies=[Depends(require_key)])
def delete_watch(watch_id: int):
    with closing(db()) as conn, conn:
        if conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,)).rowcount == 0:
            raise HTTPException(404, f"watch {watch_id} not found")
    return Response(status_code=204)


def page_lines(w: sqlite3.Row, page) -> list[str]:
    """select -> include -> ignore. Raises FetchError when the selectors match nothing."""
    if w["css"] or w["xpath"]:
        try:
            lines = selected_lines(page.content, page.encoding, w["css"], w["xpath"])
        except (etree.ParserError, etree.XPathError, SelectorError, ValueError) as exc:
            raise FetchError(f"could not apply css/xpath: {exc}") from exc
        if lines is None:
            raise FetchError(NO_MATCH)
    else:
        lines = visible_lines(page.content, page.encoding)
    include = json.loads(w["include_filters"]) if w["include_filters"] else []
    ignore = json.loads(w["ignore_patterns"]) if w["ignore_patterns"] else []
    return filter_lines(lines, include, ignore)


@app.post("/check", dependencies=[Depends(require_key)])  # it overwrites stored snapshots
def check():
    changed, unchanged, errors = [], 0, []
    with closing(db()) as conn:
        watches = conn.execute("SELECT * FROM watches ORDER BY id").fetchall()
        for w in watches:
            try:
                page = fetch(w["url"])
                lines = page_lines(w, page)
            except (BlockedURL, FetchError) as exc:
                errors.append({"id": w["id"], "url": w["url"], "error": str(exc)})
                continue
            # First check stores the baseline and counts as unchanged.
            old = w["snapshot"].split("\n") if w["snapshot"] else []
            item = None
            if w["snapshot"] is not None and lines != old:
                item = {"id": w["id"], "url": w["url"], "label": w["label"], **compare(old, lines)}
                if w["trigger_text"]:
                    event = trigger_event(w["trigger_text"], old, lines)
                    item = {**item, "trigger": event} if event else None
            if item:
                changed.append(item)
            else:
                unchanged += 1
            with conn:
                conn.execute(
                    "UPDATE watches SET snapshot = ?, last_checked_at = ? WHERE id = ?",
                    ("\n".join(lines), now(), w["id"]),
                )
    return {"changed": changed, "unchanged": unchanged, "errors": errors}
