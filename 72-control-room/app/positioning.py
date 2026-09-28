"""Positioning page: 78's monthly positioning map (themes x brands, quotes, white space, shifts).

Read-only and server-rendered: 72 fetches 78 `GET /positioning`, `/positioning/latest` or
`/positioning/{id}` (no key: 78 serves them without one) and renders the matrix. A cell is a
link to the same page with `theme` and `brand`, which lists that cell's quotes with their source
links. Only http(s) source links are rendered.
"""
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, Request
from fastapi.responses import HTMLResponse

from .backends import BackendError, _detail


def _safe_url(u) -> str | None:
    u = str(u or "")
    return u if urlsplit(u).scheme in ("http", "https") and len(u) < 2000 else None


async def _get(app, path: str):
    s = app.state.settings
    if not s.ad_library_url:
        raise BackendError("ad-library", "not installed")
    try:
        r = await app.state.backends.c.get(f"{s.ad_library_url}{path}", timeout=15.0)
    except httpx.HTTPError as e:
        raise BackendError("ad-library", f"unreachable ({type(e).__name__})") from None
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise BackendError("ad-library", _detail(r), r.status_code)
    return r.json()


def view(snap: dict, theme: str, brand: str) -> dict:
    """Template data: brands (columns), rows (theme, cells), the selected cell's quotes."""
    brands = [b for b in snap.get("brands") or [] if isinstance(b, dict)]
    names = [b.get("name") for b in brands]
    cells = snap.get("cells") or {}
    top = max([c.get("count", 0) for row in cells.values() for c in row.values()] + [1])
    rows = []
    for t in cells:
        row = cells[t]
        rows.append({"theme": t, "cells": [{"brand": n, "count": (row.get(n) or {}).get("count", 0),
                                            "facts": (row.get(n) or {}).get("from_facts", 0),
                                            "heat": round(4 * (row.get(n) or {}).get("count", 0) / top)}
                                           for n in names]})
    selected = None
    if theme in cells and brand in names:
        c = cells[theme].get(brand) or {}
        selected = {"theme": theme, "brand": brand, "count": c.get("count", 0),
                    "quotes": [{"quote": q.get("quote"), "ref": (q.get("source") or {}).get("ref"),
                                "kind": (q.get("source") or {}).get("kind"),
                                "url": _safe_url((q.get("source") or {}).get("url"))} for q in c.get("quotes") or []]}
    descr = {t.get("key"): t.get("description") for t in snap.get("themes") or [] if isinstance(t, dict)}
    for s in snap.get("shifts") or []:
        for q in s.get("quote") or []:
            q["url"] = _safe_url((q.get("source") or {}).get("url"))
    return {"brands": brands, "rows": rows, "selected": selected, "descr": descr}


def register(app, page, current):
    @app.get("/positioning", response_class=HTMLResponse)
    async def positioning(request: Request, id: int | None = None, theme: str = "", brand: str = "",
                          session=Depends(current)):
        if not app.state.settings.ad_library_url:
            return page(request, "positioning.html", session, not_installed=True, snap=None, error=None,
                        history=[], nav="more")
        try:
            history = await _get(app, "/positioning") or []
            snap = await _get(app, f"/positioning/{id}" if id else "/positioning/latest")
            err = None if snap or not id else f"Positioning map {id} not found."
        except BackendError as e:
            history, snap, err = [], None, str(e)
        v = view(snap, theme[:60], brand[:200]) if snap else {}
        return page(request, "positioning.html", session, snap=snap, history=history, error=err,
                    not_installed=False, nav="more", **v)
