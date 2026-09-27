"""Listmonk bridge: puts the week's newsletter into Listmonk as a draft campaign.

Listmonk facts this file relies on (read from the Listmonk docs and source, September 2026):
- Auth (v4+ API users): `Authorization: token <api_user>:<token>`, or HTTP basic auth with the
  same pair. https://listmonk.app/docs/apis/apis/
- Responses are `{"data": ...}`; errors are 4xx/5xx with `{"message": "..."}`.
- `POST /api/campaigns` {name, subject, lists:[int], type, content_type, body, altbody?,
  from_email?, send_at?, messenger?, template_id?, tags?} -> `{"data": {"id", "status":
  "draft", ...}}`. New campaigns are always drafts. content_type is one of richtext, html,
  markdown, plain, visual. A `send_at` in the past is rejected (validateCampaignFields).
  The body is compiled as a Go template, so an unknown `{{ ... }}` is a 400.
- `PUT /api/campaigns/{id}/status` {"status": "scheduled"} only works from draft/paused and
  only when the campaign has a send_at (core.UpdateCampaignStatus).
- `POST /api/campaigns/{id}/test` needs the full campaign fields plus `subscribers`
  (e-mails that already exist as Listmonk subscribers), otherwise it is a 400.
- `GET /api/lists?per_page=all` -> `{"data": {"results": [{"id", "name", "subscriber_count",
  ...}], "total", ...}}`.
- Markdown bodies go through goldmark with raw HTML allowed, so the hidden preheader block
  works in both html and markdown campaigns.
See README "Listmonk API reference used".
"""
import base64
import hmac
import html
import logging
import os
import re
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="listmonk-bridge")
log = logging.getLogger("listmonk-bridge")

TIMEOUT_S = 30.0
MAX_SUBJECT = 200
MAX_BODY_BYTES = 200 * 1024
MAX_PREHEADER = 300
MAX_NAME = 200
SCRIPT_RE = re.compile(r"<\s*/?\s*script\b", re.IGNORECASE)
# 18-email-renderer writes this placeholder; Listmonk's template tag is {{ UnsubscribeURL }}.
UNSUB_RE = re.compile(r"\{\{\s*unsubscribe_url\s*\}\}", re.IGNORECASE)
BODY_TAG_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


# ---------- config


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if default:  # safe-on flags: only an explicit false turns them off
        return value not in ("false", "0", "no", "off")
    return value in ("true", "1", "yes", "on")  # safe-off flags: only an explicit true


def dry_run() -> bool:
    """Anything but an explicit false value keeps DRY_RUN on (safe default)."""
    return _flag("DRY_RUN", True)


def allow_schedule() -> bool:
    """Off unless explicitly true: a person presses send in Listmonk."""
    return _flag("ALLOW_SCHEDULE", False)


def listmonk_url() -> str:
    url = (os.environ.get("LISTMONK_URL") or "").strip().rstrip("/")
    return url[: -len("/api")] if url.endswith("/api") else url


def public_url() -> str:
    return (os.environ.get("LISTMONK_PUBLIC_URL") or "").strip().rstrip("/") or listmonk_url()


def lm_user() -> str:
    return (os.environ.get("LISTMONK_USER") or "").strip()


def lm_token() -> str:
    return (os.environ.get("LISTMONK_TOKEN") or "").strip()


def auth_mode() -> str:
    return (os.environ.get("LISTMONK_AUTH_MODE") or "token").strip().lower()


def allowed_list_ids() -> tuple[list[int], str | None]:
    raw = (os.environ.get("LISTMONK_LIST_IDS") or "").strip()
    if not raw:
        return [], None
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or int(part) <= 0:
            return [], f"LISTMONK_LIST_IDS must be comma-separated list ids, got {part!r}"
        out.append(int(part))
    return out, None


def test_emails() -> list[str]:
    raw = os.environ.get("TEST_EMAILS") or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def template_id() -> tuple[int | None, str | None]:
    raw = (os.environ.get("LISTMONK_TEMPLATE_ID") or "").strip()
    if not raw:
        return None, None
    if not raw.isdigit():
        return None, f"LISTMONK_TEMPLATE_ID must be a number, got {raw!r}"
    return int(raw), None


