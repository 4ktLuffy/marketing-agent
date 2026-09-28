"""Product feed page: 87's uploads with their counts, and the downloads.

Read-only: 72 lists 87 `GET /batches` and passes the exports through (`export.csv|tsv`,
`supplemental.csv|tsv`) with the service key, which the browser never sees. Approving stays with
the approver key on 87 (72 holds none).
"""
import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .backends import BackendError, _detail

KINDS = {"export", "supplemental"}
FORMATS = {"csv": "text/csv; charset=utf-8", "tsv": "text/tab-separated-values; charset=utf-8"}


async def _get(app, path: str) -> httpx.Response:
    s = app.state.settings
    if not s.feed_url:
        raise BackendError("feed-optimizer", "not installed")
    try:
        r = await app.state.backends.c.get(f"{s.feed_url}{path}", headers={"X-API-Key": s.internal_key}, timeout=30.0)
    except httpx.HTTPError as e:
        raise BackendError("feed-optimizer", f"unreachable ({type(e).__name__})") from None
    if r.status_code >= 400:
        raise BackendError("feed-optimizer", _detail(r), r.status_code)
    return r


def register(app, page, current):
    @app.get("/feeds", response_class=HTMLResponse)
    async def feeds(request: Request, session=Depends(current)):
        if not app.state.settings.feed_url:
            return page(request, "feeds.html", session, not_installed=True, batches=[], error=None, nav="more")
        try:
            data = (await _get(app, "/batches?limit=50")).json()
            batches, err = [b for b in data if isinstance(b, dict)] if isinstance(data, list) else [], None
        except (BackendError, ValueError) as e:
            batches, err = [], str(e)
        return page(request, "feeds.html", session, not_installed=False, batches=batches, error=err, nav="more")

    @app.get("/feeds/{batch_id}/{kind}.{fmt}")
    async def feed_download(request: Request, batch_id: int, kind: str, fmt: str, session=Depends(current)):
        if kind not in KINDS or fmt not in FORMATS:
            raise HTTPException(404, "not found")
        if not app.state.settings.feed_url:
            return page(request, "error.html", session, 404, error="feed-optimizer (87) is not installed", nav="more")
        try:
            r = await _get(app, f"/batches/{batch_id}/{kind}.{fmt}")
        except BackendError as e:
            return page(request, "error.html", session, e.status if e.status == 404 else 502, error=str(e), nav="more")
        name = f"feed-{batch_id}-{'optimized' if kind == 'export' else 'supplemental'}.{fmt}"
        return Response(r.content, media_type=FORMATS[fmt],
                        headers={"Content-Disposition": f'attachment; filename="{name}"',
                                 "Cache-Control": "private, no-store"})
