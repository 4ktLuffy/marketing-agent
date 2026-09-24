"""Analytics ingest: daily per-channel metrics from CSV exports, and KPIs over a period."""
import csv
import hmac
import io
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request

app = FastAPI(title="analytics-ingest")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# A label names where rows came from (e.g. "umami"); rows are stored under it as `source`.
LABEL_PATTERN = r"^[a-z0-9_-]{1,32}$"
COUNTS = ("impressions", "clicks", "sessions", "conversions", "spend")
RATIOS = ("ctr", "cvr", "cpa")

# GA4 export header -> our column. First match wins (explorations and reports differ).
GA4_COLUMNS = {
    "date": ["date"],
    "channel": ["session default channel group", "default channel group",
                "first user default channel group", "session primary channel group"],
    "sessions": ["sessions"],
    "conversions": ["key events", "conversions"],
    "impressions": ["impressions"],
    "clicks": ["clicks", "ads clicks"],
    "spend": ["ads cost", "cost", "spend"],
    "campaign": ["session campaign", "session manual campaign name"],
}
GENERIC_COLUMNS = {c: [c] for c in ("date", "channel", "campaign", *COUNTS)}

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    date TEXT NOT NULL,
    channel TEXT NOT NULL,
    campaign TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    impressions INTEGER NOT NULL,
    clicks INTEGER NOT NULL,
    sessions INTEGER NOT NULL,
    conversions REAL NOT NULL,
    spend REAL NOT NULL,
    PRIMARY KEY (date, channel, campaign, source)
);
"""
# Databases created before campaigns existed have PRIMARY KEY (date, channel, source) and
# no campaign column. SQLite cannot change a primary key in place, so the table is rebuilt
# once, in one transaction; old rows get campaign = ''.
MIGRATE_ADD_CAMPAIGN = """
CREATE TABLE metrics_new (
    date TEXT NOT NULL,
    channel TEXT NOT NULL,
    campaign TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    impressions INTEGER NOT NULL,
    clicks INTEGER NOT NULL,
    sessions INTEGER NOT NULL,
    conversions REAL NOT NULL,
    spend REAL NOT NULL,
    PRIMARY KEY (date, channel, campaign, source)
);
INSERT INTO metrics_new (date, channel, campaign, source, impressions, clicks, sessions,
                         conversions, spend)
    SELECT date, channel, '', source, impressions, clicks, sessions, conversions, spend
    FROM metrics;
