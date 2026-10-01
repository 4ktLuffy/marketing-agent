"""Audit page: is what the business has already published still true?

Paste page or PDF addresses (one per line) and/or old text; 88 (POST /audit, read-only) reads them
(addresses through the page extractor 07) and lists every sentence that contradicts a current fact:
an old price, an expired offer, another branch's value, an internal value on a public page. Anyone
signed in can open the page; running an audit needs a writer. The 88 URL and key never reach the
browser; nothing here is stored.
"""
import re
from datetime import date

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse

from .backends import BackendError
from .facts_page import DIMS
from .tasks import scope_options

MAX_URLS = 10
MAX_TEXT = 40000
URL_RE = re.compile(r"^https?://\S{4,1990}$", re.I)
LEAK_WHY = "An internal value must never be written on a public page."


def parse_form(form) -> tuple[dict | None, list[str], dict]:
    """Form -> (88 body or None, errors, values to re-show). Pure; no network."""
    urls_raw = str(form.get("urls", "") or "")[:20000]
    text = str(form.get("text", "") or "")[:MAX_TEXT + 1]
    label = " ".join(str(form.get("label", "") or "").split())[:120]
    on = str(form.get("on", "") or "").strip()[:10]
    scope = {d: [str(x).strip()[:80] for x in form.getlist(f"scope_{d}")[:20] if str(x).strip()] for d, _ in DIMS}
    v = {"urls": urls_raw, "text": text[:MAX_TEXT], "label": label, "on": on, **{f"scope_{d}": scope[d] for d in scope}}
    errors: list[str] = []
    urls = list(dict.fromkeys(u.strip() for u in urls_raw.splitlines() if u.strip()))
    bad = [u for u in urls if not URL_RE.match(u)]
    if bad:
        errors.append(f"Addresses must start with http:// or https:// (not: {bad[0][:60]}).")
    if len(text) > MAX_TEXT:
        errors.append(f"The pasted text is too long (at most {MAX_TEXT} characters).")
    sources = [{"url": u} for u in urls if URL_RE.match(u)]
    if text.strip():
        sources.append({"text": text[:MAX_TEXT], "label": label or "pasted text"})
    if not sources and not errors:
        errors.append("Add at least one page address or some text to check.")
    if len(sources) > MAX_URLS:
        errors.append(f"At most {MAX_URLS} sources at a time.")
    if on:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", on):
                raise ValueError
            date.fromisoformat(on)
        except ValueError:
            errors.append("The date is like 2026-10-01, or leave it empty for today.")
    if errors:
        return None, errors, v
    body = {"sources": sources, "scope": {d: x for d, x in scope.items() if x}}
    if on:
        body["on"] = on
    return body, errors, v


def result_view(out: dict) -> dict:
    """88 answer -> rows the template shows, defensive about shape."""
    sources, rows = [], []
    for s in out.get("sources") or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("source") or "")[:300]
        drift = [d for d in s.get("drift") or [] if isinstance(d, dict)]
        sources.append({"source": name, "checked": s.get("sentences_checked") if isinstance(s.get("sentences_checked"), int) else 0,
                        "ok": s.get("ok_matches") if isinstance(s.get("ok_matches"), int) else 0, "drift": len(drift),
                        "note": str(s.get("note") or "")[:200] or None, "error": str(s.get("error") or "")[:300] or None})
        for d in drift:
            leak = bool(d.get("leak"))
            should = d.get("should_say")
            rows.append({"source": name, "sentence": str(d.get("sentence") or "")[:400], "says": str(d.get("says") or "")[:300],
                         "should_say": str(should)[:300] if should else None, "leak": leak,
                         "fact_key": str(d.get("fact_key") or "")[:60] or None, "why": LEAK_WHY if leak else str(d.get("detail") or "")[:300]})
    return {"on": str(out.get("on") or "")[:10], "sources": sources, "rows": rows}


def register(app, page, current, csrf, B, need=None):
    need = need or (lambda role: csrf)
    s = app.state.settings

    async def options():
        try:
            data = await B().facts_v2()
            return scope_options([f for f in data.get("facts") or [] if isinstance(f, dict)])
        except BackendError:
            return {d: [] for d, _ in DIMS}

    def blank():
        return {"urls": "", "text": "", "label": "", "on": "", **{f"scope_{d}": [] for d, _ in DIMS}}

    async def show(request, session, status=200, values=None, **ctx):
        if not s.tasks_url:
            return page(request, "audit.html", session, nav="more", not_installed=True, values=blank(), options={}, dims=DIMS)
        return page(request, "audit.html", session, status, nav="more", values=values or blank(),
                    options=await options(), dims=DIMS, **ctx)

    @app.get("/audit", response_class=HTMLResponse)
    async def audit_get(request: Request, session=Depends(current)):
        return await show(request, session)

    @app.post("/audit", response_class=HTMLResponse)
    async def audit_run(request: Request, session=Depends(need("writer"))):
        if not s.tasks_url:
            return await show(request, session)
        form = await request.form()
        body, errors, v = parse_form(form)
        if errors:
            return await show(request, session, 422, v, errors=errors)
        try:
            out = await B()._tasks("POST", "/audit", json=body, timeout=180)
        except BackendError as e:
            msg = ("Too many checks just now; try again in a minute." if e.status == 429
                   else f"The audit did not run: the task bridge (88) said {e.detail}")
            return await show(request, session, 429 if e.status == 429 else 422 if e.status in (413, 422) else 502, v, errors=[msg])
        return await show(request, session, 200, v, result=result_view(out if isinstance(out, dict) else {}))