def config_errors() -> list[str]:
    errors = []
    if not os.environ.get("INTERNAL_API_KEY"):
        errors.append("INTERNAL_API_KEY is not set: every endpoint but /health answers 503")
    if auth_mode() not in ("token", "basic"):
        errors.append("LISTMONK_AUTH_MODE must be 'token' (Listmonk v4+) or 'basic'")
    _, e = allowed_list_ids()
    if e:
        errors.append(e)
    _, e = template_id()
    if e:
        errors.append(e)
    if not listmonk_url():
        errors.append("LISTMONK_URL is not set: only DRY_RUN works")
    elif not listmonk_url().startswith(("http://", "https://")):
        errors.append("LISTMONK_URL must start with http:// or https://")
    if not lm_user() or not lm_token():
        errors.append("LISTMONK_USER / LISTMONK_TOKEN are not set: only DRY_RUN works")
    return errors


def configured() -> bool:
    return not config_errors()


# ---------- auth


def require_key(x_api_key: str | None = Header(default=None)):
    expected = os.environ.get("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(503, "INTERNAL_API_KEY is not configured on this service")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or wrong X-API-Key")


# ---------- secret hygiene


def _secrets() -> list[str]:
    token, user = lm_token(), lm_user()
    if not token:
        return []
    pair = f"{user}:{token}"
    return [pair, base64.b64encode(pair.encode()).decode(), token]


def scrub(value):
    """Remove the Listmonk token from anything that could reach a response or a log line."""
    secrets = _secrets()
    if not secrets:
        return value
    if isinstance(value, str):
        for s in secrets:
            value = value.replace(s, "***")
        return value
    if isinstance(value, dict):
        return {scrub(k): scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"detail": scrub(exc.detail)}, status_code=exc.status_code,
                        headers=exc.headers)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    log.error("unexpected error on %s: %s", request.url.path, type(exc).__name__)
    return JSONResponse({"detail": "internal error"}, status_code=500)


# ---------- Listmonk client


class ListmonkError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def http_client() -> httpx.Client:
    headers = {"Accept": "application/json"}
    auth = None
    if auth_mode() == "basic":
        auth = httpx.BasicAuth(lm_user(), lm_token())
    else:
        headers["Authorization"] = f"token {lm_user()}:{lm_token()}"
    return httpx.Client(timeout=TIMEOUT_S, headers=headers, auth=auth)


def listmonk_message(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return (r.text or "").strip()[:300] or f"HTTP {r.status_code}"
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or body)[:300]
    return str(body)[:300]


def call_listmonk(method: str, path: str, body: dict | None = None, params: dict | None = None):
    url = listmonk_url() + "/api" + path
    try:
        with http_client() as client:
            r = client.request(method, url, json=body, params=params)
    except httpx.TimeoutException:
        raise ListmonkError(None, f"Listmonk did not answer within {TIMEOUT_S:.0f} s") from None
    except httpx.HTTPError as e:
        raise ListmonkError(None, f"cannot reach Listmonk at {listmonk_url()}: {type(e).__name__}") from None
    if r.status_code in (401, 403):
        raise ListmonkError(r.status_code, "Listmonk rejected LISTMONK_USER/LISTMONK_TOKEN "
                            f"(check LISTMONK_AUTH_MODE and the API user's permissions): "
                            f"{listmonk_message(r)}")
    if r.status_code >= 400:
        raise ListmonkError(r.status_code, listmonk_message(r))
    try:
        payload = r.json()
    except ValueError:
        raise ListmonkError(r.status_code, "Listmonk answered with non-JSON") from None
    return payload.get("data") if isinstance(payload, dict) else payload


def bad_gateway(e: ListmonkError, **extra) -> HTTPException:
    return HTTPException(502, {"message": "Listmonk error", "listmonk_status": e.status,
                               "listmonk_error": e.message, **extra})


def require_live_config():
    errors = config_errors()
    if errors:
        raise HTTPException(503, "; ".join(errors) + ". See GET /health, or keep DRY_RUN=true.")


def campaign_url(campaign_id) -> str | None:
    base = public_url()
    return f"{base}/admin/campaigns/{campaign_id}" if base and campaign_id is not None else None


# ---------- content


def fix_placeholders(text: str) -> str:
    return UNSUB_RE.sub("{{ UnsubscribeURL }}", text)


def preheader_block(preheader: str) -> str:
    # Same hidden-span technique as 18-email-renderer: inboxes show it next to the subject.
    return ('<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;'
            'font-size:1px;line-height:1px;color:#ffffff;opacity:0;">'
            f"{html.escape(preheader, quote=False)}</div>")


