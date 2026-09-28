"""Feed optimizer: better product titles (and descriptions) for a Google Merchant Center feed.

Upload a feed file (CSV or TSV). Every product gets a rule-based title at once (no LLM). POST
/propose asks the llm-gateway (03) for a title per product and checks it in code: a title with
a number, attribute, claim or brand the product's own row does not have is rejected, and the
rule-based title is used instead. A person approves products with the approver key. The export
is the uploaded file with only the approved titles and descriptions replaced. Nothing is sent to
Merchant Center: the person uploads the file there.
"""
import hmac
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import checks, feed, services

app = FastAPI(title="feed-optimizer")

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    format TEXT NOT NULL,
    raw BLOB NOT NULL,
    rows INTEGER NOT NULL,
    columns TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES batches (id) ON DELETE CASCADE,
    pos INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    row TEXT NOT NULL,
    rule_title TEXT NOT NULL,
    warnings TEXT NOT NULL,
    state TEXT NOT NULL,
    proposed_title TEXT NOT NULL,
    title_source TEXT NOT NULL,
    proposed_description TEXT,
    description_source TEXT NOT NULL,
    llm_title TEXT,
    llm_description TEXT,
    title_rejected TEXT NOT NULL DEFAULT '[]',
    description_rejected TEXT NOT NULL DEFAULT '[]',
    llm_error TEXT,
    approved_title TEXT,
    approved_description TEXT,
    approved_at TEXT,
    approved_by TEXT,
    UNIQUE (batch_id, product_id)
);
CREATE INDEX IF NOT EXISTS products_batch ON products (batch_id, pos);
"""
_migrate_lock = threading.Lock()
_migrated: set[str] = set()
_propose_lock = threading.Lock()  # one propose run at a time: two runs must not call the model twice per product


# ---------- settings


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name) or default)
    except ValueError:
        return default
    return min(max(v, lo), hi)


def max_bytes() -> int:
    return env_int("FEED_MAX_BYTES", 10_000_000, 1000, 100_000_000)


def max_rows() -> int:
    return env_int("FEED_MAX_ROWS", 5000, 1, 100_000)


# ---------- storage


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/feeds.sqlite")


def migrate(conn: sqlite3.Connection) -> None:
    path = db_path()
    if path in _migrated:
        return
    with _migrate_lock:
        if path in _migrated:
            return
        conn.execute("PRAGMA journal_mode=WAL")
        for stmt in filter(str.strip, SCHEMA.split(";")):
            conn.execute(stmt)
        _migrated.add(path)


@contextmanager
def db():
    conn = sqlite3.connect(db_path(), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        migrate(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def write(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def batch_or_404(conn, batch_id: int) -> dict:
    row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    return dict(row)


def batch_brands(conn, batch_id: int) -> set[str]:
    brands = set()
    for r in conn.execute("SELECT row FROM products WHERE batch_id = ?", (batch_id,)):
        b = checks.tidy(json.loads(r["row"]).get("brand") or "")
        if b:
            brands.add(b)
    return brands


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def require_approver(x_approver_key: str | None) -> None:
    """Approving changes to a live product feed needs APPROVER_KEY. Unset = refused (no open mode)."""
    expected = os.environ.get("APPROVER_KEY")
    if not expected:
        raise HTTPException(503, "APPROVER_KEY is not configured on this service")
    if not (x_approver_key and hmac.compare_digest(x_approver_key, expected)):
        raise HTTPException(403, "approving needs the approver key (X-Approver-Key)")


# ---------- proposals


def _decide_title(row: dict, ctx: checks.Context, llm_title: str | None) -> tuple[str, str, list[dict]]:
    """(title, source, reasons the model's title was rejected). Source: llm, rules or original."""
    original = row.get("title") or ""
    rejected: list[dict] = []
    if llm_title is not None:
        t = checks.tidy(llm_title)
        if len(t) > 1 and t[0] == t[-1] == '"':
            t = t[1:-1].strip()
        rejected = checks.check(t, ctx, "title")
        if not rejected:
            return (original, "original", []) if t == checks.tidy(original) else (t, "llm", [])
    rule, _ = checks.rule_title(row, ctx)
    if rule and not checks.check(rule, ctx, "title") and rule != checks.tidy(original):
        return rule, "rules", rejected
    return original, "original", rejected


def _decide_description(row: dict, ctx: checks.Context, llm_desc: str | None) -> tuple[str, str, list[dict]]:
    original = row.get("description") or ""
    if not llm_desc or not llm_desc.strip():
        return original, "original", []
    d = checks.tidy(llm_desc)
    rejected = checks.check(d, ctx, "description")
    if rejected:
        return original, "original", rejected
    return (original, "original", []) if d == checks.tidy(original) else (d, "llm", [])


def _product_out(r: sqlite3.Row, full: bool = True) -> dict:
    row = json.loads(r["row"])
    out = {
        "id": r["product_id"], "state": r["state"],
        "title": {"before": row.get("title") or "", "after": r["proposed_title"], "source": r["title_source"],
                  "rule_based": r["rule_title"], "model": r["llm_title"],
                  "rejected": json.loads(r["title_rejected"])},
        "description": {"before": row.get("description") or "", "after": r["proposed_description"],
                        "source": r["description_source"], "model": r["llm_description"],
                        "rejected": json.loads(r["description_rejected"])},
        "warnings": json.loads(r["warnings"]),
        "error": r["llm_error"],
        "changed": _changed(r),
        "approved": r["approved_at"] is not None,
        "approved_at": r["approved_at"], "approved_by": r["approved_by"],
    }
    if not full:
        out["description"].pop("before")
    return out


def _changed(r) -> bool:
    row = json.loads(r["row"])
    return r["proposed_title"] != (row.get("title") or "") or \
        (r["proposed_description"] is not None and r["proposed_description"] != (row.get("description") or ""))


def summary(conn, b: dict) -> dict:
    rows = conn.execute("SELECT * FROM products WHERE batch_id = ?", (b["id"],)).fetchall()
    reasons: dict[str, int] = {}
    t_rej = d_rej = 0
    for r in rows:
        tr, dr = json.loads(r["title_rejected"]), json.loads(r["description_rejected"])
        t_rej += bool(tr)
        d_rej += bool(dr)
        for code in {x["code"] for x in tr} | {x["code"] for x in dr}:
            reasons[code] = reasons.get(code, 0) + 1
    src = lambda s: sum(1 for r in rows if r["state"] == "proposed" and r["title_source"] == s)  # noqa: E731
    return {
        "id": b["id"], "name": b["name"], "format": b["format"], "created_at": b["created_at"],
        "products": len(rows),
        "pending": sum(1 for r in rows if r["state"] == "pending"),
        "proposed": sum(1 for r in rows if r["state"] == "proposed" and _changed(r)),
        "unchanged": sum(1 for r in rows if r["state"] == "proposed" and not _changed(r)),
        "titles": {"model": src("llm"), "rule_based": src("rules"), "original": src("original")},
        "descriptions_from_model": sum(1 for r in rows if r["description_source"] == "llm"),
        "rejected": {"titles": t_rej, "descriptions": d_rej, "reasons": dict(sorted(reasons.items()))},
        "errors": sum(1 for r in rows if r["llm_error"]),
        "approved": sum(1 for r in rows if r["approved_at"]),
        "columns": json.loads(b["columns"]),
    }


# ---------- endpoints


@app.get("/health")
def health():
    return {"status": "ok", "gateway": bool(services.gateway_url()), "max_rows": max_rows(), "max_bytes": max_bytes()}


def _store(name: str, data: bytes) -> dict:
    try:
        parsed = feed.parse(data, max_rows())
    except feed.FeedError as exc:
        raise HTTPException(422, str(exc)) from None
    rows = parsed.rows()
    brands = {checks.tidy(r.get("brand") or "") for _, r in rows} - {""}
    known = set(checks.EVIDENCE_FIELDS) | set(feed.NEVER_CHANGED)
    with db() as conn, write(conn):
        cur = conn.execute("INSERT INTO batches (name, format, raw, rows, columns, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (name, "tsv" if parsed.delimiter == "\t" else "csv", data, len(rows),
                            json.dumps(parsed.columns), fmt(now_dt())))
        bid = cur.lastrowid
        for pos, row in rows:
            ctx = checks.Context(row, brands)
            rule, warnings = checks.rule_title(row, ctx)
            title, source, _ = _decide_title(row, ctx, None)
            conn.execute(
                "INSERT INTO products (batch_id, pos, product_id, row, rule_title, warnings, state, proposed_title,"
                " title_source, proposed_description, description_source) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, 'original')",
                (bid, pos, row["id"].strip(), json.dumps(row, ensure_ascii=False), rule,
                 json.dumps(warnings), title, source, row.get("description")))
        b = batch_or_404(conn, bid)
        out = summary(conn, b)
    out["unknown_columns"] = [c for c in parsed.columns if c not in known]
    return out


