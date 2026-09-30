"""Client approval links (Phase 2, agencies): let a client sign off posts without an account.

Owner side (logged in): pick posts -> a link /c/<token> and a 6-digit PIN, both shown ONCE (19
keeps only hashes; this service keeps neither) -> send them by different routes -> list and
withdraw links on /client-links.

Client side (no login, /c/...): the link asks for the PIN; with it, a phone-first page shows each
post exactly as it will go out, read-only evidence labels, and per post "Approve" / "Request
changes". The client's answers are recorded in 19 (audit + note); approving never approves the
item, the agency still does. The client page has no owner session and no owner powers: every read
and write goes through 19 with the token + PIN, CSRF per page, a per-address limit on wrong PINs,
no caching, noindex. It shows no notes, flags' wording, fact keys, keys or internal URLs.

Link + PIN prove who had both, not a legal signature.
"""
import hashlib
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import views
from .backends import BackendError
from .security import LoginLimiter, client_ip, same

TOKEN = re.compile(r"^[A-Za-z0-9_-]{40,64}$")
PIN = re.compile(r"^\d{6}$")
CLIENT_COOKIE = "cr_client"        # the client's short session (path /c/): token + PIN in memory only
CLIENT_PRE = "cr_client_pre"       # CSRF token of the PIN form, like the login form's
DAYS = (1, 3, 7, 14, 30)
LINKABLE = "draft,in_review,approved"
STATUS_WORDS = {"idea": "being written", "draft": "being written", "in_review": "waiting for your answer",
                "approved": "approved by the agency", "published": "published", "rejected": "withdrawn"}
CLIENT_WORDS = {"review": "the agency checks this by hand"}   # staff wording that reads wrong to a client
ROBOTS = {"X-Robots-Tag": "noindex, nofollow, noarchive"}
ENDED = {410: "This link has expired or was withdrawn. Ask the agency for a new one.",
         409: "This link is locked after too many wrong PINs. Ask the agency for a new one.",
         404: "This link does not exist. Check that you copied all of it."}


@dataclass
class ClientSession:
    token: str
    pin: str
    created: float
    last_seen: float
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    media: set = field(default_factory=set)   # (kind, name) this link may show


class ClientSessions:
    """Session id -> ClientSession, keyed by the sha256 of the id (like Sessions). Short-lived: the
    client re-enters the PIN after `minutes` of inactivity or 4x that in total."""

    def __init__(self, minutes: float, clock=time.time):
        self.idle, self.max_age, self.clock = minutes * 60, minutes * 240, clock
        self._by_hash: dict[str, ClientSession] = {}

    @staticmethod
    def _h(sid: str) -> str:
        return hashlib.sha256(sid.encode()).hexdigest()

    def _alive(self, s: ClientSession, now: float) -> bool:
        return now - s.created < self.max_age and now - s.last_seen < self.idle

    def create(self, token: str, pin: str) -> tuple[str, ClientSession]:
        now = self.clock()
        self._by_hash = {h: s for h, s in self._by_hash.items() if self._alive(s, now)}
        sid = secrets.token_urlsafe(32)
        s = ClientSession(token=token, pin=pin, created=now, last_seen=now)
        self._by_hash[self._h(sid)] = s
        return sid, s

    def get(self, sid: str | None, token: str | None = None) -> ClientSession | None:
        if not sid or len(sid) > 100:
            return None
        h = self._h(sid)
        s = self._by_hash.get(h)
        now = self.clock()
        if s is None or not self._alive(s, now):
            self._by_hash.pop(h, None)
            return None
        if token is not None and not same(token, s.token):
            return None
        s.last_seen = now
        return s

    def drop(self, sid: str | None) -> None:
        if sid:
            self._by_hash.pop(self._h(sid), None)


# ---------- what the client may see