def with_preheader(body: str, preheader: str | None) -> str:
    if not preheader or html.escape(preheader, quote=False) in body or preheader in body:
        return body  # already there (18 renders it), do not show it twice
    block = preheader_block(preheader)
    m = BODY_TAG_RE.search(body)
    if m:
        return body[: m.end()] + block + body[m.end():]
    return block + "\n\n" + body


class CampaignRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subject: str
    preheader: str | None = None
    body_html: str | None = None
    body_markdown: str | None = None
    body_text: str | None = None  # optional plain-text part (18 returns it as "text")
    list_ids: list[int] | None = None
    send_at: datetime | None = None
    name: str | None = None

    @field_validator("subject")
    @classmethod
    def _subject(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("subject is empty")
        if len(v) > MAX_SUBJECT:
            raise ValueError(f"subject is {len(v)} characters; the limit is {MAX_SUBJECT}")
        return v

    @field_validator("preheader")
    @classmethod
    def _preheader(cls, v: str | None) -> str | None:
        v = " ".join(v.split()) if v else None
        if v and len(v) > MAX_PREHEADER:
            raise ValueError(f"preheader is {len(v)} characters; the limit is {MAX_PREHEADER}")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        v = " ".join(v.split()) if v else None
        if v and len(v) > MAX_NAME:
            raise ValueError(f"name is {len(v)} characters; the limit is {MAX_NAME}")
        return v

    @model_validator(mode="after")
    def _body(self):
        has_html = bool(self.body_html and self.body_html.strip())
        has_md = bool(self.body_markdown and self.body_markdown.strip())
        if has_html == has_md:
            raise ValueError("send exactly one of body_html or body_markdown")
        for label, text in (("body", self.body_html or self.body_markdown or ""),
                            ("body_text", self.body_text or "")):
            size = len(text.encode("utf-8"))
            if size > MAX_BODY_BYTES:
                raise ValueError(f"{label} is {size} bytes; the limit is {MAX_BODY_BYTES} (200 KB)")
            if SCRIPT_RE.search(text):
                raise ValueError(f"{label} contains a <script> tag; e-mail must not carry scripts")
        if self.list_ids is not None and any(i <= 0 for i in self.list_ids):
            raise ValueError("list_ids must be positive Listmonk list ids")
        return self


def resolve_lists(requested: list[int] | None) -> list[int]:
    allowed, err = allowed_list_ids()
    if err:
        raise HTTPException(503, err)
    ids = list(dict.fromkeys(requested)) if requested else allowed
    if not ids:
        raise HTTPException(422, "no list_ids given and LISTMONK_LIST_IDS is not set")
    if allowed:
        bad = [i for i in ids if i not in allowed]
        if bad:
            raise HTTPException(422, f"list ids {bad} are not in LISTMONK_LIST_IDS {allowed}")
    return ids


def resolve_send_at(send_at: datetime | None) -> datetime | None:
    if send_at is None:
        return None
    if send_at.tzinfo is None:
        send_at = send_at.replace(tzinfo=timezone.utc)  # documented: no offset = UTC
    if send_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "send_at is in the past; Listmonk only schedules future times")
    return send_at


def campaign_body(req: CampaignRequest, lists: list[int], send_at: datetime | None) -> dict:
    if req.body_html and req.body_html.strip():
        content_type, body = "html", req.body_html
    else:
        content_type, body = "markdown", req.body_markdown or ""
    body = with_preheader(fix_placeholders(body), req.preheader)
    out = {
        "name": req.name or f"Newsletter {datetime.now(timezone.utc):%Y-%m-%d}: {req.subject}"[:MAX_NAME],
        "subject": req.subject,
        "lists": lists,
        "type": "regular",
        "content_type": content_type,
        "body": body,
        "messenger": "email",
        "tags": ["marketing-agent"],
    }
    if req.body_text and req.body_text.strip():
        out["altbody"] = fix_placeholders(req.body_text)
    from_email = (os.environ.get("LISTMONK_FROM_EMAIL") or "").strip()
    if from_email:
        out["from_email"] = from_email
    tpl, _ = template_id()
    if tpl:
        out["template_id"] = tpl
    if send_at is not None:
        out["send_at"] = send_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


# ---------- endpoints


@app.get("/health")
def health():
    allowed, _ = allowed_list_ids()
    errors = config_errors()
    return {
        "status": "ok" if not errors else "degraded",
        "configured": not errors,
        "dry_run": dry_run(),
        "allow_schedule": allow_schedule(),
        "listmonk_url": listmonk_url() or None,
        "public_url": public_url() or None,
        "auth_mode": auth_mode(),
        "credentials_set": bool(lm_user() and lm_token()),
        "list_ids": allowed,
        "test_emails": len(test_emails()),
        "config_errors": errors,
    }


