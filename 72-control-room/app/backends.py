"""Every call to another service. Keys are added here and only here; nothing in this module's
results carries a key or an internal URL back to a page.

| Call | Service | Key sent |
|---|---|---|
| read items, reschedule | 19 content-calendar | reads: none; PATCH scheduled_at: X-API-Key |
| apply decisions | n8n webhook `mkt-apply-decisions` (same code as form 38) | X-Control-Key only |
| attempts | 46 learning-service | none |
| insights, hooks, campaigns, scorecards | 45 campaign-service | none |
| paid ads summary (spend, CPL, ROAS, alerts) | 84 ads-sync | none |
| pillars, health | 61 content-engine | none; resume: X-API-Key |
| limits | 14 platform-rules | none |
| status | 22 status-page | none |
| images, videos | 17 image-cards, 71 video-assembly | none (the id is the secret) |
| brand setup: completeness, summary, voice questions | 05 brand-service | none |
| brand setup: editable brand (read, save, reset), save voice | 05 brand-service | X-API-Key |
| voice interview -> profile (`voice_profile` prompt) | 03 llm-gateway | X-API-Key (and `X-Caller: 72 voice interview`) |
| activity (calls in flight, recent calls, model totals) | 03 llm-gateway (each gateway in ACTIVITY_GATEWAYS) | X-API-Key |

The control room never holds APPROVER_KEY: only n8n (38, 39, and this webhook) can approve.
"""
import asyncio
import time

import httpx


class BackendError(Exception):
    """A service did not answer as expected. The message is safe to show (no URL, no key)."""

    def __init__(self, service: str, detail: str, status: int | None = None, errors: list | None = None):
        super().__init__(f"{service}: {detail}")
        self.service, self.detail, self.status = service, detail, status
        self.errors = errors or []   # validation errors (422): [{"loc": [...], "msg": str}]


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

    async def _get(self, service: str, url: str, params=None, timeout: float = 10.0, headers: dict | None = None):
        try:
            r = await self.c.get(url, params=params, timeout=timeout, headers=headers)
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

    # ---- 84 ads-sync (read-only summary; the page never syncs or changes budgets)
    async def ads_summary(self, days: int) -> dict:
        return await self._get("ads", f"{self.s.ads_url}/summary", {"days": days}, timeout=20)

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
        if not base:
            raise BackendError(kind, "not installed")
        headers = {"Range": range_header} if range_header else {}
        req = self.c.build_request("GET", f"{base}/{kind}/{name}", headers=headers, timeout=60)
        try:
            return await self.c.send(req, stream=True)
        except httpx.HTTPError as e:
            raise BackendError(kind, f"unreachable ({type(e).__name__})") from None


    # ---- 03 gateway(s): the activity log (metadata only; see 03 README)
    async def gateway_activity(self, url: str, limit: int = 200) -> dict:
        data = await self._get("gateway", f"{url}/v1/activity", {"limit": limit}, timeout=5.0, headers=self._key())
        if not isinstance(data, dict):
            raise BackendError("gateway", "unexpected answer")
        return data

    # ---- 05 brand-service and 03 gateway: brand setup
    async def _call(self, service: str, method: str, url: str, *, json=None, key: bool = False,
                    timeout: float = 10.0, none_on_404: bool = False, headers: dict | None = None):
        try:
            h = {**(self._key() if key else {}), **(headers or {})} or None
            r = await self.c.request(method, url, json=json, headers=h, timeout=timeout)
        except httpx.HTTPError as e:
            raise BackendError(service, f"unreachable ({type(e).__name__})") from None
        if none_on_404 and r.status_code == 404:
            return None
        if r.status_code >= 400:
            errors = []
            if r.status_code == 422:
                try:
                    d = r.json().get("detail")
                    errors = [e for e in d if isinstance(e, dict)] if isinstance(d, list) else []
                except (ValueError, AttributeError):
                    pass
            raise BackendError(service, _detail(r), r.status_code, errors)
        return r.json()

    async def document(self, title: str, markdown: str) -> str:
        """21 POST /document: markdown as one standalone HTML page."""
        return (await self._call("report", "POST", f"{self.s.report_url}/document",
                                 json={"title": title, "markdown": markdown}, timeout=20))["html"]

    async def brand_editable(self) -> dict:
        return await self._call("brand", "GET", f"{self.s.brand_url}/brand/editable", key=True)

    async def brand_save(self, fields: dict) -> dict:
        return await self._call("brand", "PUT", f"{self.s.brand_url}/brand/editable", json=fields, key=True)

    async def brand_reset(self) -> dict:
        return await self._call("brand", "DELETE", f"{self.s.brand_url}/brand/editable", key=True)

    async def brand_completeness(self) -> dict:
        return await self._call("brand", "GET", f"{self.s.brand_url}/brand/completeness", key=True)

    async def brand_summary(self) -> str:
        return (await self._call("brand", "GET", f"{self.s.brand_url}/profile/summary", key=True)).get("summary", "")

    async def voice_questions(self) -> list[str]:
        return (await self._call("brand", "GET", f"{self.s.brand_url}/voice/questions", key=True)).get("questions", [])

    async def voice(self) -> dict | None:
        return await self._call("brand", "GET", f"{self.s.brand_url}/voice", none_on_404=True, key=True)

    async def voice_save(self, profile: dict) -> dict:
        return await self._call("brand", "PUT", f"{self.s.brand_url}/voice", json=profile, key=True)

    async def voice_generate(self, answers: str) -> dict:
        body = await self._call("gateway", "POST", f"{self.s.gateway_url}/v1/run", key=True,
                                json={"prompt": "voice_profile", "vars": {"answers": answers}},
                                headers={"X-Caller": "72 voice interview"}, timeout=self.s.voice_timeout_s)
        out = body.get("output") if isinstance(body, dict) else None
        if not isinstance(out, dict):
            raise BackendError("gateway", "the model did not return a profile; try again")
        return out


async def gather_soft(*coros):
    """Run calls in parallel; a failed one becomes its BackendError instead of failing the page."""
    out = await asyncio.gather(*coros, return_exceptions=True)
    for o in out:
        if isinstance(o, BaseException) and not isinstance(o, BackendError):
            raise o
    return out