def _internal(url: str, internal_urls: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(url.startswith(u) for u in internal_urls) or "." not in host or host in ("localhost",)


def client_media(url, internal_urls: list[str]) -> tuple[str | None, tuple | None]:
    """An image/video URL -> (src for the client page, (kind, name) to allow) or (None, None).
    Cards/videos/clips are served through /c/m/ for this link only; another https URL is used as is
    unless it points at an internal service."""
    u = views.http_url(url)
    if not u:
        return None, None
    src = views.media_src(u)
    if src and src.startswith("/media/"):
        _, _, kind, name = src.split("/", 3)
        return f"/c/m/{kind}/{name}", (kind, name)
    if u.startswith("https://") and not _internal(u, internal_urls):
        return u, None
    return None, None


def client_item(item: dict, internal_urls: list[str]) -> tuple[dict, set]:
    """One item as the client sees it: the text, the channel, the date, the media, evidence LABELS
    (no fact keys, no quotes, no staff wording), and this link's earlier answers."""
    image, m1 = client_media(item.get("image_url"), internal_urls)
    video, m2 = client_media(item.get("video_url"), internal_urls)
    poster, extra = None, set()
    if m2 and m2[1].endswith(".mp4"):   # 71 renders a .jpg poster next to each .mp4
        poster, extra = f"{video[:-4]}.jpg", {(m2[0], m2[1][:-4] + ".jpg")}
    chips, seen = [], set()
    if item.get("origin") == "task-bridge":
        for e in views.evidence_chips(item):
            if e["label"] not in seen:
                seen.add(e["label"])
                chips.append({"label": e["label"], "words": CLIENT_WORDS.get(e["label"], e["words"]),
                              "level": e["level"]})
    link = views.http_url(item.get("link"))
    if link and _internal(link, internal_urls):
        link = None
    answers = [{"name": str(a.get("name") or "")[:80], "approved": a.get("action") == "client_approved",
                "version": a.get("version"), "at": str(a.get("at") or "")[:16].replace("T", " ")}
               for a in item.get("client_responses") or [] if isinstance(a, dict)]
    status = str(item.get("status") or "")
    words = STATUS_WORDS.get(status, status)
    mine = [a for a in answers if a["version"] == item.get("version")]
    if status == "in_review" and mine:   # this link already answered this exact version
        words = "you approved this version" if mine[-1]["approved"] else "you asked for changes"
    ch = str(item.get("channel") or "").lower() or "other"
    return ({"id": item.get("id"), "title": str(item.get("title") or ""), "channel": ch,
             "body": str(item.get("body") or ""), "body_sha256": str(item.get("body_sha256") or ""),
             "version": item.get("version"), "status": status, "status_words": words,
             "open": status in ("idea", "draft", "in_review", "approved"),
             "date": str(item.get("scheduled_at") or "")[:10], "image": image, "video": video, "poster": poster,
             "link": link, "chips": chips, "answers": answers, "long_title": ch in ("blog", "email", "newsletter")},
            {m for m in (m1, m2) if m} | extra)


# ---------- routes


def register(app, page, current, csrf, B, need=None):
    need = need or (lambda role: csrf)   # older callers: CSRF only, no roles
    s = app.state.settings
    app.state.client_sessions = ClientSessions(s.client_session_minutes)
    # Wrong PINs and unknown links per address; the all-addresses cap is loose (many clients).
    app.state.client_limiter = LoginLimiter(s.client_max_failures, s.client_window_s, all_factor=20)
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

    def secure(request: Request) -> bool:
        return s.cookie_secure == "true" or (s.cookie_secure == "auto" and request.url.scheme == "https")

    def public_base(request: Request) -> str:
        return s.control_public_url or str(request.base_url).rstrip("/")

    # ------------------------------------------------------------ owner side (logged in)
    @app.get("/client-links", response_class=HTMLResponse)
    async def links_page(request: Request, session=Depends(current)):
        try:
            links, err = await B().client_links(), None
        except BackendError as e:
            links, err = [], f"The calendar (19) did not answer: {e.detail}"
        return page(request, "client_links.html", session, nav="more", links=links, error=err)

    async def linkable(request):
        try:
            return await B().items(status=LINKABLE), None
        except BackendError as e:
            return [], f"The calendar (19) did not answer: {e.detail}"

    @app.get("/client-links/new", response_class=HTMLResponse)
    async def new_link(request: Request, session=Depends(current)):
        picked = {int(x) for x in request.query_params.getlist("items") if x.isdigit() and len(x) < 12}
        items, err = await linkable(request)
        return page(request, "client_link_new.html", session, nav="more", items=items, picked=picked, days=DAYS,
                    values={"label": "", "days": 7}, error=err)

    @app.post("/client-links/new", response_class=HTMLResponse)
    async def make_link(request: Request, session=Depends(need("approver"))):
        form = await request.form()
        picked = [int(x) for x in form.getlist("item_id")[:50] if str(x).isdigit() and len(str(x)) < 12]
        label = " ".join(str(form.get("label", ""))[:200].split())
        days = int(form.get("days")) if str(form.get("days", "")).isdigit() else 0
        problem = ("Pick at least one post." if not picked else
                   "The name is at most 80 characters." if len(label) > 80 else
                   "Pick how long the link works." if days not in DAYS else None)
        if problem:
            items, err = await linkable(request)
            return page(request, "client_link_new.html", session, 422, nav="more", items=items, picked=set(picked),
                        days=DAYS, values={"label": label, "days": days or 7}, error=err, problem=problem)
        pin = f"{secrets.randbelow(10**6):06d}"
        try:
            made = await B().client_link_create(picked, label, days, pin)
        except BackendError as e:
            items, err = await linkable(request)
            return page(request, "client_link_new.html", session, 422 if e.status in (404, 422) else 502, nav="more",
                        items=items, picked=set(picked), days=DAYS, values={"label": label, "days": days},
                        error=err, problem=f"Not made: the calendar (19) said {e.detail}")
        token = str(made.get("token") or "")
        if not TOKEN.match(token):
            return page(request, "error.html", session, 502, error="The calendar (19) returned no link.", nav="more")
        # Shown once: neither the token nor the PIN is stored anywhere readable after this page.
        return page(request, "client_link_made.html", session, 201, nav="more", url=f"{public_base(request)}/c/{token}",
                    pin=pin, link=made)

    @app.post("/client-links/{link_id}/revoke")
    async def revoke_link(request: Request, link_id: int, session=Depends(need("approver"))):
        try:
            await B().client_link_revoke(link_id)
        except BackendError as e:
            raise HTTPException(e.status if e.status in (404,) else 502, e.detail) from None
        return RedirectResponse("/client-links", status_code=303)

    # ------------------------------------------------------------ client side (no login)
    def client_page(request: Request, name: str, status: int = 200, **ctx):
        ctx["request"] = request
        resp = templates.TemplateResponse(request, name, ctx, status_code=status, headers=ROBOTS)
        return resp

    def pre_token(request: Request) -> str:
        t = request.cookies.get(CLIENT_PRE) or ""
        return t if re.fullmatch(r"[A-Za-z0-9_-]{32,64}", t) else secrets.token_urlsafe(32)

    def pin_form(request: Request, token: str, status: int = 200, error: str | None = None):
        pre = pre_token(request)
        resp = client_page(request, "client_pin.html", status, token=token, csrf=pre, error=error)
        resp.set_cookie(CLIENT_PRE, pre, max_age=3600, httponly=True, samesite="strict", secure=secure(request),
                        path="/c/")
        return resp

    def ended(request: Request, status: int):
        resp = client_page(request, "client_ended.html", status, message=ENDED.get(status, ENDED[410]))
        resp.delete_cookie(CLIENT_COOKIE, path="/c/")
        return resp

    def limited(request: Request, ip: str):
        wait = app.state.client_limiter.retry_after(ip)
        if not wait:
            return None
        resp = client_page(request, "client_ended.html", 429,
                           message=f"Too many wrong tries from here. Try again in {max(1, wait // 60)} min.")
        resp.headers["Retry-After"] = str(wait)
        return resp

    def check_token(token: str):
        if not TOKEN.match(token):
            raise HTTPException(404, "not found")

    async def brand_name() -> str:
        try:
            return str((await B().brand_editable()).get("name") or "")[:120]
        except (BackendError, AttributeError):
            return ""

    async def items_page(request, cs: ClientSession, token: str, status=200, **ctx):
        data = await B().client_resolve(token, cs.pin)
        items, media = [], set()
        for it in data.get("items") or []:
            if isinstance(it, dict):
                view, m = client_item(it, s.internal_urls)
                items.append(view)
                media |= m
        cs.media = media
        return client_page(request, "client_review.html", status, token=token, csrf=cs.csrf, items=items,
                           label=str(data.get("label") or ""), expires=str(data.get("expires_at") or "")[:10],
                           brand=await brand_name(), **ctx)

    @app.get("/c/{token}", response_class=HTMLResponse)
    async def client_open(request: Request, token: str, answered: int = 0, d: str = ""):
        check_token(token)
        cs = app.state.client_sessions.get(request.cookies.get(CLIENT_COOKIE), token)
        if cs is None:
            return pin_form(request, token)
        try:
            return await items_page(request, cs, token, answered=answered, answered_changes=d == "changes")
        except BackendError as e:
            app.state.client_sessions.drop(request.cookies.get(CLIENT_COOKIE))
            if e.status in ENDED:
                return ended(request, e.status)
            return client_page(request, "client_ended.html", 502, message="Something went wrong on our side. Try again later.")

    @app.post("/c/{token}", response_class=HTMLResponse)
    async def client_pin(request: Request, token: str):
        check_token(token)
        form = await request.form()
        pre = request.cookies.get(CLIENT_PRE) or ""
        if not pre or not same(str(form.get("csrf", "")), pre):
            raise HTTPException(403, "missing or wrong CSRF token (reload the page)")
        ip = client_ip(request, os.getenv("TRUSTED_PROXIES", ""))
        if (resp := limited(request, ip)) is not None:
            return resp
        pin = re.sub(r"\s", "", str(form.get("pin", ""))[:20])
        if not PIN.match(pin):
            return pin_form(request, token, 422, "The PIN is 6 digits.")
        try:
            await B().client_resolve(token, pin)
        except BackendError as e:
            if e.status in (403, 404):
                app.state.client_limiter.failed(ip)
            if e.status == 403:
                m = re.search(r"(\d+) tries left", e.detail or "")
                n = int(m.group(1)) if m else 0
                left = f" {n} more wrong {'try' if n == 1 else 'tries'} and the link locks." if m else ""
                return pin_form(request, token, 401, f"That PIN is not right.{left}")
            if e.status in ENDED:
                return ended(request, e.status)
            return client_page(request, "client_ended.html", 502, message="Something went wrong on our side. Try again later.")
        app.state.client_sessions.drop(request.cookies.get(CLIENT_COOKIE))
        sid, _ = app.state.client_sessions.create(token, pin)
        resp = RedirectResponse(f"/c/{token}", status_code=303, headers=ROBOTS)
        resp.set_cookie(CLIENT_COOKIE, sid, max_age=int(s.client_session_minutes * 240), httponly=True,
                        samesite="strict", secure=secure(request), path="/c/")
        resp.delete_cookie(CLIENT_PRE, path="/c/")
        return resp

    @app.post("/c/{token}/respond", response_class=HTMLResponse)
    async def client_respond(request: Request, token: str):
        check_token(token)
        cs = app.state.client_sessions.get(request.cookies.get(CLIENT_COOKIE), token)
        if cs is None:
            return RedirectResponse(f"/c/{token}", status_code=303, headers=ROBOTS)
        form = await request.form()
        if not same(str(form.get("csrf", "")), cs.csrf):
            raise HTTPException(403, "missing or wrong CSRF token (reload the page)")
        item_id = str(form.get("item_id", ""))
        sha = str(form.get("body_sha256", ""))
        decision = str(form.get("decision", ""))
        name = " ".join(str(form.get("name", ""))[:200].split())
        comment = str(form.get("comment", ""))[:4000].strip()
        if not item_id.isdigit() or len(item_id) > 12 or not views.SHA256.match(sha) or decision not in ("approve", "changes"):
            raise HTTPException(422, "bad form")
        problem = ("Write your name, so the agency knows who answered." if not name else
                   "Your name is at most 80 characters." if len(name) > 80 else
                   "The comment is at most 2000 characters." if len(comment) > 2000 else
                   "Say what should change." if decision == "changes" and not comment else None)
        try:
            if problem:
                return await items_page(request, cs, token, 422, problem={"id": int(item_id), "text": problem},
                                        values={"name": name, "comment": comment, "id": int(item_id)})
            await B().client_respond({"token": token, "pin": cs.pin, "item_id": int(item_id), "body_sha256": sha,
                                      "decision": decision, "name": name, "comment": comment})
        except BackendError as e:
            if e.status == 409 and "text changed" in (e.detail or ""):
                return await items_page(request, cs, token, 409, problem={
                    "id": int(item_id), "text": "The text changed since you opened the link. Read the new text below."})
            if e.status == 409 and "no longer open" in (e.detail or ""):
                return await items_page(request, cs, token, 409, problem={
                    "id": int(item_id), "text": "This post is no longer open for answers."})
            app.state.client_sessions.drop(request.cookies.get(CLIENT_COOKIE))
            if e.status in ENDED or e.status == 403:
                return ended(request, e.status if e.status in ENDED else 410)
            return client_page(request, "client_ended.html", 502, message="Something went wrong on our side. Try again later.")
        return RedirectResponse(f"/c/{token}?answered={item_id}&d={decision}#post-{item_id}", status_code=303,
                                headers=ROBOTS)

    @app.get("/c/m/{kind}/{name}")
    async def client_media_route(request: Request, kind: str, name: str):
        cs = app.state.client_sessions.get(request.cookies.get(CLIENT_COOKIE))
        if cs is None or (kind, name) not in cs.media:
            raise HTTPException(404, "not found")
        try:
            r = await B().media(kind, name, request.headers.get("range"))
        except BackendError:
            raise HTTPException(502, "media unreachable") from None
        if r.status_code not in (200, 206):
            await r.aclose()
            raise HTTPException(404, "not found")
        keep = {k: r.headers[k] for k in ("content-type", "content-length", "content-range", "accept-ranges")
                if k in r.headers}
        keep.update({"Cache-Control": "private, no-store", **ROBOTS})

        async def body():
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                await r.aclose()
        return StreamingResponse(body(), status_code=r.status_code, headers=keep)
