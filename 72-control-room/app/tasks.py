"""Tasks: ask any chatbot for copy without giving it your business, then check every sentence.

Flow (88 task-bridge, contract §4): new task (goal, pieces, scope, publish date) -> the pack, with
the data-sharing preview FIRST (what leaves, what stays a placeholder, what is held back) -> copy
it into ChatGPT / Claude / Gemini / another chat -> paste the answer back -> check the split ->
submit -> each piece's filled text with evidence per sentence, and the calendar items it made ->
export once every piece is approved. Plus the blockers list. Every call to 88 is made here with
INTERNAL_API_KEY; nothing about 88 (URL, key) reaches the browser.
"""
import re
from datetime import date

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import views
from .backends import BackendError
from .facts_page import DIMS, today

TASK_ID = re.compile(r"^T-[A-Za-z0-9]{4,12}$")
CHANNELS = ["linkedin", "x", "facebook", "instagram", "threads", "blog", "email", "newsletter", "website"]
KINDS = ["post", "article", "email", "ad", "caption", "story", "page"]
PROVIDERS = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini", "other": "Another AI"}
MAX_PIECES = 10
MAX_PASTE = 40000
WITHHELD_WHY = {
    "restricted": "restricted: it never leaves your business",
    "internal": "internal: it may only travel as a placeholder",
    "expired": "expired before the publish date",
    "not_yet_valid": "not valid yet on the publish date",
    "out_of_scope": "for another branch, channel or group than this task",
    "scope_unspecified": "depends on a choice this task leaves open (pick it to include this fact)",
    "draft": "not confirmed yet",
    "superseded": "replaced by a newer fact",
    "retired": "retired",
}
BLOCKER_LINK = {"missing_fact": "/facts?show=questions", "blocked_slot": "/facts?show=questions",
                "review_due": "/facts?show=due", "expired_in_use": "/facts?show=expired",
                "expired": "/facts?show=expired"}
SAFE_LINK = re.compile(r"^/(?:tasks|facts|items|blockers)(?:/[A-Za-z0-9_-]+)*/?(?:\?[A-Za-z0-9_=&-]*)?(?:#[\w-]*)?$")


def blank_piece() -> dict:
    return {"channel": "linkedin", "kind": "post", "max_chars": ""}


def scope_options(facts: list[dict]) -> dict[str, list[str]]:
    """Every scope value any fact names, per dimension: what the task's pickers offer."""
    out: dict[str, set] = {d: set() for d, _ in DIMS}
    for f in facts:
        for d, _ in DIMS:
            for v in (f.get("scope") or {}).get(d) or []:
                if isinstance(v, str) and v.strip():
                    out[d].add(v.strip())
    return {d: sorted(vals, key=str.lower) for d, vals in out.items()}


