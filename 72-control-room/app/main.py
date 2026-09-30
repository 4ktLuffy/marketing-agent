"""72 control room: a mobile-first review queue, calendar and dashboards for the marketing agent.

A backend-for-frontend: the browser gets a session cookie and a CSRF token, never a key or an
internal URL. Decisions are posted to n8n (workflow 72-control-room/n8n), which runs the same
decision code as the approval form (38).
"""
import os
import asyncio
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import activity, brand_setup, client_links, config, facts_page, feeds, onboarding, positioning, tasks, users, views
from . import results as results_page
from .backends import BackendError, Backends, gather_soft
from .pending import PendingDecisions
from .security import ACTOR, LoginLimiter, Session, Sessions, client_ip, same

HERE = Path(__file__).parent
SESSION_COOKIE = "cr_session"
LOGIN_COOKIE = "cr_login"
DECISIONS = {"approve", "edit", "reject_rewrite", "reject_drop", "back_to_draft"}
ISO = re.compile(r"^\d{4}-\d\d-\d\d(?:[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:?\d\d)?)?$")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; "
       "media-src 'self' https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'; object-src 'none'; manifest-src 'self'; font-src 'self' data:")


class NotLoggedIn(Exception):
    pass


class Forbidden(Exception):
    """Logged in, but the role can't do this (need(role) in create_app)."""

    def __init__(self, session: Session):
        self.session = session