DROP TABLE metrics;
ALTER TABLE metrics_new RENAME TO metrics;
"""
_migrate_lock = threading.Lock()
_migrated: set[str] = set()


def db_path() -> str:
    return os.environ.get("DB_PATH", "/data/analytics.sqlite")


@contextmanager
def db():
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection) -> None:
    """Bring an old-schema database up to date. Runs once per DB path per process.

    Parallel requests race here: the lock covers threads, and BEGIN IMMEDIATE + re-checking
    the columns inside the transaction covers other processes, so the table is rebuilt once.
    """
    path = db_path()
    if path in _migrated:
        return
    with _migrate_lock:
        if path in _migrated:
            return
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(metrics)")}
            if "campaign" not in cols:
                for stmt in filter(str.strip, MIGRATE_ADD_CAMPAIGN.split(";")):
                    conn.execute(stmt)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        _migrated.add(path)


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- CSV parsing


def parse_date(value: str) -> str:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"date {value!r} is not YYYY-MM-DD or YYYYMMDD")


def parse_number(value: str | None, name: str, integer: bool) -> float:
    raw = (value or "").strip().replace(",", "").replace("$", "").replace("€", "").replace("£", "")
    if raw == "":
        return 0
    try:
        n = float(raw)
    except ValueError:
        raise ValueError(f"{name} {value!r} is not a number")
    if n < 0:
        raise ValueError(f"{name} is negative")
    return int(round(n)) if integer else n


def parse_csv(text: str, source: str) -> tuple[dict, list]:
    """Return ({(date, channel, campaign): metrics}, errors). Rows for the same key are summed."""
    lines = text.splitlines()
    # GA4 exports start with '#' comment lines; skip them (and blank lines) everywhere.
    numbered = [(i + 1, ln) for i, ln in enumerate(lines) if ln.strip() and not ln.lstrip().startswith("#")]
    if not numbered:
        raise HTTPException(422, "CSV is empty")
    reader = csv.reader([ln for _, ln in numbered])
    header = [h.strip().lower() for h in next(reader)]
    mapping = GA4_COLUMNS if source == "ga4" else GENERIC_COLUMNS
    col = {}
    for field, names in mapping.items():
        for n in names:
            if n in header:
                col[field] = header.index(n)
                break
    missing = [f for f in ("date", "channel") if f not in col]
    if missing:
        raise HTTPException(422, f"missing required column(s) {missing}; header was {header}")

    rows, errors = {}, []
    for (line_no, _), cells in zip(numbered[1:], reader):
        get = lambda f: cells[col[f]] if f in col and col[f] < len(cells) else ""
        if get("date").strip().lower() in ("grand total", "total", "totals"):
            continue  # summary row some exports append
        try:
            d = parse_date(get("date"))
            channel = get("channel").strip()
            if not channel:
                raise ValueError("channel is empty")
            campaign = get("campaign").strip()  # optional; missing column or cell -> ""
            values = {
                f: parse_number(get(f), f, integer=f in ("impressions", "clicks", "sessions"))
                for f in COUNTS
            }
        except ValueError as e:
            errors.append({"row": line_no, "error": str(e)})
            continue
        acc = rows.setdefault((d, channel, campaign), dict.fromkeys(COUNTS, 0))
        for f, v in values.items():
            acc[f] += v
    return rows, errors


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload", dependencies=[Depends(require_key)])
async def upload(
    request: Request,
    source: Literal["generic", "ga4"] = "generic",
    label: str | None = Query(None, pattern=LABEL_PATTERN,
                              description="store rows under this source name instead of `source`"),
):
    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"upload larger than {MAX_UPLOAD_BYTES} bytes")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "body must be UTF-8 text/csv")
    rows, errors = parse_csv(text, source)
    if errors:
        raise HTTPException(422, {"message": f"{len(errors)} bad row(s); nothing imported",
                                  "rows": errors})
    if not rows:
        raise HTTPException(422, "no data rows found")
    with db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO metrics (date, channel, campaign, source, impressions,"
            " clicks, sessions, conversions, spend) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(d, ch, camp, label or source, *(v[f] for f in COUNTS))
             for (d, ch, camp), v in rows.items()],
        )
    return {"rows_imported": len(rows)}


# ---------- KPIs


def ratio(num: float, den: float, digits: int) -> float | None:
    return round(num / den, digits) if den else None


def with_ratios(t: dict) -> dict:
    out = {
        "impressions": int(t["impressions"] or 0),
        "clicks": int(t["clicks"] or 0),
        "sessions": int(t["sessions"] or 0),
        "conversions": clean(t["conversions"] or 0),
        "spend": round(t["spend"] or 0, 2),
    }
    out["ctr"] = ratio(out["clicks"], out["impressions"], 4)
    # cvr per session when sessions exist (site analytics), else per click (ads-only data).
    out["cvr"] = ratio(out["conversions"], out["sessions"] or out["clicks"], 4)
    out["cpa"] = ratio(out["spend"], out["conversions"], 2)
    return out


def clean(n: float) -> float | int:
    n = round(n, 2)
    return int(n) if float(n).is_integer() else n


def aggregate(conn, start: str, end: str, source: str | None, group_by: str | None,
              campaign: str | None = None):
    """Sum COUNTS over [start, end]; group_by is None, "channel" or "campaign"."""
    sums = ", ".join(f"SUM({c}) AS {c}" for c in COUNTS)
    sql = f"SELECT {group_by + ', ' if group_by else ''}{sums} FROM metrics WHERE date BETWEEN ? AND ?"
    args = [start, end]
    if source:
        sql += " AND source = ?"
        args.append(source)
    if campaign:
        sql += " AND campaign = ?"
        args.append(campaign)
    if group_by == "campaign":
        sql += " AND campaign != ''"
    if group_by:
        sql += f" GROUP BY {group_by}"
    return conn.execute(sql, args).fetchall()


def delta(cur, prev):
    if cur is None or not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def iso_day(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{name} must be YYYY-MM-DD")


@app.get("/kpis")
def kpis(
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    compare: bool = True,
    source: str | None = Query(None, pattern=LABEL_PATTERN,
                               description="generic, ga4, or a label used on upload"),
    campaign: str | None = Query(None, description="exact campaign (utm_campaign) to report on"),
):
    campaign = (campaign or "").strip() or None
    end = iso_day(to, "to") if to else date.today() - timedelta(days=1)
    start = iso_day(from_, "from") if from_ else end - timedelta(days=6)
    if start > end:
        raise HTTPException(422, "from is after to")
    with db() as conn:
        s, e = start.isoformat(), end.isoformat()
        totals = with_ratios(dict(aggregate(conn, s, e, source, None, campaign)[0]))
        channels = [
            {"channel": r["channel"], **with_ratios(dict(r))}
            for r in aggregate(conn, s, e, source, "channel", campaign)
        ]
        channels.sort(key=lambda c: (-c["sessions"], -c["clicks"], c["channel"]))
        previous = deltas = None
        if compare:
            p_end = start - timedelta(days=1)
            p_start = p_end - (end - start)
            prev = with_ratios(dict(
                aggregate(conn, p_start.isoformat(), p_end.isoformat(), source, None, campaign)[0]))
            previous = {"period": {"from": p_start.isoformat(), "to": p_end.isoformat()}, **prev}
            deltas = {k: delta(totals[k], prev[k]) for k in (*COUNTS, *RATIOS)}
        by_campaign = None
        if campaign is None:
            by_campaign = []
            for r in aggregate(conn, s, e, source, "campaign"):
                t = with_ratios(dict(r))
                by_campaign.append({"campaign": r["campaign"],
                                    **{k: t[k] for k in ("sessions", "clicks", "conversions", "spend")}})
            by_campaign.sort(key=lambda c: (-c["sessions"], -c["clicks"], c["campaign"]))
    out = {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "totals": totals,
        "by_channel": channels,
        "previous": previous,
        "delta_pct": deltas,
    }
    if by_campaign is not None:  # only when not filtered to one campaign
        out["by_campaign"] = by_campaign
    return out