def parse_new(form, options: dict[str, list[str]]) -> tuple[dict, dict, dict]:
    """Form -> (88 body, errors, values to re-show)."""
    get = lambda k, n: str(form.get(k, "") or "")[:n].strip()  # noqa: E731
    v = {"goal": get("goal", 2000), "audience": get("audience", 600), "notes": get("notes", 2000),
         "publish_on": get("publish_on", 20)}
    errors: dict[str, list[str]] = {}
    err = lambda f, m: errors.setdefault(f, []).append(m)  # noqa: E731
    chans, kinds, maxes = (form.getlist(n)[:MAX_PIECES + 1] for n in ("p_channel", "p_kind", "p_max"))
    pieces = [{"channel": str(c).strip().lower()[:40], "kind": str(k).strip().lower()[:40], "max_chars": str(m).strip()[:7]}
              for c, k, m in zip(chans, kinds, maxes)]
    remove = form.get("remove")
    if remove is not None and str(remove).isdigit() and int(remove) < len(pieces):
        pieces.pop(int(remove))
    v["pieces"] = pieces
    scope = {}
    for d, _ in DIMS:
        picked = [str(x).strip()[:80] for x in form.getlist(f"scope_{d}") if str(x).strip()]
        extra = [x.strip()[:80] for x in re.split(r"[,\n]", get(f"scope_{d}_other", 1000)) if x.strip()]
        scope[d] = list(dict.fromkeys(picked + extra))[:50]
        v[f"scope_{d}"] = scope[d]
        v[f"scope_{d}_other"] = ", ".join(x for x in extra if x not in options.get(d, []))

    if len(v["goal"]) < 3:
        err("goal", "say in a sentence what the copy is for")
    elif len(v["goal"]) > 1000:
        err("goal", "at most 1000 characters")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v["publish_on"]):
            raise ValueError
        date.fromisoformat(v["publish_on"])
    except ValueError:
        err("publish_on", "pick the day it goes out (like 2026-10-06)")
    if len(v["audience"]) > 300:
        err("audience", "at most 300 characters")
    if len(v["notes"]) > 1000:
        err("notes", "at most 1000 characters")
    allowed = set(CHANNELS) | set(options.get("channels", []))
    if not pieces:
        err("pieces", "add at least one piece")
    if len(pieces) > MAX_PIECES:
        err("pieces", f"at most {MAX_PIECES} pieces")
    body_pieces = []
    for i, p in enumerate(pieces[:MAX_PIECES], 1):
        if p["channel"] not in allowed:
            err("pieces", f"Piece {i}: pick a channel from the list")
        if p["kind"] not in KINDS:
            err("pieces", f"Piece {i}: pick what kind of piece it is")
        mc = None
        if p["max_chars"]:
            if not p["max_chars"].isdigit() or not 20 <= int(p["max_chars"]) <= 100000:
                err("pieces", f"Piece {i}: the length limit is a number of characters (20 or more), or blank")
            else:
                mc = int(p["max_chars"])
        body_pieces.append({"key": f"p{i}", "channel": p["channel"], "kind": p["kind"], "max_chars": mc})
    body = {"goal": v["goal"], "pieces": body_pieces, "scope": scope, "publish_on": v["publish_on"],
            "audience": v["audience"] or None, "notes": v["notes"] or None}
    return body, errors, v


def group_findings(findings: list) -> list[dict]:
    """Findings -> [{sentence, chips:[{label, words, level, fact_key, quote, detail, blocking}]}], in order.
    A finding without a sentence (e.g. a missing disclosure) is about the whole piece."""
    out: dict[str, dict] = {}
    for i, f in enumerate(findings or []):
        if not isinstance(f, dict):
            continue
        label = f.get("label") if f.get("label") in views.EVIDENCE else "review"
        words, level = views.EVIDENCE[label]
        if label == "no_source" and not f.get("blocking"):
            level = "warn"
        sentence = str(f.get("sentence") or "").strip()
        g = out.setdefault(sentence, {"sentence": sentence, "chips": []})
        g["chips"].append({"label": label, "words": words, "level": level, "fact_key": f.get("fact_key"),
                           "quote": f.get("quote"), "detail": str(f.get("detail") or "")[:300],
                           "blocking": bool(f.get("blocking")), "index": i,
                           "acceptable": label in ACCEPTABLE and bool(f.get("blocking")) and not f.get("accepted"),
                           "accepted": accepted_view(f.get("accepted"))})
    return list(out.values())


# the only finding a person may overrule in the control room (88 enforces the same list)
ACCEPTABLE = {"missing_disclosure"}
PIECE_KEY = re.compile(r"^[a-z0-9_-]{1,20}$")
SHA = re.compile(r"^[0-9a-f]{64}$")


def accepted_view(a) -> dict | None:
    if not isinstance(a, dict):
        return None
    return {"by": str(a.get("by") or "")[:80], "note": str(a.get("note") or "")[:300], "at": str(a.get("at") or "")[:25]}