@app.post("/batches", status_code=201, dependencies=[Depends(require_key)])
async def upload(request: Request, name: str = Query("feed", max_length=120)):
    """The body is the feed file itself (curl --data-binary @products.tsv)."""
    cap = max_bytes()
    if int(request.headers.get("content-length") or 0) > cap:
        raise HTTPException(413, f"the file is larger than {cap} bytes")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > cap:
            raise HTTPException(413, f"the file is larger than {cap} bytes")
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")[:80] or "feed"
    return await run_in_threadpool(_store, clean, bytes(data))


@app.get("/batches", dependencies=[Depends(require_key)])
def list_batches(limit: int = Query(50, ge=1, le=500)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [summary(conn, dict(b)) for b in rows]


@app.get("/batches/{batch_id}", dependencies=[Depends(require_key)])
def get_batch(batch_id: int, only: Literal["all", "changed", "rejected", "approved", "pending"] = "all",
              limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0)):
    """Before and after for each product, with the reasons a model proposal was rejected."""
    with db() as conn:
        b = batch_or_404(conn, batch_id)
        rows = conn.execute("SELECT * FROM products WHERE batch_id = ? ORDER BY pos", (batch_id,)).fetchall()
        out = summary(conn, b)
    keep = {"all": lambda r: True, "changed": _changed, "approved": lambda r: r["approved_at"] is not None,
            "pending": lambda r: r["state"] == "pending",
            "rejected": lambda r: r["title_rejected"] != "[]" or r["description_rejected"] != "[]"}[only]
    selected = [r for r in rows if keep(r)]
    out["items"] = [_product_out(r) for r in selected[offset:offset + limit]]
    out["total"] = len(selected)
    return out