@app.get("/lists", dependencies=[Depends(require_key)])
def lists():
    require_live_config()
    try:
        data = call_listmonk("GET", "/lists", params={"per_page": "all"})
    except ListmonkError as e:
        raise bad_gateway(e)
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise bad_gateway(ListmonkError(200, "unexpected /api/lists response (no results)"))
    allowed, _ = allowed_list_ids()
    return [
        {"id": row.get("id"), "name": row.get("name"),
         "subscriber_count": row.get("subscriber_count"),
         "allowed": (not allowed) or row.get("id") in allowed}
        for row in results if isinstance(row, dict)
    ]


@app.post("/campaigns", dependencies=[Depends(require_key)])
def create_campaign(req: CampaignRequest):
    lists_ = resolve_lists(req.list_ids)
    send_at = resolve_send_at(req.send_at)
    schedule = bool(send_at and allow_schedule())
    body = campaign_body(req, lists_, send_at if schedule else None)
    ignored = bool(send_at and not schedule)
    note = ("send_at ignored: ALLOW_SCHEDULE is off, so the campaign stays a draft and a "
            "person sends it in Listmonk") if ignored else None

    if dry_run():
        return {"status": "dry_run", "campaign_id": None, "url": None,
                "scheduled": False, "would_schedule": schedule, "send_at_ignored": ignored,
                "note": note, "would_send": body}

    require_live_config()
    try:
        created = call_listmonk("POST", "/campaigns", body)
    except ListmonkError as e:
        raise bad_gateway(e)
    campaign_id = created.get("id") if isinstance(created, dict) else None
    if campaign_id is None:
        raise bad_gateway(ListmonkError(200, "Listmonk accepted the campaign but returned no id"))
    log.info("draft campaign %s created in Listmonk (lists %s, %s)",
             campaign_id, lists_, body["content_type"])

    if schedule:
        try:
            call_listmonk("PUT", f"/campaigns/{campaign_id}/status", {"status": "scheduled"})
        except ListmonkError as e:
            # The draft exists; say so, so nobody creates a second one.
            raise bad_gateway(e, message="draft created but scheduling failed",
                              campaign_id=campaign_id, url=campaign_url(campaign_id))
        log.info("campaign %s scheduled for %s", campaign_id, body["send_at"])

    return {"status": "created", "campaign_id": campaign_id, "url": campaign_url(campaign_id),
            "scheduled": schedule, "send_at": body.get("send_at"), "send_at_ignored": ignored,
            "note": note, "list_ids": lists_, "content_type": body["content_type"]}


TEST_FIELDS = ("name", "subject", "from_email", "body", "altbody", "content_type",
               "messenger", "template_id", "headers", "type", "tags")


@app.post("/campaigns/{campaign_id}/test", dependencies=[Depends(require_key)])
def test_campaign(campaign_id: int):
    recipients = test_emails()
    if not recipients:
        raise HTTPException(503, "TEST_EMAILS is not set (comma-separated e-mails that exist "
                                 "as Listmonk subscribers)")
    if dry_run():
        return {"status": "dry_run", "campaign_id": campaign_id, "recipients": len(recipients)}
    require_live_config()
    try:
        camp = call_listmonk("GET", f"/campaigns/{campaign_id}")
    except ListmonkError as e:
        if e.status in (400, 404):
            raise HTTPException(404, f"Listmonk has no campaign {campaign_id}: {e.message}")
        raise bad_gateway(e)
    if not isinstance(camp, dict):
        raise bad_gateway(ListmonkError(200, "unexpected campaign response"))
    body = {k: camp.get(k) for k in TEST_FIELDS if camp.get(k) is not None}
    body["lists"] = [l.get("id") for l in camp.get("lists") or []
                     if isinstance(l, dict) and l.get("id") is not None]
    body["subscribers"] = recipients
    try:
        call_listmonk("POST", f"/campaigns/{campaign_id}/test", body)
    except ListmonkError as e:
        raise bad_gateway(e)
    log.info("test of campaign %s sent to %d address(es)", campaign_id, len(recipients))
    return {"status": "sent", "campaign_id": campaign_id, "recipients": len(recipients),
            "url": campaign_url(campaign_id)}