def piece_result(p: dict, channels: dict) -> dict:
    """One submitted piece (88 submit answer or a task's piece) as the review page shows it."""
    item_id = p.get("calendar_item_id") or p.get("item_id")
    key = str(p.get("piece_key") or p.get("key") or "")
    return {"key": key, "channel": p.get("channel") or channels.get(key) or "", "text": p.get("filled_text") or "",
            "blocked": bool(p.get("blocked")), "groups": group_findings(p.get("findings") or []),
            "item_id": item_id if isinstance(item_id, int) else None, "state": p.get("state"),
            "sha": p.get("filled_sha256") if isinstance(p.get("filled_sha256"), str) else None,
            "rule_problems": rule_problems(p.get("checks"))}


RULE_WHERE = {"brand": "Brand rules", "platform": "Channel rules"}


def rule_problems(checks) -> list[dict]:
    """Brand (05 /check) and channel (14 /validate) problems 88 found. An error blocks the piece,
    so the reviewer must see it next to the fact findings, not only a "blocked" chip."""
    out = []
    if not isinstance(checks, dict):
        return out
    for where, label in RULE_WHERE.items():
        for v in checks.get(where) or []:
            if isinstance(v, dict):
                out.append({"where": label, "blocking": v.get("severity") == "error",
                            "text": str(v.get("detail") or v.get("rule") or "")[:300]})
    for s in checks.get("skipped") or []:
        out.append({"where": "Not checked", "blocking": False, "text": str(s)[:300]})
    return out


def export_state(task: dict) -> tuple[bool, list[str]]:
    """88 says whether the task can be exported; otherwise what is missing."""
    exp = task.get("export") if isinstance(task.get("export"), dict) else {}
    ready = task.get("export_ready", exp.get("ready"))
    missing = task.get("export_missing", exp.get("missing"))
    if not isinstance(missing, list):
        missing = [f"{p.get('piece_key') or p.get('key')} ({p.get('channel') or 'piece'}) is not approved yet"
                   + (f" (now: {p.get('state')})" if p.get("state") else "")
                   for p in (task.get("piece_state") or task.get("pieces") or [])
                   if isinstance(p, dict) and p.get("state") != "approved"]
    return ready is True, [str(m)[:300] for m in missing]


def blocker_view(b: dict) -> dict:
    link = str(b.get("link") or "")
    if not SAFE_LINK.match(link):
        link = BLOCKER_LINK.get(str(b.get("kind") or ""), "")
    count = b.get("count") if isinstance(b.get("count"), int) else None
    return {"kind": str(b.get("kind") or ""), "count": count, "text": str(b.get("text") or b.get("kind") or "")[:300],
            "link": link}