class ProposeRequest(BaseModel):
    limit: int = Field(20, ge=1, le=500)
    descriptions: bool = False
    llm: bool = True


@app.post("/batches/{batch_id}/propose", dependencies=[Depends(require_key)])
def propose(batch_id: int, req: ProposeRequest | None = None):
    """Proposals for the next `limit` pending products. Call again until `pending` is 0."""
    req = req or ProposeRequest()
    if not _propose_lock.acquire(blocking=False):
        raise HTTPException(409, "a propose run is already going")
    try:
        with db() as conn:
            batch_or_404(conn, batch_id)
            brands = batch_brands(conn, batch_id)
            todo = conn.execute("SELECT * FROM products WHERE batch_id = ? AND state = 'pending' ORDER BY pos LIMIT ?",
                                (batch_id, req.limit)).fetchall()
        use_llm = req.llm and bool(services.gateway_url())
        done, stopped = 0, None
        for r in todo:
            row = json.loads(r["row"])
            ctx = checks.Context(row, brands)
            llm, error = None, None
            if use_llm:
                try:
                    llm = services.propose(row, req.descriptions)
                except services.ServiceError as exc:
                    if "unreachable" in str(exc):
                        stopped = str(exc)   # the product stays pending for the next run
                        break
                    error = str(exc)
            title, t_src, t_rej = _decide_title(row, ctx, llm["title"] if llm else None)
            desc, d_src, d_rej = _decide_description(row, ctx, llm["description"] if llm and req.descriptions else None)
            with db() as conn, write(conn):
                conn.execute(
                    "UPDATE products SET state = 'proposed', proposed_title = ?, title_source = ?, proposed_description = ?,"
                    " description_source = ?, llm_title = ?, llm_description = ?, title_rejected = ?,"
                    " description_rejected = ?, llm_error = ? WHERE id = ? AND state = 'pending'",
                    (title, t_src, desc, d_src, llm["title"] if llm else None,
                     (llm["description"] or None) if llm else None, json.dumps(t_rej), json.dumps(d_rej), error, r["id"]))
            done += 1
        with db() as conn:
            out = summary(conn, batch_or_404(conn, batch_id))
        out["done"] = done
        out["model"] = use_llm
        if stopped:
            out["stopped"] = stopped
        return out
    finally:
        _propose_lock.release()


