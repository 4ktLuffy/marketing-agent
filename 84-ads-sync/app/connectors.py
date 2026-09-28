"""Read-only connectors: Meta Marketing API Insights and Google Ads API (REST searchStream).

Nothing here creates, changes or pauses anything: the only calls are GET /insights (Meta), the
OAuth token refresh and POST googleAds:searchStream (a read query; POST only carries the GAQL).
Every error becomes an AdsError whose message is safe to show: credentials are scrubbed.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from urllib.parse import urlsplit

import httpx

GRAPH_HOST = "graph.facebook.com"
DEFAULT_GRAPH_VERSION = "v26.0"      # Graph/Marketing API, released 2026-07-29
DEFAULT_GOOGLE_ADS_VERSION = "v25"   # Google Ads API, released 2026-07-22
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ADS_HOST = "googleads.googleapis.com"
META_PAGE_LIMIT = 500
META_MAX_PAGES = 50
RETRY_MAX_WAIT = 5.0          # a longer wait is returned to the caller as Retry-After
DEFAULT_RETRY_AFTER = 300
# Meta throttling: 4 app, 17 user, 613 custom, 80000 ads insights BUC, 80004 ads management BUC.
META_RATE_CODES = {4, 17, 32, 613, 80000, 80004}
META_LEVELS = {
    "campaign": ("campaign_id", "campaign_name"),
    "adset": ("adset_id", "adset_name"),
    "ad": ("ad_id", "ad_name"),
}
TIMEOUT = 60.0


class AdsError(Exception):
    def __init__(self, platform: str, status: int, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.platform, self.status, self.message, self.retry_after = platform, status, message, retry_after


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- Meta


def meta_act(account: str) -> str:
    a = account.strip()
    a = a[4:] if a.startswith("act_") else a
    if not a.isdigit():
        raise ValueError(f"Meta ad account id must be numeric (act_123 or 123), got {account[:40]!r}")
    return "act_" + a


def parse_buc(headers: httpx.Headers) -> dict:
    """x-business-use-case-usage: {"<business id>": [{"type","call_count","total_cputime",
    "total_time","estimated_time_to_regain_access" (minutes)}]} -> the highest usage seen."""
    out = {"max_pct": 0, "regain_minutes": 0, "types": []}
    for name in ("x-business-use-case-usage", "x-ad-account-usage", "x-app-usage"):
        raw = headers.get(name)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        entries = []
        if isinstance(data, dict) and name == "x-business-use-case-usage":
            for v in data.values():
                entries += v if isinstance(v, list) else [v]
        elif isinstance(data, dict):
            entries = [data]
        for e in entries:
            if not isinstance(e, dict):
                continue
            pct = max(num(e.get(k)) for k in ("call_count", "total_cputime", "total_time", "acc_id_util_pct",
                                                "call_volume", "cpu_time", "app_id_util_pct"))
            out["max_pct"] = max(out["max_pct"], pct)
            out["regain_minutes"] = max(out["regain_minutes"], num(e.get("estimated_time_to_regain_access")))
            if e.get("type") and e["type"] not in out["types"]:
                out["types"].append(e["type"])
    return out


def _meta_error(r: httpx.Response, scrub) -> AdsError:
    try:
        err = r.json().get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    code, sub = err.get("code"), err.get("error_subcode")
    msg = scrub(str(err.get("message") or ""))[:300]
    fb = f" (Meta code {code}{'/' + str(sub) if sub else ''}: {msg})" if code else f" (HTTP {r.status_code})"
    if code == 190 or r.status_code == 401:
        return AdsError("meta", 502, "Meta rejected META_ACCESS_TOKEN: it is expired, revoked or invalid" + fb +
                        ". Create a new System User token with ads_read (Business Settings → System users → "
                        "Generate new token), put it in .env and restart ads-sync.")
    if code in META_RATE_CODES or r.status_code == 429:
        buc = parse_buc(r.headers)
        ra = int(buc["regain_minutes"] * 60) if buc["regain_minutes"] else None
        if ra is None:
            try:
                ra = int(float(r.headers.get("retry-after", "")))
            except ValueError:
                ra = DEFAULT_RETRY_AFTER
        return AdsError("meta", 429, f"Meta rate limit reached{fb}. Retry after {ra} s.", retry_after=ra)
    if code in (10, 294) or (isinstance(code, int) and 200 <= code <= 299) or r.status_code == 403:
        return AdsError("meta", 502, "Meta refused access to the ad account" + fb + ". The token needs the ads_read "
                        "permission and the system user must be assigned to the ad account (see README: Meta).")
    if code == 100:
        return AdsError("meta", 502, "Meta rejected the request" + fb + ". Check META_AD_ACCOUNT_IDS.")
    return AdsError("meta", 502, "Meta Marketing API error" + fb)


class Meta:
    def __init__(self, token: str, version: str, conv_actions: list[str], rev_actions: list[str], scrub):
        self.token, self.version = token, version
        self.conv_actions, self.rev_actions = conv_actions, rev_actions
        self.scrub = scrub
        self.calls = 0
        self.usage = {"max_pct": 0, "regain_minutes": 0, "types": []}

    def _get(self, client: httpx.Client, url: str, params: dict | None) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"}  # never in the URL, so never in paging links
        for attempt in (1, 2):
            try:
                r = client.get(url, params=params, headers=headers)
            except httpx.HTTPError as e:
                raise AdsError("meta", 502, f"Meta Marketing API unreachable ({type(e).__name__})")
            self.calls += 1
            u = parse_buc(r.headers)
            self.usage["max_pct"] = max(self.usage["max_pct"], u["max_pct"])
            self.usage["regain_minutes"] = max(self.usage["regain_minutes"], u["regain_minutes"])
            self.usage["types"] = sorted(set(self.usage["types"]) | set(u["types"]))
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    raise AdsError("meta", 502, "Meta returned a response that is not JSON")
                if isinstance(body, dict) and "error" in body:
                    raise _meta_error(r, self.scrub)
                return body
            err = _meta_error(r, self.scrub)
            if err.status == 429 and attempt == 1 and (err.retry_after or 0) <= RETRY_MAX_WAIT:
                time.sleep(err.retry_after or 0)
                continue
            raise err
        raise AdsError("meta", 502, "unreachable")  # pragma: no cover

    def _sum_actions(self, items, wanted: list[str]) -> float:
        if not isinstance(items, list):
            return 0.0
        return sum(num(a.get("value")) for a in items if isinstance(a, dict) and a.get("action_type") in wanted)

    def insights(self, client: httpx.Client, account: str, since: date, until: date, level: str,
                 warnings: list[str]) -> list[dict]:
        """Daily rows (time_increment=1) for one ad account at one level, every page."""
        id_f, name_f = META_LEVELS[level]
        fields = ["date_start", "date_stop", "account_currency", "campaign_id", "campaign_name",
                  "spend", "impressions", "clicks", "actions", "action_values"]
        for f in (id_f, name_f):
            if f not in fields:
                fields.append(f)
        act = meta_act(account)
        url: str | None = f"https://{GRAPH_HOST}/{self.version}/{act}/insights"
        params: dict | None = {
            "level": level, "fields": ",".join(fields), "time_increment": 1, "limit": META_PAGE_LIMIT,
            "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
        }
        rows, pages = [], 0
        while url:
            body = self._get(client, url, params)
            params = None  # paging.next carries every parameter
            for d in body.get("data") or []:
                if not isinstance(d, dict) or not d.get(id_f) or not d.get("date_start"):
                    continue
                rows.append({
                    "platform": "meta", "account_id": act, "level": level,
                    "entity_id": str(d[id_f]), "entity_name": str(d.get(name_f) or ""),
                    "campaign_id": str(d.get("campaign_id") or d[id_f]),
                    "campaign_name": str(d.get("campaign_name") or d.get(name_f) or ""),
                    "date": str(d["date_start"])[:10], "currency": str(d.get("account_currency") or ""),
                    "spend": num(d.get("spend")), "impressions": int(num(d.get("impressions"))),
                    "clicks": int(num(d.get("clicks"))),
                    "conversions": self._sum_actions(d.get("actions"), self.conv_actions),
                    "revenue": self._sum_actions(d.get("action_values"), self.rev_actions),
                    "utm_campaign": None,
                })
            pages += 1
            nxt = (body.get("paging") or {}).get("next")
            if nxt and urlsplit(nxt).scheme == "https" and urlsplit(nxt).hostname == GRAPH_HOST:
                url = nxt
            else:
                if nxt:
                    warnings.append(f"meta {act}: paging link not on {GRAPH_HOST}; stopped")
                url = None
            if url and pages >= META_MAX_PAGES:
                warnings.append(f"meta {act} {level}: stopped after {META_MAX_PAGES} pages ({len(rows)} rows)")
                break
        return rows


# ---------------------------------------------------------------- Google Ads

GAQL = ("SELECT customer.currency_code, campaign.id, campaign.name, campaign.final_url_suffix, "
        "campaign.tracking_url_template, segments.date, metrics.cost_micros, metrics.impressions, "
        "metrics.clicks, metrics.conversions, metrics.conversions_value FROM campaign "
        "WHERE segments.date BETWEEN '{since}' AND '{until}'")

UTM_RE = re.compile(r"(?:^|[?&{};])utm_campaign=([^&#\s{}]+)", re.I)


def utm_campaign(*texts: str | None) -> str | None:
    for t in texts:
        m = UTM_RE.search(t or "")
        if m and not m.group(1).startswith("{"):
            return m.group(1)
    return None


def _google_details(body) -> tuple[list[str], str | None]:
    """Error codes (e.g. DEVELOPER_TOKEN_NOT_APPROVED) and a retry delay from a Google error body."""
    text = json.dumps(body) if not isinstance(body, str) else body
    codes = re.findall(r'"[a-zA-Z]+Error"\s*:\s*"([A-Z_]+)"', text)
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', text)
    return codes, m.group(1) if m else None


def _google_error(r: httpx.Response, scrub) -> AdsError:
    try:
        body = r.json()
    except ValueError:
        body = r.text
    if isinstance(body, list) and body:
        body = body[0]
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    codes, delay = _google_details(body)
    msg = scrub(str(err.get("message") or ""))[:300]
    g = f" (HTTP {r.status_code} {err.get('status') or ''}{': ' + ', '.join(codes) if codes else ''}{' — ' + msg if msg else ''})"
    if r.status_code == 429 or "RESOURCE_EXHAUSTED" in codes or err.get("status") == "RESOURCE_EXHAUSTED":
        try:
            ra = int(float(delay)) if delay else int(float(r.headers.get("retry-after", "")))
        except ValueError:
            ra = DEFAULT_RETRY_AFTER
        return AdsError("google", 429, f"Google Ads API quota reached{g}. Retry after {ra} s.", retry_after=ra)
    if "DEVELOPER_TOKEN_NOT_APPROVED" in codes or "DEVELOPER_TOKEN_PROHIBITED" in codes:
        return AdsError("google", 502, "Google Ads rejected GOOGLE_ADS_DEVELOPER_TOKEN" + g + ". A new token only works "
                        "with test accounts: apply for Basic access in the manager account's API Center.")
    if "DEVELOPER_TOKEN_INVALID" in codes or "DEVELOPER_TOKEN_PARAMETER_MISSING" in codes:
        return AdsError("google", 502, "GOOGLE_ADS_DEVELOPER_TOKEN is invalid" + g + ". Copy it again from API Center.")
    if "USER_PERMISSION_DENIED" in codes or "CUSTOMER_NOT_ENABLED" in codes or r.status_code == 403:
        return AdsError("google", 502, "Google Ads refused access to the account" + g + ". The OAuth user must have "
                        "access to GOOGLE_ADS_CUSTOMER_IDS; if access comes through a manager account, set "
                        "GOOGLE_ADS_LOGIN_CUSTOMER_ID to the manager's id (digits only).")
    if r.status_code == 401:
        return AdsError("google", 502, "Google Ads rejected the access token" + g + ". Generate a new "
                        "GOOGLE_ADS_REFRESH_TOKEN (README: Google Ads).")
    return AdsError("google", 502, "Google Ads API error" + g)


class Google:
    def __init__(self, developer_token: str, client_id: str, client_secret: str, refresh_token: str,
                 login_customer_id: str, version: str, scrub):
        self.dev, self.cid, self.secret, self.refresh = developer_token, client_id, client_secret, refresh_token
        self.login = re.sub(r"\D", "", login_customer_id or "")
        self.version = version
        self.scrub = scrub
        self.calls = 0

    _cache: dict = {}  # refresh-token hash -> (access_token, expires_at); per process

    def access_token(self, client: httpx.Client, force: bool = False) -> str:
        key = hash((self.cid, self.refresh))
        hit = Google._cache.get(key)
        if hit and not force and hit[1] - 60 > time.time():
            return hit[0]
        try:
            r = client.post(GOOGLE_TOKEN_URL, data={"grant_type": "refresh_token", "client_id": self.cid,
                                                     "client_secret": self.secret, "refresh_token": self.refresh})
        except httpx.HTTPError as e:
            raise AdsError("google", 502, f"Google OAuth unreachable ({type(e).__name__})")
        self.calls += 1
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or not body.get("access_token"):
            err = str(body.get("error") or f"HTTP {r.status_code}")
            desc = self.scrub(str(body.get("error_description") or ""))[:200]
            if err == "invalid_grant":
                raise AdsError("google", 502, f"Google rejected GOOGLE_ADS_REFRESH_TOKEN (invalid_grant: {desc or 'expired or revoked'}). "
                               "It was revoked, the password changed, or the OAuth consent screen is in Testing (refresh "
                               "tokens then expire after 7 days). Publish the consent screen, generate a new refresh token "
                               "(README: Google Ads), put it in .env and restart ads-sync.")
            if err in ("invalid_client", "unauthorized_client"):
                raise AdsError("google", 502, f"Google rejected GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET ({err}). "
                               "Use the Desktop or Web OAuth client that created the refresh token.")
            raise AdsError("google", 502, f"Google OAuth token refresh failed ({err}{': ' + desc if desc else ''})")
        Google._cache[key] = (body["access_token"], time.time() + int(num(body.get("expires_in")) or 3600))
        return body["access_token"]

    def _post(self, client: httpx.Client, customer: str, query: str) -> list:
        url = f"https://{GOOGLE_ADS_HOST}/{self.version}/customers/{customer}/googleAds:searchStream"
        for attempt in (1, 2, 3):
            headers = {"Authorization": f"Bearer {self.access_token(client, force=attempt > 1)}",
                       "developer-token": self.dev, "content-type": "application/json"}
            if self.login:
                headers["login-customer-id"] = self.login
            try:
                r = client.post(url, json={"query": query}, headers=headers)
            except httpx.HTTPError as e:
                raise AdsError("google", 502, f"Google Ads API unreachable ({type(e).__name__})")
            self.calls += 1
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError:
                    raise AdsError("google", 502, "Google Ads returned a response that is not JSON")
                if isinstance(body, dict):  # searchStream answers a JSON array of batches
                    body = [body]
                for b in body:
                    if isinstance(b, dict) and b.get("error"):
                        raise _google_error(httpx.Response(int(b["error"].get("code") or 500), json=b), self.scrub)
                return body
            err = _google_error(r, self.scrub)
            if r.status_code == 401 and attempt == 1:
                continue  # a revoked or expired access token: one retry with a fresh one
            if err.status == 429 and attempt < 3 and (err.retry_after or 0) <= RETRY_MAX_WAIT:
                time.sleep(err.retry_after or 0)
                continue
            raise err
        raise AdsError("google", 502, "unreachable")  # pragma: no cover

    def campaigns(self, client: httpx.Client, customer: str, since: date, until: date) -> list[dict]:
        cust = re.sub(r"\D", "", customer)
        if not cust:
            raise AdsError("google", 503, f"GOOGLE_ADS_CUSTOMER_IDS has an entry without digits: {customer[:20]!r}")
        batches = self._post(client, cust, GAQL.format(since=since.isoformat(), until=until.isoformat()))
        rows = []
        for b in batches:
            for res in (b.get("results") or []) if isinstance(b, dict) else []:
                c, m, s = res.get("campaign") or {}, res.get("metrics") or {}, res.get("segments") or {}
                if not c.get("id") or not s.get("date"):
                    continue
                rows.append({
                    "platform": "google", "account_id": cust, "level": "campaign",
                    "entity_id": str(c["id"]), "entity_name": str(c.get("name") or ""),
                    "campaign_id": str(c["id"]), "campaign_name": str(c.get("name") or ""),
                    "date": str(s["date"])[:10],
                    "currency": str((res.get("customer") or {}).get("currencyCode") or ""),
                    # int64 values arrive as JSON strings; cost is in micros of the account currency.
                    "spend": num(m.get("costMicros")) / 1_000_000,
                    "impressions": int(num(m.get("impressions"))), "clicks": int(num(m.get("clicks"))),
                    "conversions": num(m.get("conversions")), "revenue": num(m.get("conversionsValue")),
                    "utm_campaign": utm_campaign(c.get("finalUrlSuffix"), c.get("trackingUrlTemplate")),
                })
        return rows