def create_app() -> FastAPI:
    from . import logredact
    logredact.install()   # /c/<token> never reaches the access log
    settings = config.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(follow_redirects=False) as client:
            app.state.backends = Backends(settings, client)
            app.state.pending = PendingDecisions(app.state.backends, settings.reviewer, settings.undo_seconds)
            task = asyncio.create_task(app.state.pending.run())
            try:
                yield
            finally:
                task.cancel()
                try:
                    await app.state.pending.flush_due(everything=True)   # not undone: send them
                except Exception:  # noqa: BLE001
                    pass

    app = FastAPI(title="control-room", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.sessions = Sessions(settings.session_hours, settings.idle_minutes)
    app.state.limiter = LoginLimiter(settings.login_max_failures, settings.login_window_s)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals["STATUS_COLORS"] = views.STATUS_COLORS
    templates.env.globals["can"] = lambda role: False   # page() passes the session's own
    templates.env.globals["me"] = None

    def secure(request: Request) -> bool:
        return settings.cookie_secure == "true" or (settings.cookie_secure == "auto" and request.url.scheme == "https")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        ACTOR.set(None)   # set again by `current` for a logged-in request
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.url.path.startswith(("/static/", "/media/")):
            resp.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith("/c/"):   # client approval pages: never indexed
            resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        if secure(request):
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp

    @app.exception_handler(NotLoggedIn)
    async def not_logged_in(request: Request, exc: NotLoggedIn):
        if request.headers.get("HX-Request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        if request.method == "GET" and not request.url.path.startswith(("/calendar/events", "/media/", "/activity/data")):
            nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/login?next={quote(nxt)}", status_code=303)
        return JSONResponse({"detail": "log in first"}, status_code=401)

    @app.exception_handler(Forbidden)
    async def forbidden(request: Request, exc: Forbidden):
        role = exc.session.role or "none"
        return page(request, "error.html", exc.session, 403, nav="more",
                    error=f"Your role ({role}) can't do this; ask the owner.")

    async def current(request: Request) -> Session:
        """The session, with the person's role and name as the users file says NOW (a removed user
        is logged out, a changed role applies at once). Async on purpose: ACTOR must be set in the
        request's own context, not in a worker thread's copy."""
        sid = request.cookies.get(SESSION_COOKIE)
        s = app.state.sessions.get(sid)
        if s is None:
            raise NotLoggedIn()
        if s.user:
            try:
                u = users.lookup(settings, s.user)
            except users.UsersFileError:
                u = None
            if u is None:
                app.state.sessions.drop(sid)
                raise NotLoggedIn()
            s.display, s.role = u.display, u.role
        ACTOR.set(s.display or None)
        return s

    async def check_csrf(request: Request, session: Session) -> None:
        token = request.headers.get("X-CSRF-Token")
        if token is None and request.headers.get("content-type", "").startswith(
                ("application/x-www-form-urlencoded", "multipart/form-data")):
            token = (await request.form()).get("csrf")
        if not token or not same(str(token), session.csrf):
            raise HTTPException(403, "missing or wrong CSRF token (reload the page)")

    async def csrf(request: Request, session: Session = Depends(current)) -> Session:
        await check_csrf(request, session)
        return session

    needs: dict = {}

    def need(role: str):
        """Dependency: logged in with at least `role` (owner > approver > writer); a POST also needs
        the CSRF token (checked first). Anything less gets the 403 page."""
        if role not in users.RANK:
            raise ValueError(role)
        if role not in needs:
            async def dep(request: Request, session: Session = Depends(current)) -> Session:
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    await check_csrf(request, session)
                if users.RANK.get(session.role, 0) < users.RANK[role]:
                    raise Forbidden(session)
                return session
            dep.role = role
            needs[role] = dep
        return needs[role]

    app.state.need = need

    def page(request: Request, name: str, session: Session | None, status: int = 200, **ctx):
        me = {"name": session.user or settings.user, "display": session.display or settings.reviewer,
              "role": session.role} if session else None
        rank = users.RANK.get(me["role"], 0) if me else 0
        ctx.update(request=request, csrf=session.csrf if session else None,
                   user=me["name"] if me else settings.user, me=me,
                   can=lambda role: rank >= users.RANK.get(role, 99),
                   undo_seconds=settings.undo_seconds)
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def frag(request: Request, name: str, session: Session, status: int = 200, **ctx):
        return page(request, name, session, status, **ctx)

    B = lambda: app.state.backends  # noqa: E731
    P = lambda: app.state.pending  # noqa: E731

    # ------------------------------------------------------------------ health (no login)
    @app.get("/health")
    def health():
        return {"status": "ok"}

    # ------------------------------------------------------------------ login / logout
    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        token = request.cookies.get(LOGIN_COOKIE) or ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", token):
            token = secrets.token_urlsafe(32)
        resp = page(request, "login.html", None, login_token=token, next=_safe_next(next),
                    configured=users.state(settings) in ("env", "file"))
        resp.set_cookie(LOGIN_COOKIE, token, max_age=3600, httponly=True, samesite="strict", secure=secure(request))
        return resp

    @app.post("/login", response_class=HTMLResponse)
    def login(request: Request, user: str = Form(""), password: str = Form(""), csrf: str = Form(""),
              next: str = Form("/")):
        ip = client_ip(request, os.getenv("TRUSTED_PROXIES", ""))
        pre = request.cookies.get(LOGIN_COOKIE) or ""
        if not pre or not same(csrf, pre):
            raise HTTPException(403, "missing or wrong CSRF token (reload the login page)")
        st = users.state(settings)
        ctx = dict(login_token=pre, next=_safe_next(next), configured=st in ("env", "file"))
        if st == "bad":
            return page(request, "login.html", None, 503, error="The users file can't be read; the owner can check "
                        "it with python -m app.users list.", **ctx)
        if st == "none":
            msg = ("CONTROL_PASSWORD is not set on the server." if users.read_users(settings.users_file) is None
                   else "Nobody is in the users file yet; add someone with python -m app.users add.")
            return page(request, "login.html", None, 503, error=msg, **ctx)
        wait = app.state.limiter.retry_after(ip)
        if wait:
            resp = page(request, "login.html", None, 429,
                        error=f"Too many failed logins. Try again in {max(1, wait // 60)} min.", **ctx)
            resp.headers["Retry-After"] = str(wait)
            return resp
        try:
            who = users.authenticate(settings, user[:200], password[:1000])
        except users.UsersFileError:
            return page(request, "login.html", None, 503, error="The users file can't be read; the owner can "
                        "check it with python -m app.users list.", **ctx)
        if who is None:
            app.state.limiter.failed(ip)
            return page(request, "login.html", None, 401, error="Wrong user or password.", **ctx)
        app.state.limiter.succeeded(ip)
        app.state.sessions.drop(request.cookies.get(SESSION_COOKIE))
        sid, _ = app.state.sessions.create(who)
        resp = RedirectResponse(_safe_next(next), status_code=303)
        resp.set_cookie(SESSION_COOKIE, sid, max_age=int(settings.session_hours * 3600), httponly=True,
                        samesite="strict", secure=secure(request), path="/")
        resp.delete_cookie(LOGIN_COOKIE)
        return resp

    @app.post("/logout")
    def logout(request: Request, session: Session = Depends(csrf)):
        app.state.sessions.drop(request.cookies.get(SESSION_COOKIE))
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    # ------------------------------------------------------------------ 2 review queue
    async def queue_cards(tab: str = "all"):
        err = None
        try:
            items = await B().items(status="in_review")
        except BackendError as e:
            items, err = [], e.detail
        hidden = P().hidden_ids()
        items = [i for i in items if i.get("id") not in hidden]
        items.sort(key=lambda i: (i.get("scheduled_at") is None, i.get("scheduled_at") or "", i.get("id") or 0))
        pillars = await B().pillar_titles() if any(views.pillar_id(i) for i in items) else {}
        cards = [views.card(i, pillars) for i in items]
        flagged = [c for c in cards if any(f["level"] in ("danger", "warn") for f in c["flags"])]
        return (flagged if tab == "flagged" else cards), len(cards), len(flagged), err

    @app.get("/", response_class=HTMLResponse)
    async def queue(request: Request, tab: str = "all", toast: str = "", session: Session = Depends(current)):
        tab = "flagged" if tab == "flagged" else "all"
        (cards, total, n_flagged, err), blockers = await asyncio.gather(
            queue_cards(tab), tasks.compact_blockers(B(), settings))
        pending = P().items.get(toast) if toast else None
        return page(request, "queue.html", session, cards=cards, total=total, n_flagged=n_flagged, tab=tab,
                    error=err, toast=pending, results=list(P().results)[:5], nav="queue", blockers=blockers)

    @app.post("/decide")
    async def decide(request: Request, id: int = Form(...), decision: str = Form(...), text: str = Form(""),
                     reason: str = Form(""), publish_at: str = Form(""), seen: str = Form(""),
                     session: Session = Depends(need("approver"))):
        if decision not in DECISIONS or id < 1:
            raise HTTPException(422, "unknown decision")
        if len(text) > 60000 or len(reason) > 500 or len(publish_at) > 40:
            raise HTTPException(422, "text, reason or publish time too long")
        publish_at = publish_at.strip()
        if publish_at and not ISO.match(publish_at):
            raise HTTPException(422, "publish time: YYYY-MM-DD HH:MM (UTC)")
        # The body_sha256 of the text on the card (empty from an older page or calendar).
        if seen and not views.SHA256.match(seen):
            raise HTTPException(422, "seen: the text's sha256, 64 hex characters")
        d = P().add(id, decision, text=text if decision == "edit" else None, reason=reason.strip() or None,
                    publish_at=publish_at or None, seen_sha256=seen or None,
                    reviewer=session.display or settings.reviewer)
        if request.headers.get("HX-Request"):
            return frag(request, "_decided.html", session, d=d)
        return RedirectResponse(f"/?toast={d.token}", status_code=303)

    @app.post("/undo/{token}")
    async def undo(request: Request, token: str, session: Session = Depends(need("approver"))):
        d = P().undo(token)
        htmx = request.headers.get("HX-Request")
        if not htmx:
            return RedirectResponse("/", status_code=303)
        if d is None or d is False:
            msg = "Too late: it was already sent." if d is False else "Nothing to undo."
            return frag(request, "_undo_failed.html", session, 409 if d is False else 404, token=token, message=msg)
        try:
            item = await B().item(d.item_id)
            pillars = await B().pillar_titles() if views.pillar_id(item) else {}
            c = views.card(item, pillars)
        except BackendError:
            c = None
        return frag(request, "_undone.html", session, c=c, token=token, d=d)

    @app.get("/results", response_class=HTMLResponse)
    def results(request: Request, session: Session = Depends(current)):
        return frag(request, "_results.html", session, results=list(P().results)[:5],
                    sending=sum(1 for d in P().items.values() if d.state == "sending"))

    # ------------------------------------------------------------------ 3 edit & approve, 5 detail
    @app.get("/items/{item_id}/edit", response_class=HTMLResponse)
    async def edit(request: Request, item_id: int, session: Session = Depends(current)):
        try:
            item = await B().item(item_id)
        except BackendError as e:
            return page(request, "error.html", session, e.status if e.status == 404 else 502, error=str(e), nav="queue")
        limits = await B().limits()
        rule = limits.get(str(item.get("channel") or "").lower()) or {}
        return page(request, "edit.html", session, c=views.card(item), rule=rule, nav="queue",
                    fold=views.fold_at(item.get("channel")), pending=item_id in P().hidden_ids())

    @app.get("/items/{item_id}", response_class=HTMLResponse)
    async def detail(request: Request, item_id: int, session: Session = Depends(current)):
        try:
            item = await B().item(item_id)
        except BackendError as e:
            return page(request, "error.html", session, e.status if e.status == 404 else 502, error=str(e), nav="calendar")
        attempts, pillars = await gather_soft(B().attempts(item_id), B().pillar_titles())
        return page(request, "item.html", session, c=views.card(item, pillars if isinstance(pillars, dict) else {}),
                    timeline=views.timeline(item),
                    attempts=None if isinstance(attempts, BackendError) else attempts,
                    pending=item_id in P().hidden_ids(), nav="calendar")

    @app.get("/items/{item_id}/download")
    async def download(request: Request, item_id: int, session: Session = Depends(current)):
        """The item's body as one HTML file (21 /document): a client report (85) to forward."""
        if not settings.report_url:
            return page(request, "error.html", session, 404, error="report-builder (21) is not installed", nav="calendar")
        try:
            item = await B().item(item_id)
            title = str(item.get("title") or f"Item {item_id}")
            doc = await B().document(title, str(item.get("body") or ""))
        except BackendError as e:
            return page(request, "error.html", session, e.status if e.status == 404 else 502, error=str(e), nav="calendar")
        name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or f"item-{item_id}"
        return Response(doc, media_type="text/html; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.html"',
                                 "Cache-Control": "private, no-store"})

    # ------------------------------------------------------------------ 4 calendar
    @app.get("/calendar", response_class=HTMLResponse)
    def calendar(request: Request, session: Session = Depends(current)):
        return page(request, "calendar.html", session, nav="calendar")

    @app.get("/calendar/events")
    async def calendar_events(start: str = "", end: str = "", session: Session = Depends(current)):
        for v in (start, end):
            if v and (len(v) > 40 or not ISO.match(v)):
                raise HTTPException(422, "start/end: ISO dates")
        try:
            items = await B().items(**{"from": start, "to": end})
        except BackendError as e:
            return JSONResponse({"detail": str(e)}, status_code=502)
        return [views.event(i) for i in items if i.get("scheduled_at")]

    @app.post("/calendar/reschedule")
    async def reschedule(request: Request, session: Session = Depends(need("approver"))):
        try:
            body = await request.json()
            item_id, start = int(body["id"]), str(body["start"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "body: {id, start}") from None
        if len(start) > 40 or not ISO.match(start):
            raise HTTPException(422, "start: an ISO date-time")
        try:
            item = await B().item(item_id)
            if item.get("status") not in views.EDITABLE_TIME:
                raise HTTPException(409, f"a {item.get('status')} item can't be moved")
            return views.event(await B().reschedule(item_id, start))
        except BackendError as e:
            raise HTTPException(e.status if e.status in (404, 409, 422) else 502, e.detail) from None

    # ------------------------------------------------------------------ 6 performance
    @app.get("/performance", response_class=HTMLResponse)
    async def performance(request: Request, days: int = 30, session: Session = Depends(current)):
        days = days if days in (7, 30, 90) else 30
        calls = [B().insights(days), B().hooks(days)] + ([B().ads_summary(days)] if settings.ads_url else [])
        ins, hooks, *ads = await gather_soft(*calls)
        ads = ads[0] if ads else None
        return page(request, "performance.html", session, days=days, nav="performance",
                    insights=None if isinstance(ins, BackendError) else ins,
                    hooks=None if isinstance(hooks, BackendError) else hooks,
                    ads=None if isinstance(ads, BackendError) else ads, ads_installed=bool(settings.ads_url),
                    errors=[str(x) for x in (ins, hooks, ads) if isinstance(x, BackendError)])

    # ------------------------------------------------------------------ 7 content engine
    async def pillar_rows():
        pillars = await B().pillars()
        healths = await gather_soft(*(B().pillar_health(p["id"]) for p in pillars))
        return [{"p": p, "h": None if isinstance(h, BackendError) else h} for p, h in zip(pillars, healths)]

    @app.get("/engine", response_class=HTMLResponse)
    async def engine(request: Request, session: Session = Depends(current)):
        if not settings.engine_url:
            return page(request, "engine.html", session, rows=[], error=None, not_installed=True, nav="more")
        try:
            rows, err = await pillar_rows(), None
        except BackendError as e:
            rows, err = [], str(e)
        return page(request, "engine.html", session, rows=rows, error=err, nav="more")

    @app.post("/engine/{pid}/resume")
    async def resume(request: Request, pid: int, session: Session = Depends(need("approver"))):
        try:
            await B().resume(pid)
        except BackendError as e:
            raise HTTPException(e.status if e.status in (404, 409) else 502, e.detail) from None
        return RedirectResponse("/engine", status_code=303)

    # ------------------------------------------------------------------ 8 campaigns
    @app.get("/campaigns", response_class=HTMLResponse)
    async def campaigns(request: Request, session: Session = Depends(current)):
        try:
            camps = await B().campaigns("planned,active,paused")
            cards = await gather_soft(*(B().scorecard(c["id"]) for c in camps))
            unmeasured = await gather_soft(B().unmeasured())
            err = None
        except BackendError as e:
            camps, cards, unmeasured, err = [], [], [[]], str(e)
        rows = [{"c": c, "s": None if isinstance(s, BackendError) else s} for c, s in zip(camps, cards)]
        um = unmeasured[0] if unmeasured and not isinstance(unmeasured[0], BackendError) else []
        return page(request, "campaigns.html", session, rows=rows, unmeasured=um, error=err, nav="more")

    # ------------------------------------------------------------------ 9 chat, 10 health
    @app.get("/chat", response_class=HTMLResponse)
    def chat(request: Request, session: Session = Depends(current)):
        return page(request, "chat.html", session, nav="more",
                    chat_url=f"{settings.n8n_public_url}/webhook/mkt-marketing-chat/chat",
                    form_url=f"{settings.n8n_public_url}/form/mkt-content-approval")

    @app.get("/status", response_class=HTMLResponse)
    async def status(request: Request, session: Session = Depends(current)):
        try:
            st, err = await B().status(), None
        except BackendError as e:
            st, err = None, str(e)
        return page(request, "status.html", session, st=st, error=err, nav="more")

    @app.get("/more", response_class=HTMLResponse)
    def more(request: Request, session: Session = Depends(current)):
        return page(request, "more.html", session, nav="more")

    # ------------------------------------------------------------------ 11 brand setup (05, 03)
    brand_setup.register(app, page, current, csrf, B, need=need)

    # ------------------------------------------------------------------ 12 positioning map (78, read-only)
    positioning.register(app, page, current)

    # ------------------------------------------------------------------ 13 product feed (87, read-only)
    feeds.register(app, page, current)

    # ------------------------------------------------------------------ 14 activity (03 gateways, 19; read-only)
    activity.register(app, page, current)

    # ------------------------------------------------------------------ 15 facts (05 v2), 16 tasks + blockers (88)
    onboarding.register(app, page, current, csrf, B, need=need)   # /facts/setup before /facts/{key}/...
    facts_page.register(app, page, current, csrf, B, need=need)
    results_page.register(app, page, current)       # /tasks/results before /tasks/{task_id}
    tasks.register(app, page, current, csrf, B, need=need)

    # ------------------------------------------------------------------ 17 client approval links (19; /c/ is public)
    client_links.register(app, page, current, csrf, B, need=need)

    # ------------------------------------------------------------------ media (17 cards, 71 videos)
    @app.get("/media/{kind}/{name}")
    async def media(request: Request, kind: str, name: str, session: Session = Depends(current)):
        ok = (kind == "cards" and re.fullmatch(r"[0-9a-f]{16,64}\.png", name)) or \
             (kind == "videos" and re.fullmatch(r"[0-9a-f]{16,64}\.(mp4|jpg)", name)) or \
             (kind == "clips" and re.fullmatch(r"[0-9a-f]{32}\.(mp4|jpg)", name))
        if not ok:
            raise HTTPException(404, "not found")
        try:
            r = await B().media(kind, name, request.headers.get("range"))
        except BackendError:
            raise HTTPException(502, "media service unreachable") from None
        if r.status_code not in (200, 206):
            await r.aclose()
            raise HTTPException(404 if r.status_code == 404 else 502, "not found")
        keep = {k: r.headers[k] for k in ("content-type", "content-length", "content-range", "accept-ranges")
                if k in r.headers}
        keep["Cache-Control"] = "private, max-age=3600"

        async def body():
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                await r.aclose()
        return StreamingResponse(body(), status_code=r.status_code, headers=keep)

    return app


def _safe_next(n: str) -> str:
    return n if isinstance(n, str) and n.startswith("/") and not n.startswith("//") and "\\" not in n and len(n) < 300 else "/"


app = create_app()
