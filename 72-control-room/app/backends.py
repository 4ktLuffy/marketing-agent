"""Every call to another service. Keys are added here and only here; nothing in this module's
results carries a key or an internal URL back to a page.

| Call | Service | Key sent |
|---|---|---|
| read items, reschedule | 19 content-calendar | reads: none; PATCH scheduled_at: X-API-Key |
| apply decisions | n8n webhook `mkt-apply-decisions` (same code as form 38) | X-Control-Key only |
| attempts | 46 learning-service | none |
| insights, hooks, campaigns, scorecards | 45 campaign-service | none |
| pillars, health | 61 content-engine | none; resume: X-API-Key |
| limits | 14 platform-rules | none |
| status | 22 status-page | none |
| images, videos | 17 image-cards, 71 video-assembly | none (the id is the secret) |

The control room never holds APPROVER_KEY: only n8n (38, 39, and this webhook) can approve.
"""
import asyncio
import time

import httpx


class BackendError(Exception):
    """A service did not answer as expected. The message is safe to show (no URL, no key)."""

    def __init__(self, service: str, detail: str, status: int | None = None):
        super().__init__(f"{service}: {detail}")
        self.service, self.detail, self.status = service, detail, status


def _detail(r: httpx.Response) -> str:
    try:
        body = r.json()
        d = body.get("detail", body) if isinstance(body, dict) else body
    except ValueError:
        d = r.text
    if isinstance(d, dict):
        d = d.get("message") or d
    return f"HTTP {r.status_code}: {str(d)[:300]}"


class Backends:
    def __init__(self, settings, client: httpx.AsyncClient):
        self.s, self.c = settings, client
        self._cache: dict[str, tuple[float, object]] = {}

    async def _get(self, service: str, url: str, params=None, timeout: float = 10.0):
        try:
            r = await self.c.get(url, params=params, timeout=timeout)
        except httpx.HTTPError as e:
            raise BackendError(service, f"unreachable ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise BackendError(service, _detail(r), r.status_code)
        return r.json()

    async def _cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        val = await fn()
        self._cache[key] = (time.monotonic(), val)
        return val

    def _key(self) -> dict:
        return {"X-API-Key": self.s.internal_key}

    # ---- 19 content-calendar
    async def items(self, **params) -> list[dict]:
        q = {k: v for k, v in params.items() if v not in (None, "")}
        data = await self._get("calendar", f"{self.s.calendar_url}/items", q)
        return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []

    async def item(self, item_id: int) -> dict:
        return await self._get("calendar", f"{self.s.calendar_url}/items/{item_id}")

    async def reschedule(self, item_id: int, when: str) -> dict:
        try:
            r = await self.c.patch(f"{self.s.calendar_url}/items/{item_id}", json={"scheduled_at": when},
                                   headers=self._key(), timeout=10.0)
        except httpx.HTTPError as e:
            raise BackendError("calendar", f"unreachable ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise BackendError("calendar", _detail(r), r.status_code)
        return r.json()

    # ---- n8n: decisions run in the workflow that shares form 38's code
    async def apply_decisions(self, reviewer: str, decisions: list[dict]) -> dict:
        try:
            r = await self.c.post(f"{self.s.n8n_url}/webhook/mkt-apply-decisions",
                                  json={"reviewer": reviewer, "decisions": decisions},
                                  headers={"X-Control-Key": self.s.control_key},
                                  timeout=self.s.decision_timeout_s)
        except httpx.HTTPError as e:
            raise BackendError("n8n", f"unreachable ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise BackendError("n8n", _detail(r), r.status_code)
        try:
            body = r.json()
        except ValueError:
            raise BackendError("n8n", "answer is not JSON (is workflow 72 imported and published?)") from None
        if not isinstance(body, dict):
            raise BackendError("n8n", "unexpected answer")
        return body

    # ---- 46 learning-service
    async def attempts(self, item_id: int) -> dict:
        return await self._get("learning", f"{self.s.learning_url}/items/{item_id}/attempts")

    # ---- 45 campaign-service
    async def insights(self, days: int) -> dict:
        return await self._get("campaigns", f"{self.s.campaigns_url}/insights", {"days": days}, timeout=30)

    async def hooks(self, days: int) -> dict:
        return await self._get("campaigns", f"{self.s.campaigns_url}/insights/hooks",
                               {"days": days, "explore": 0}, timeout=30)

    async def campaigns(self, status: str | None = None) -> list[dict]:
        data = await self._get("campaigns", f"{self.s.campaigns_url}/campaigns", {"status": status} if status else None)
        return data if isinstance(data, list) else []

    async def scorecard(self, cid: int) -> dict:
        return await self._get("campaigns", f"{self.s.campaigns_url}/campaigns/{cid}/scorecard", timeout=20)

    async def unmeasured(self) -> list[dict]:
        data = await self._get("campaigns", f"{self.s.campaigns_url}/report/unmeasured")
        return data if isinstance(data, list) else []

    # ---- 61 content-engine
    async def pillars(self) -> list[dict]:
        data = await self._get("engine", f"{self.s.engine_url}/pillars")
        return data if isinstance(data, list) else []

    async def pillar_titles(self) -> dict[int, str]:
        async def load():
            try:
                return {p["id"]: p.get("title") or f"pillar #{p['id']}" for p in await self.pillars()}
            except BackendError:
                return {}
        return await self._cached("pillar_titles", 60, load)

    async def pillar_health(self, pid: int) -> dict:
        return await self._get("engine", f"{self.s.engine_url}/pillars/{pid}/health")

    async def resume(self, pid: int) -> dict:
        try:
            r = await self.c.post(f"{self.s.engine_url}/pillars/{pid}/resume", headers=self._key(), timeout=10.0)
        except httpx.HTTPError as e:
            raise BackendError("engine", f"unreachable ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise BackendError("engine", _detail(r), r.status_code)
        return r.json()

    # ---- 14 platform-rules
    async def limits(self) -> dict:
        async def load():
            try:
                data = await self._get("rules", f"{self.s.rules_url}/rules")
                return data.get("channels", {}) if isinstance(data, dict) else {}
            except BackendError:
                return {}
        return await self._cached("limits", 600, load)

    # ---- 22 status-page
    async def status(self) -> dict:
        return await self._get("status", f"{self.s.status_url}/status", timeout=30)

    # ---- media from 17 / 71, streamed through (so a phone on HTTPS never needs their ports)
    async def media(self, kind: str, name: str, range_header: str | None):
        base = {"cards": self.s.cards_url, "videos": self.s.video_url, "clips": self.s.clips_url}[kind]
        headers = {"Range": range_header} if range_header else {}
        req = self.c.build_request("GET", f"{base}/{kind}/{name}", headers=headers, timeout=60)
        try:
            return await self.c.send(req, stream=True)
        except httpx.HTTPError as e:
            raise BackendError(kind, f"unreachable ({type(e).__name__})") from None


async def gather_soft(*coros):
    """Run calls in parallel; a failed one becomes its BackendError instead of failing the page."""
    out = await asyncio.gather(*coros, return_exceptions=True)
    for o in out:
        if isinstance(o, BaseException) and not isinstance(o, BackendError):
            raise o
    return out