def register(app, page, current, csrf, B, need=None):
    need = need or (lambda role: csrf)   # older callers: CSRF only, no roles
    s = app.state.settings

    def installed():
        return bool(s.tasks_url)

    def off(request, session, name="tasks.html"):
        return page(request, name, session, not_installed=True, nav="more", tasks=[], blockers=[])

    def check_id(task_id: str):
        if not TASK_ID.match(task_id):
            raise HTTPException(404, "no such task")

    def fail(request, session, e: BackendError):
        return page(request, "error.html", session, e.status if e.status in (404, 409) else 502, error=str(e), nav="more")

    @app.get("/tasks", response_class=HTMLResponse)
    async def tasks_list(request: Request, session=Depends(current)):
        if not installed():
            return off(request, session)
        try:
            ts, err = await B().tasks(), None
        except BackendError as e:
            ts, err = [], f"The task bridge (88) did not answer: {e.detail}"
        return page(request, "tasks.html", session, nav="more", tasks=ts, error=err)

    async def options():
        try:
            data = await B().facts_v2()
            return scope_options([f for f in data.get("facts") or [] if isinstance(f, dict)])
        except BackendError:
            return {d: [] for d, _ in DIMS}

    def new_page(request, session, status=200, **ctx):
        ctx.setdefault("errors", {})
        return page(request, "task_new.html", session, status, nav="more", channels=CHANNELS, kinds=KINDS,
                    dims=DIMS, **ctx)

    @app.get("/tasks/new", response_class=HTMLResponse)
    async def task_new(request: Request, session=Depends(current)):
        if not installed():
            return off(request, session)
        v = {"goal": "", "audience": "", "notes": "", "publish_on": "", "pieces": [blank_piece()],
             **{f"scope_{d}": [] for d, _ in DIMS}, **{f"scope_{d}_other": "" for d, _ in DIMS}}
        return new_page(request, session, values=v, options=await options(), today=today().isoformat())

    @app.post("/tasks/new", response_class=HTMLResponse)
    async def task_create(request: Request, session=Depends(need("writer"))):
        if not installed():
            return off(request, session)
        form = await request.form()
        opts = await options()
        body, errors, v = parse_new(form, opts)
        ctx = dict(values=v, options=opts, today=today().isoformat())
        action = form.get("action")
        if action == "add":
            if len(v["pieces"]) < MAX_PIECES:
                v["pieces"].append(blank_piece())
            return new_page(request, session, **ctx)
        if form.get("remove") is not None:
            return new_page(request, session, **ctx)
        if errors:
            return new_page(request, session, 422, errors=errors, **ctx)
        try:
            made = await B().task_create(body)
        except BackendError as e:
            return new_page(request, session, 422 if e.status == 422 else 502,
                            errors={"general": [f"Not created: the task bridge (88) said {e.detail}"]}, **ctx)
        tid = str(made.get("id") or "")
        if not TASK_ID.match(tid):
            return new_page(request, session, 502, errors={"general": ["The task bridge (88) returned no task id."]}, **ctx)
        return RedirectResponse(f"/tasks/{tid}/pack", status_code=303)

    async def load(task_id: str) -> dict:
        task = await B().task(task_id)
        if "pack" not in task or "share_preview" not in task:
            try:
                task = {**task, **await B().task_pack(task_id)}
            except BackendError as e:
                if e.status != 404:
                    raise
        return task

    @app.get("/tasks/{task_id}/pack", response_class=HTMLResponse)
    async def task_pack(request: Request, task_id: str, session=Depends(current)):
        check_id(task_id)
        if not installed():
            return off(request, session)
        try:
            task = await load(task_id)
        except BackendError as e:
            return fail(request, session, e)
        return pack_page(request, session, task)

    def pack_page(request, session, task, status=200, **ctx):
        prev = task.get("share_preview") if isinstance(task.get("share_preview"), dict) else {}
        withheld = [{"key": w.get("key"), "why": WITHHELD_WHY.get(str(w.get("reason")), str(w.get("reason") or ""))}
                    for w in prev.get("withheld") or [] if isinstance(w, dict)]
        return page(request, "task_pack.html", session, status, nav="more", task=task,
                    sent=[str(x) for x in prev.get("sent") or []], slotted=[str(x) for x in prev.get("slotted") or []],
                    withheld=withheld, chars=prev.get("chars"), providers=PROVIDERS, **ctx)

    @app.post("/tasks/{task_id}/paste", response_class=HTMLResponse)
    async def task_paste(request: Request, task_id: str, session=Depends(need("writer"))):
        check_id(task_id)
        if not installed():
            return off(request, session)
        form = await request.form()
        text, provider = str(form.get("text", "")), str(form.get("provider", ""))
        problem = None
        if not text.strip():
            problem = "Paste the AI's whole answer first."
        elif len(text) > MAX_PASTE:
            problem = f"That is too long ({len(text)} characters; at most {MAX_PASTE})."
        elif provider not in PROVIDERS:
            problem = "Say which AI you used."
        if problem:
            try:
                task = await load(task_id)
            except BackendError as e:
                return fail(request, session, e)
            return pack_page(request, session, task, 422, paste_error=problem, pasted=text[:MAX_PASTE], provider=provider)
        try:
            split = await B().task_paste(task_id, text, provider)
            task = await B().task(task_id)
        except BackendError as e:
            return fail(request, session, e)
        return review_page(request, session, task, split=split)

    def review_page(request, session, task, status=200, split=None, results=None, **ctx):
        pieces = [p for p in task.get("pieces") or [] if isinstance(p, dict)]
        channels = {str(p.get("key") or p.get("piece_key")): p.get("channel") for p in pieces}
        # 88 returns the requested pieces under "pieces" and their results under "piece_state".
        state = [p for p in task.get("piece_state") or [] if isinstance(p, dict)] or pieces
        if results is None and any(p.get("filled_text") for p in state):
            results = [piece_result(p, channels) for p in state]
        ready, missing = export_state(task)
        sp = None
        if split is not None:
            parts = [x for x in split.get("split") or [] if isinstance(x, dict)]
            by_key = {str(x.get("piece_key")): x for x in parts}
            sp = {"draft_id": split.get("draft_id"), "parts": parts,
                  "problems": [str(x.get("text") if isinstance(x, dict) else x)[:300] for x in split.get("problems") or []],
                  "rows": [{"key": str(p.get("key") or p.get("piece_key")), "channel": p.get("channel"),
                            "text": (by_key.get(str(p.get("key") or p.get("piece_key"))) or {}).get("text") or ""}
                           for p in pieces]}
        return page(request, "task_review.html", session, status, nav="more", task=task, channels=channels,
                    split=sp, results=results, ready=ready, missing=missing, **ctx)

    @app.post("/tasks/{task_id}/split", response_class=HTMLResponse)
    async def task_split(request: Request, task_id: str, session=Depends(need("writer"))):
        check_id(task_id)
        if not installed():
            return off(request, session)
        form = await request.form()
        draft = str(form.get("draft_id", ""))
        if not draft.isdigit():
            raise HTTPException(422, "draft_id: a number")
        keys, texts = form.getlist("piece_key")[:MAX_PIECES], form.getlist("piece_text")[:MAX_PIECES]
        parts = [{"piece_key": str(k)[:20], "text": str(t)[:MAX_PASTE]} for k, t in zip(keys, texts)]
        if not any(p["text"].strip() for p in parts):
            raise HTTPException(422, "give at least one piece some text")
        try:
            split = await B().task_split(task_id, int(draft), parts)
            task = await B().task(task_id)
        except BackendError as e:
            return fail(request, session, e)
        return review_page(request, session, task, split=split)

    @app.post("/tasks/{task_id}/submit", response_class=HTMLResponse)
    async def task_submit(request: Request, task_id: str, session=Depends(need("writer"))):
        check_id(task_id)
        if not installed():
            return off(request, session)
        form = await request.form()
        draft = str(form.get("draft_id", ""))
        if not draft.isdigit():
            raise HTTPException(422, "draft_id: a number")
        # A split with notes: the person confirmed the pieces as shown, so save that split first.
        keys, texts = form.getlist("piece_key")[:MAX_PIECES], form.getlist("piece_text")[:MAX_PIECES]
        parts = [{"piece_key": str(k)[:20], "text": str(t)[:MAX_PASTE]} for k, t in zip(keys, texts)]
        try:
            if parts and any(p["text"].strip() for p in parts):
                confirmed = await B().task_split(task_id, int(draft), parts)
                draft = str(confirmed.get("draft_id", draft))
            out = await B().task_submit(task_id, int(draft))
            task = await B().task(task_id)
        except BackendError as e:
            return fail(request, session, e)
        got = out.get("pieces") if isinstance(out, dict) else out
        channels = {str(p.get("key") or p.get("piece_key")): p.get("channel") for p in task.get("pieces") or []
                    if isinstance(p, dict)}
        results = [piece_result(p, channels) for p in got or [] if isinstance(p, dict)]
        return review_page(request, session, task, results=results, submitted=True)

    @app.post("/tasks/{task_id}/accept", response_class=HTMLResponse)
    async def task_accept(request: Request, task_id: str, session=Depends(need("approver"))):
        """"It's there, in other words": the person overrules one missing-disclosure finding on the
        text they saw. 88 checks the hash, records who and why, and moves the piece to review only
        when nothing else blocks it."""
        check_id(task_id)
        if not installed():
            return off(request, session)
        form = await request.form()
        key, idx, sha = str(form.get("piece_key", "")), str(form.get("finding", "")), str(form.get("sha", ""))
        note = " ".join(str(form.get("note", "")).split())[:300]
        # Optional: the exact words from the piece, saved as a draft wording for the owner (88 -> 05).
        # Not normalised: 88 checks it is copied from the text, so it must stay as the person pasted it.
        wording = str(form.get("wording", "")).strip()
        if not PIECE_KEY.match(key) or not idx.isdigit() or not SHA.match(sha):
            raise HTTPException(422, "piece_key, finding and sha are needed")
        try:
            task = await B().task(task_id)
        except BackendError as e:
            return fail(request, session, e)
        if len(note) < 3:
            return review_page(request, session, task, 422,
                               accept_error="Say where the disclosure is (for example: “said as ‘frames extra’”).")
        if wording and not 3 <= len(wording) <= 200:
            return review_page(request, session, task, 422, accept_error=(
                "The wording to save is 3 to 200 characters, copied exactly from the post (or leave it empty)."))
        try:
            out = await B().task_accept(task_id, key, int(idx), sha, session.display or s.reviewer or "control room",
                                        note, wording or None)
            task = await B().task(task_id)
        except BackendError as e:
            if e.status in (409, 422):
                msg = ("The text changed since you looked, or this finding can't be accepted. Reload and check again."
                       if not wording else
                       "The text changed since you looked, this finding can't be accepted, or the wording to save "
                       "is not copied exactly from the post. Check it and try again.")
                return review_page(request, session, task, e.status, accept_error=msg)
            return fail(request, session, e)
        proposal = out.get("wording_proposal") if isinstance(out, dict) else None
        wording_saved = wording_error = None
        if wording and isinstance(proposal, dict):
            if proposal.get("error"):
                wording_error = str(proposal.get("error"))[:300]
            elif proposal.get("id") is not None:
                wording_saved = True
        return review_page(request, session, task, accepted=True, wording_saved=wording_saved,
                           wording_error=wording_error)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    async def task_detail(request: Request, task_id: str, session=Depends(current)):
        check_id(task_id)
        if not installed():
            return off(request, session)
        try:
            task = await B().task(task_id)
        except BackendError as e:
            return fail(request, session, e)
        return review_page(request, session, task)

    @app.get("/tasks/{task_id}/export")
    async def task_export(request: Request, task_id: str, format: str = "md", session=Depends(current)):
        check_id(task_id)
        if format not in ("txt", "md"):
            raise HTTPException(422, "format: txt or md")
        if not installed():
            return off(request, session)
        try:
            content, _ = await B().task_export(task_id, format)
        except BackendError as e:
            msg = f"Not ready to export: {e.detail}" if e.status == 409 else str(e)
            return page(request, "error.html", session, e.status if e.status in (404, 409) else 502, error=msg, nav="more")
        media = "text/markdown; charset=utf-8" if format == "md" else "text/plain; charset=utf-8"
        return Response(content, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{task_id}.{format}"',
                                 "Cache-Control": "private, no-store"})

    @app.get("/blockers", response_class=HTMLResponse)
    async def blockers(request: Request, session=Depends(current)):
        if not installed():
            return off(request, session, "blockers.html")
        try:
            bs, err = [blocker_view(b) for b in await B().blockers()], None
        except BackendError as e:
            bs, err = [], f"The task bridge (88) did not answer: {e.detail}"
        return page(request, "blockers.html", session, nav="more", blockers=bs, error=err)


async def compact_blockers(backends, settings) -> list[dict]:
    """For the queue header: blockers with a count, quickly; nothing when 88 is off or slow."""
    if not settings.tasks_url:
        return []
    try:
        return [b for b in (blocker_view(x) for x in await backends.blockers(timeout=3.0)) if b["count"] != 0]
    except BackendError:
        return []
