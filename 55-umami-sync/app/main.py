"""Umami sync: per-day visits and conversions by utm_source x utm_campaign, uploaded to 20.

Umami API facts used here were checked against the Umami source at tag v3.4.0
(https://github.com/umami-software/umami/tree/v3.4.0); see README "Umami API used".
"""
import csv
import hmac
import io
import json
import logging
import os
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title="umami-sync")
log = logging.getLogger("umami-sync")

MAX_DAYS = 31
LABEL = "umami"  # rows land in 20 under source=umami, apart from manual CSV uploads
CLOUD_URL = "https://api.umami.is/v1"
BREAKDOWN_FIELDS = ["utmSource", "utmCampaign"]
BREAKDOWN_ROW_CAP = 500  # getBreakdown ends with `limit 500`
EVENT_PAGE_SIZE = 500
MAX_EVENT_PAGES = 10  # at most 5000 conversion events read per day
MAX_429_WAIT = 15.0  # Umami Cloud: 50 calls per 15 s per API key


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


def secrets() -> list[str]:
    names = ("UMAMI_PASSWORD", "UMAMI_API_KEY", "INTERNAL_API_KEY")
    return [v for v in (os.environ.get(n, "") for n in names) if v]


def scrub(text: str, extra: tuple[str, ...] = ()) -> str:
    """Remove any credential value from a message before it is logged or returned."""
    for s in (*secrets(), *extra):
        if s:
            text = text.replace(s, "***")
    return text


class UmamiError(Exception):
    pass