class ApproveRequest(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=5000)
    all: bool = False
    approved_by: str | None = Field(default=None, max_length=80)


def _targets(conn, batch_id: int, req: ApproveRequest) -> list[sqlite3.Row]:
    if req.all == bool(req.ids):
        raise HTTPException(422, 'send either "ids" or "all": true')
    rows = conn.execute("SELECT * FROM products WHERE batch_id = ? ORDER BY pos", (batch_id,)).fetchall()
    if req.all:
        return rows
    by_id = {r["product_id"]: r for r in rows}
    missing = [i for i in req.ids if i not in by_id]
    if missing:
        raise HTTPException(404, f"not in batch {batch_id}: {', '.join(missing[:10])}")
    return [by_id[i] for i in dict.fromkeys(req.ids)]


@app.post("/batches/{batch_id}/approve", dependencies=[Depends(require_key)])
def approve(batch_id: int, req: ApproveRequest, x_approver_key: str | None = Header(default=None)):
    """Approve the current proposal of the given products (or of every changed one). What is
    approved is copied, so a later proposal run cannot change an approved product."""
    require_approver(x_approver_key)
    approved, skipped = [], []
    with db() as conn, write(conn):
        batch_or_404(conn, batch_id)
        for r in _targets(conn, batch_id, req):
            if r["state"] != "proposed":
                skipped.append({"id": r["product_id"], "reason": "no proposal yet (run /propose)"})
            elif not _changed(r):
                skipped.append({"id": r["product_id"], "reason": "nothing to change"})
            else:
                conn.execute("UPDATE products SET approved_title = proposed_title, approved_description = proposed_description,"
                             " approved_at = ?, approved_by = ? WHERE id = ?",
                             (fmt(now_dt()), (req.approved_by or "approver").strip()[:80], r["id"]))
                approved.append(r["product_id"])
    return {"approved": approved, "skipped": skipped if not req.all else [s for s in skipped if "proposal" in s["reason"]]}


@app.post("/batches/{batch_id}/revoke", dependencies=[Depends(require_key)])
def revoke(batch_id: int, req: ApproveRequest, x_approver_key: str | None = Header(default=None)):
    require_approver(x_approver_key)
    with db() as conn, write(conn):
        batch_or_404(conn, batch_id)
        ids = [r["id"] for r in _targets(conn, batch_id, req) if r["approved_at"]]
        conn.executemany("UPDATE products SET approved_title = NULL, approved_description = NULL, approved_at = NULL,"
                         " approved_by = NULL WHERE id = ?", [(i,) for i in ids])
    return {"revoked": len(ids)}


@app.delete("/batches/{batch_id}", dependencies=[Depends(require_key)])
def delete_batch(batch_id: int):
    with db() as conn, write(conn):
        batch_or_404(conn, batch_id)
        conn.execute("DELETE FROM products WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
    return {"deleted": batch_id}


# ---------- export


MEDIA = {"csv": "text/csv; charset=utf-8", "tsv": "text/tab-separated-values; charset=utf-8"}


def _approved_changes(conn, batch_id: int) -> dict[str, dict[str, str]]:
    changes = {}
    for r in conn.execute("SELECT * FROM products WHERE batch_id = ? AND approved_at IS NOT NULL", (batch_id,)):
        ch = {"title": r["approved_title"]}
        if r["approved_description"] is not None:
            ch["description"] = r["approved_description"]
        changes[r["product_id"]] = ch
    return changes


def _verify(original: feed.Feed, text: str) -> None:
    """The export read back: same columns, same products in the same order, and every column
    except title and description unchanged. A failure here is a bug; nothing is returned."""
    again = feed.parse(text.encode("utf-8"), max_rows=10 ** 6, max_field_chars=10 ** 6)
    a, b = original.rows(), again.rows()
    if again.columns != original.columns or len(a) != len(b):
        raise HTTPException(500, "export check failed: columns or products differ")
    for (_, ra), (_, rb) in zip(a, b):
        for col in original.columns:
            if col not in feed.EDITABLE and ra[col] != rb[col]:
                raise HTTPException(500, f"export check failed: {col} of {ra.get('id')!r} changed")


def _download(body: str, filename: str, kind: str) -> Response:
    return Response(body.encode("utf-8"), media_type=MEDIA[kind],
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@app.get("/batches/{batch_id}/export.{kind}", dependencies=[Depends(require_key)])
def export_feed(batch_id: int, kind: Literal["csv", "tsv"]):
    """The uploaded file with only the approved titles and descriptions replaced. In the upload's
    own format, every other byte is as uploaded. In the other format, every value is."""
    with db() as conn:
        b = batch_or_404(conn, batch_id)
        changes = _approved_changes(conn, batch_id)
    original = feed.parse(b["raw"], max_rows=10 ** 6, max_field_chars=10 ** 6)
    body = feed.export(original, changes)
    _verify(original, body)
    if kind != b["format"]:
        again = feed.parse(body.encode("utf-8"), max_rows=10 ** 6, max_field_chars=10 ** 6)
        body = feed.write_table(original.header.values, [r.values for r in again.records if not r.blank()],
                                "\t" if kind == "tsv" else ",")
    return _download(body, f"{b['name'].rsplit('.', 1)[0]}-optimized.{kind}", kind)


@app.get("/batches/{batch_id}/supplemental.{kind}", dependencies=[Depends(require_key)])
def export_supplemental(batch_id: int, kind: Literal["csv", "tsv"]):
    """A Merchant Center supplemental feed: id, title (and description when an approved product
    changed it) for the approved products only. The main feed stays as it is."""
    with db() as conn:
        b = batch_or_404(conn, batch_id)
        rows = conn.execute("SELECT * FROM products WHERE batch_id = ? AND approved_at IS NOT NULL ORDER BY pos",
                            (batch_id,)).fetchall()
    with_desc = any(r["approved_description"] is not None and
                    r["approved_description"] != (json.loads(r["row"]).get("description") or "") for r in rows)
    header = ["id", "title"] + (["description"] if with_desc else [])
    table = []
    for r in rows:
        line = [r["product_id"], r["approved_title"]]
        if with_desc:
            line.append(r["approved_description"] if r["approved_description"] is not None
                        else json.loads(r["row"]).get("description") or "")
        table.append(line)
    body = feed.write_table(header, table, "\t" if kind == "tsv" else ",")
    return _download(body, f"{b['name'].rsplit('.', 1)[0]}-supplemental.{kind}", kind)