class Umami:
    """Minimal Umami v3 client. Every call pauses first, so a sync stays under rate limits."""

    def __init__(self, client: httpx.Client, base: str, website_id: str, pause: float):
        self.client = client
        self.base = base.rstrip("/")
        # Umami Cloud serves the API at api.umami.is/v1/<route>; self-hosted at <url>/api/<route>.
        host = urlsplit(self.base).hostname or ""
        self.prefix = self.base if host == "api.umami.is" else self.base + "/api"
        self.website_id = website_id
        self.pause = pause
        self.headers: dict[str, str] = {}
        self.calls = 0
        self.legacy_breakdown = False
        self.token = ""

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        for attempt in (1, 2):
            if self.calls and self.pause:
                time.sleep(self.pause)
            self.calls += 1
            try:
                r = self.client.request(method, url, headers=self.headers, **kw)
            except httpx.HTTPError as e:
                raise UmamiError(f"Umami unreachable ({type(e).__name__})")
            if r.status_code == 429 and attempt == 1:
                try:
                    wait = float(r.headers.get("retry-after", MAX_429_WAIT))
                except ValueError:
                    wait = MAX_429_WAIT
                time.sleep(min(max(wait, 0), MAX_429_WAIT))
                continue
            return r
        return r

    def login_with_key(self, key: str) -> None:
        # Cloud docs: "Authorization: Bearer <api-key>"; x-umami-api-key is the documented
        # alternative header (both may be sent if they hold the same key). Self-hosted v3.4+
        # API keys (prefix umami_) are read from the Bearer header too.
        self.headers = {"Authorization": f"Bearer {key}", "x-umami-api-key": key}

    def login(self, username: str, password: str) -> None:
        r = self._request("POST", f"{self.prefix}/auth/login",
                          json={"username": username, "password": password})
        if r.status_code != 200:
            raise UmamiError(f"Umami login failed (HTTP {r.status_code})")
        body = self._json(r, "login")
        if not isinstance(body, dict):
            raise UmamiError("login: unexpected response shape")
        if body.get("requiresTwoFactor"):
            raise UmamiError("Umami login needs two-factor auth; use an API key (UMAMI_API_KEY) instead")
        token = body.get("token")
        if not token:
            raise UmamiError("Umami login returned no token")
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    def _json(self, r: httpx.Response, what: str):
        if r.status_code != 200:
            raise UmamiError(f"{what}: Umami returned HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise UmamiError(f"{what}: Umami returned non-JSON")

    def breakdown(self, start: datetime, end: datetime) -> list[dict]:
        """Visits per (utmSource, utmCampaign) for pageviews in [start, end]."""
        site = f"{self.prefix}/websites/{self.website_id}"
        if not self.legacy_breakdown:
            r = self._request("GET", f"{site}/breakdown", params={
                "startAt": ms(start), "endAt": ms(end), "fields": json.dumps(BREAKDOWN_FIELDS)})
            if r.status_code != 404:
                rows = self._json(r, "breakdown")
                return check_list(rows, "breakdown")
            self.legacy_breakdown = True  # Umami 3.0-3.3: only POST /api/reports/breakdown
        r = self._request("POST", f"{self.prefix}/reports/breakdown", json={
            "websiteId": self.website_id, "type": "breakdown", "filters": {},
            "parameters": {"startDate": start.isoformat(timespec="milliseconds"),
                           "endDate": end.isoformat(timespec="milliseconds"),
                           "fields": BREAKDOWN_FIELDS}})
        return check_list(self._json(r, "breakdown (reports)"), "breakdown (reports)")

    def events(self, start: datetime, end: datetime, name: str) -> tuple[list[dict], bool]:
        """Custom events named `name` in [start, end]. Returns (events, complete)."""
        out: list[dict] = []
        for page in range(1, MAX_EVENT_PAGES + 1):
            r = self._request("GET", f"{self.prefix}/websites/{self.website_id}/events", params={
                "startAt": ms(start), "endAt": ms(end), "event": name,
                "pageSize": EVENT_PAGE_SIZE, "page": page})
            body = self._json(r, "events")
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise UmamiError("events: unexpected response shape")
            out.extend(body["data"])
            count = int(body.get("count") or 0)
            if not body["data"] or page * EVENT_PAGE_SIZE >= count:
                return out, True
        return out, False


def check_list(rows, what: str) -> list[dict]:
    if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
        raise UmamiError(f"{what}: unexpected response shape")
    return rows


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def day_window(day: date, tz) -> tuple[datetime, datetime]:
    """[00:00:00.000, 23:59:59.999] of `day` in `tz` (Umami filters with BETWEEN, inclusive)."""
    start = datetime.combine(day, dtime(0), tz)
    end = datetime.combine(day + timedelta(days=1), dtime(0), tz) - timedelta(milliseconds=1)
    return start, end


def number(v) -> float:
    # ClickHouse-backed Umami can return counts as strings
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def key_of(source, campaign) -> tuple[str, str]:
    channel = (source or "").strip().lower() or "direct"
    return channel, (campaign or "").strip()


def utm_from_query(query: str | None) -> tuple[str, str]:
    # Umami's /api/send stores utm_* from the event URL's query (URLSearchParams.get: first value)
    q = parse_qs(query or "", keep_blank_values=True)
    return q.get("utm_source", [""])[0], q.get("utm_campaign", [""])[0]


def read_day(umami: Umami, day: date, tz, event: str) -> tuple[dict, list[str]]:
    """{(channel, campaign): {"sessions", "conversions"}} for one day, plus warnings."""
    start, end = day_window(day, tz)
    rows: dict[tuple[str, str], dict] = {}
    warnings = []
    breakdown = umami.breakdown(start, end)
    if len(breakdown) >= BREAKDOWN_ROW_CAP:
        warnings.append(f"breakdown hit Umami's {BREAKDOWN_ROW_CAP}-row cap; smallest source/campaign pairs are missing")
    for b in breakdown:
        acc = rows.setdefault(key_of(b.get("utmSource"), b.get("utmCampaign")),
                              {"sessions": 0, "conversions": 0})
        acc["sessions"] += int(number(b.get("visits")))
    if event:
        events, complete = umami.events(start, end, event)
        if not complete:
            warnings.append(f"more than {MAX_EVENT_PAGES * EVENT_PAGE_SIZE} '{event}' events; only those were counted")
        for e in events:
            if e.get("eventName") not in (None, event):
                continue
            acc = rows.setdefault(key_of(*utm_from_query(e.get("urlQuery"))),
                                  {"sessions": 0, "conversions": 0})
            acc["conversions"] += 1
    return rows, warnings


def to_csv(rows: dict, with_conversions: bool) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    header = ["date", "channel", "campaign", "sessions"] + (["conversions"] if with_conversions else [])
    w.writerow(header)
    for (d, channel, campaign), v in sorted(rows.items()):
        line = [d, channel, campaign, v["sessions"]]
        if with_conversions:
            line.append(v["conversions"])
        w.writerow(line)
    return buf.getvalue()


def get_timezone(name: str):
    if name.upper() in ("", "UTC", "ETC/UTC"):
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(503, f"UMAMI_TIMEZONE {name!r} is not a known time zone")


class SyncRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: date = Field(alias="from")
    to: date


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/sync", dependencies=[Depends(require_key)])
def sync(req: SyncRequest):
    if req.to < req.from_:
        raise HTTPException(422, "from is after to")
    n_days = (req.to - req.from_).days + 1
    if n_days > MAX_DAYS:
        raise HTTPException(422, f"at most {MAX_DAYS} days per sync; got {n_days}")

    api_key = env("UMAMI_API_KEY")
    username, password = env("UMAMI_USERNAME"), os.environ.get("UMAMI_PASSWORD", "")
    base = env("UMAMI_URL") or (CLOUD_URL if api_key else "")
    missing = [n for n, v in (("UMAMI_URL", base), ("UMAMI_WEBSITE_ID", env("UMAMI_WEBSITE_ID"))) if not v]
    if not api_key and not (username and password):
        missing.append("UMAMI_API_KEY or UMAMI_USERNAME+UMAMI_PASSWORD")
    if missing:
        raise HTTPException(503, f"not configured: {', '.join(missing)}")
    tz = get_timezone(env("UMAMI_TIMEZONE", "UTC"))
    event = env("CONVERSION_EVENT")
    analytics = env("ANALYTICS_URL", "http://analytics-ingest:8000").rstrip("/")
    pause = number(env("UMAMI_PAUSE_SECONDS", "0.35"))

    errors: list[dict] = []
    all_rows: dict[tuple[str, str, str], dict] = {}
    days_ok = 0
    with httpx.Client(timeout=20) as client:
        umami = Umami(client, base, env("UMAMI_WEBSITE_ID"), pause)
        try:
            if api_key:
                umami.login_with_key(api_key)
            else:
                umami.login(username, password)
        except UmamiError as e:
            errors.append({"day": None, "error": scrub(str(e))})
            log.warning("sync %s..%s: %s", req.from_, req.to, errors[-1]["error"])
            return {"rows": 0, "days": 0, "errors": errors}

        for i in range(n_days):
            day = req.from_ + timedelta(days=i)
            try:
                rows, warnings = read_day(umami, day, tz, event)
            except (UmamiError, ValueError) as e:  # ValueError: bad JSON numbers etc.
                errors.append({"day": day.isoformat(), "error": scrub(str(e), (umami.token,))})
                continue
            days_ok += 1
            errors += [{"day": day.isoformat(), "error": scrub(w)} for w in warnings]
            for (channel, campaign), v in rows.items():
                all_rows[(day.isoformat(), channel, campaign)] = v

        uploaded = 0
        if all_rows:
            try:
                r = client.post(f"{analytics}/upload", params={"source": "generic", "label": LABEL},
                                content=to_csv(all_rows, bool(event)).encode(),
                                headers={"X-API-Key": os.environ.get("INTERNAL_API_KEY", ""),
                                         "content-type": "text/csv"})
                if r.status_code == 200:
                    uploaded = int(r.json().get("rows_imported", len(all_rows)))
                else:
                    errors.append({"day": None, "error": scrub(
                        f"analytics-ingest upload returned HTTP {r.status_code}: {r.text[:300]}",
                        (umami.token,))})
            except (httpx.HTTPError, ValueError) as e:
                errors.append({"day": None, "error": scrub(f"analytics-ingest unreachable ({type(e).__name__})")})
    for e in errors:
        log.warning("sync %s: %s", e["day"] or "-", e["error"])
    log.info("sync %s..%s: %d day(s) read, %d row(s) uploaded, %d Umami call(s)",
             req.from_, req.to, days_ok, uploaded, umami.calls)
    return {"rows": uploaded, "days": days_ok, "errors": errors}
