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
| facts v2 (list, versions, add, edit, query), questions, starter kits, rules (read) | 05 brand-service | X-API-Key |
| confirm / retire a fact, import, apply a starter kit, confirm / dismiss a kit rule | 05 brand-service | X-API-Key + X-Owner-Key (FACT_OWNER_KEY) |
| learned disclosure wordings: list drafts | 05 brand-service | X-API-Key |
| learned disclosure wordings: confirm / dismiss | 05 brand-service | X-API-Key + X-Owner-Key (FACT_OWNER_KEY) |
| tasks, packs, paste, split, submit, export, blockers, reconcile | 88 task-bridge | X-API-Key |
| results: an item's audit and versions (read-only) | 19 content-calendar | X-API-Key |
| client links: make, list, revoke; a client's page: resolve / respond with token + PIN | 19 content-calendar | X-API-Key |
| onboarding: text of a web page or PDF | 07 page-extractor | none |
| onboarding: is a model there (health, prompt list) | 03 llm-gateway | none |
| onboarding: proposed facts per chunk (`propose_facts` prompt) | 03 llm-gateway | X-API-Key (and `X-Caller: 72 onboarding`) |
| onboarding: add a draft fact, add an open question | 05 brand-service | X-API-Key (never X-Owner-Key) |

The control room never holds APPROVER_KEY: only n8n (38, 39, and this webhook) can approve.
"""
import asyncio
import time
import unicodedata

import httpx

from .security import ACTOR


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

    async def item_audit(self, item_id: int) -> list[dict]:
        """19 GET /items/{id}/audit (keyed): every status change and edit, oldest first."""
        body = await self._call("calendar", "GET", f"{self.s.calendar_url}/items/{item_id}/audit", key=True)
        return [a for a in body if isinstance(a, dict)] if isinstance(body, list) else []

    async def item_versions(self, item_id: int) -> list[dict]:
        """19 GET /items/{id}/versions (keyed): the body as each version was written."""
        body = await self._call("calendar", "GET", f"{self.s.calendar_url}/items/{item_id}/versions", key=True)
        return [v for v in body if isinstance(v, dict)] if isinstance(body, list) else []

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
        """To the approval service (90) when APPROVAL_URL is set, else the n8n webhook: the same
        payload, the same X-Control-Key and the same answer shape either way."""
        if self.s.approval_url:
            name, url = "approval service", f"{self.s.approval_url}/decisions"
            not_json = "answer is not JSON (is the approval service running?)"
        else:
            name, url = "n8n", f"{self.s.n8n_url}/webhook/mkt-apply-decisions"
            not_json = "answer is not JSON (is workflow 72 imported and published?)"
        try:
            r = await self.c.post(url, json={"reviewer": reviewer, "decisions": decisions},
                                  headers={"X-Control-Key": self.s.control_key},
                                  timeout=self.s.decision_timeout_s)
        except httpx.HTTPError as e:
            raise BackendError(name, f"unreachable ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise BackendError(name, _detail(r), r.status_code)
        try:
            body = r.json()
        except ValueError:
            raise BackendError(name, not_json) from None
        if not isinstance(body, dict):
            raise BackendError(name, "unexpected answer")
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
                    timeout: float = 10.0, none_on_404: bool = False, headers: dict | None = None,
                    params=None):
        try:
            h = {**(self._key() if key else {}), **(headers or {})} or None
            r = await self.c.request(method, url, json=json, headers=h, timeout=timeout, params=params)
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

    # ---- 05 brand-service: facts v2, questions, starter kits, rules (contract §2)
    def who(self) -> str:
        """The logged-in person's display name for this request (settings.reviewer outside one)."""
        return (ACTOR.get() or self.s.reviewer or "control room")[:80]

    def _actor(self) -> dict:
        # A header value must be ASCII: accents are folded ("Hénos" -> "Henos"), anything else dropped.
        folded = unicodedata.normalize("NFKD", self.who()).encode("ascii", "ignore").decode()
        clean = "".join(ch for ch in folded if 32 <= ord(ch) < 127).strip()
        return {"X-Actor": clean or (self.s.reviewer or "control room")[:80]}

    def _owner(self) -> dict:
        """X-Owner-Key for the few calls that turn a draft into a fact (or retire one). Refused here,
        before any call, when the control room has no FACT_OWNER_KEY."""
        if not self.s.fact_owner_key:
            raise BackendError("brand", "confirming is switched off: FACT_OWNER_KEY is not set on the control room", 503)
        return {"X-Owner-Key": self.s.fact_owner_key, **self._actor()}

    def _brand(self, method: str, path: str, *, owner: bool = False, **kw):
        headers = self._owner() if owner else self._actor()
        return self._call("brand", method, f"{self.s.brand_url}{path}", key=True, headers=headers, **kw)

    async def facts_v2(self, status: str | None = None) -> dict:
        return await self._brand("GET", "/facts/v2", params={"status": status} if status else None)

    async def fact_v2(self, key: str) -> dict:
        return await self._brand("GET", f"/facts/v2/{key}")

    async def fact_create(self, fact: dict) -> dict:
        return await self._brand("POST", "/facts/v2", json=fact)

    async def fact_update(self, key: str, fact: dict) -> dict:
        return await self._brand("PUT", f"/facts/v2/{key}", json=fact)

    async def fact_confirm(self, key: str) -> dict:
        return await self._brand("POST", f"/facts/v2/{key}/confirm", owner=True)

    async def fact_retire(self, key: str) -> dict:
        return await self._brand("POST", f"/facts/v2/{key}/retire", owner=True)

    async def facts_import(self, facts: list[dict], confirm: bool = False) -> dict:
        return await self._brand("POST", "/facts/v2/import", owner=True, json={"facts": facts, "confirm": confirm},
                                 timeout=30)

    async def facts_query(self, scope: dict | None = None, at: str | None = None,
                          max_sensitivity: str = "internal") -> dict:
        names = {"sites": "site", "regions": "region", "channels": "channel", "segments": "segment",
                 "plan_tiers": "plan_tier", "variants": "variant"}
        params = [(names[d], v) for d, vals in (scope or {}).items() if d in names for v in vals]
        params += [("max_sensitivity", max_sensitivity)] + ([("at", at)] if at else [])
        return await self._brand("GET", "/facts/query", params=params)

    async def questions(self, status: str | None = "open") -> list[dict]:
        body = await self._brand("GET", "/questions", params={"status": status} if status else None)
        qs = body.get("questions") if isinstance(body, dict) else body
        return [q for q in qs if isinstance(q, dict)] if isinstance(qs, list) else []

    async def question_answer(self, qid: int, answer: str | None) -> dict:
        return await self._brand("POST", f"/questions/{qid}/answer", json={"answer": answer or None})

    async def question_create(self, body: dict) -> dict:
        return await self._brand("POST", "/questions", json=body)

    async def question_dismiss(self, qid: int) -> dict:
        return await self._brand("POST", f"/questions/{qid}/dismiss")

    async def starter_kits(self) -> list[dict]:
        body = await self._brand("GET", "/starter-kits")
        return [k for k in body if isinstance(k, dict)] if isinstance(body, list) else []

    async def starter_kit_apply(self, kit_id: str) -> dict:
        return await self._brand("POST", f"/starter-kits/{kit_id}/apply", owner=True)

    async def kit_rules(self, status: str | None = None, kit: str | None = None) -> list[dict]:
        params = {k: v for k, v in {"status": status, "kit": kit}.items() if v}
        body = await self._brand("GET", "/rules", params=params or None)
        rules = body.get("rules") if isinstance(body, dict) else body
        return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []

    async def kit_rule_set(self, rid: int, action: str) -> dict:
        return await self._brand("POST", f"/rules/{rid}/{action}", owner=True)

    async def disclosure_wordings(self, status: str | None = "draft") -> list[dict]:
        """05 GET /disclosure-wordings: wordings people saved when accepting a finding (88)."""
        body = await self._brand("GET", "/disclosure-wordings", params={"status": status} if status else None)
        ws = body.get("wordings") if isinstance(body, dict) else body
        return [w for w in ws if isinstance(w, dict)] if isinstance(ws, list) else []

    async def disclosure_wording_set(self, wid: int, action: str) -> dict:
        return await self._brand("POST", f"/disclosure-wordings/{wid}/{action}", owner=True)

    # ---- onboarding: 07 page-extractor and the 03 gateway's propose_facts prompt
    async def extract(self, body: dict) -> dict:
        """07 POST /extract: {"url"} or {"pdf_base64", "filename"}. 07 is keyless (internal network)."""
        if not self.s.extractor_url:
            raise BackendError("page extractor", "not installed", 404)
        out = await self._call("page extractor", "POST", f"{self.s.extractor_url}/extract", json=body, timeout=90)
        if not isinstance(out, dict):
            raise BackendError("page extractor", "unexpected answer")
        return out

    async def gateway_health(self) -> dict:
        out = await self._call("gateway", "GET", f"{self.s.gateway_url}/health", timeout=4)
        return out if isinstance(out, dict) else {}

    async def gateway_prompts(self) -> set[str]:
        async def load():
            out = await self._call("gateway", "GET", f"{self.s.gateway_url}/v1/prompts", timeout=4)
            return {str(p.get("name")) for p in out if isinstance(p, dict)} if isinstance(out, list) else set()
        return await self._cached("gateway_prompts", 60, load)

    async def propose_facts(self, source_text: str, business_type: str, known_facts: str) -> dict:
        body = await self._call("gateway", "POST", f"{self.s.gateway_url}/v1/run", key=True,
                                json={"prompt": "propose_facts", "vars": {"source_text": source_text,
                                      "business_type": business_type or None, "known_facts": known_facts or None}},
                                headers={"X-Caller": "72 onboarding"}, timeout=self.s.voice_timeout_s)
        out = body.get("output") if isinstance(body, dict) else None
        if not isinstance(out, dict):
            raise BackendError("gateway", "the model did not return facts; try again")
        return out

    # ---- 88 task-bridge (contract §4)
    def _tasks(self, method: str, path: str, **kw):
        if not self.s.tasks_url:
            raise BackendError("task bridge", "not installed", 404)
        return self._call("task bridge", method, f"{self.s.tasks_url}{path}", key=True, **kw)

    async def tasks(self) -> list[dict]:
        body = await self._tasks("GET", "/tasks")
        ts = body.get("tasks") if isinstance(body, dict) else body
        return [t for t in ts if isinstance(t, dict)] if isinstance(ts, list) else []

    async def occasions(self, days: int = 45) -> dict:
        """88 GET /occasions: holidays/seasons near today (empty when OCCASIONS is off). Never raises:
        the New task page works without it."""
        try:
            body = await self._tasks("GET", "/occasions", params={"days": days})
        except BackendError:
            return {}
        return body if isinstance(body, dict) else {}

    async def task_list(self, limit: int) -> list[dict]:
        """The newest `limit` tasks (88 caps at 500), for the Results page."""
        body = await self._tasks("GET", "/tasks", params={"limit": limit})
        ts = body.get("tasks") if isinstance(body, dict) else body
        return [t for t in ts if isinstance(t, dict)] if isinstance(ts, list) else []

    async def task(self, task_id: str) -> dict:
        return await self._tasks("GET", f"/tasks/{task_id}")

    async def task_create(self, body: dict) -> dict:
        return await self._tasks("POST", "/tasks", json=body, timeout=60)

    async def task_pack(self, task_id: str) -> dict:
        return await self._tasks("GET", f"/tasks/{task_id}/pack")

    async def task_paste(self, task_id: str, text: str, provider: str) -> dict:
        return await self._tasks("POST", f"/tasks/{task_id}/paste", json={"text": text, "provider": provider}, timeout=30)

    async def task_split(self, task_id: str, draft_id: int, pieces: list[dict]) -> dict:
        return await self._tasks("POST", f"/tasks/{task_id}/drafts/{draft_id}/split", json={"pieces": pieces}, timeout=30)

    async def task_accept(self, task_id: str, piece_key: str, finding: int, sha256: str, by: str, note: str,
                          wording: str | None = None) -> dict:
        """A person says a missing-disclosure finding is wrong ("it's there, in other words").
        `wording`: the exact words from the piece, saved (as a draft for the owner) for next time."""
        body = {"finding": finding, "expected_sha256": sha256, "by": by, "note": note}
        if wording:
            body["wording"] = wording
        return await self._tasks("POST", f"/tasks/{task_id}/pieces/{piece_key}/accept", timeout=30, json=body)

    async def task_submit(self, task_id: str, draft_id: int) -> dict | list:
        return await self._tasks("POST", f"/tasks/{task_id}/submit", json={"draft_id": draft_id}, timeout=180)

    async def task_export(self, task_id: str, fmt: str) -> tuple[bytes, str]:
        """The approved pack as one file; 88 refuses (409) until every piece is approved and valid."""
        if not self.s.tasks_url:
            raise BackendError("task bridge", "not installed", 404)
        try:
            r = await self.c.get(f"{self.s.tasks_url}/tasks/{task_id}/export", params={"format": fmt},
                                 headers=self._key(), timeout=30)
        except httpx.HTTPError as e:
            raise BackendError("task bridge", f"unreachable ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise BackendError("task bridge", _detail(r), r.status_code)
        return r.content, r.headers.get("content-type", "text/plain")

    async def blockers(self, timeout: float = 10.0) -> list[dict]:
        body = await self._tasks("GET", "/blockers", timeout=timeout)
        bs = body.get("blockers") if isinstance(body, dict) else body
        return [b for b in bs if isinstance(b, dict)] if isinstance(bs, list) else []

    async def reconcile(self) -> dict:
        return await self._tasks("POST", "/reconcile", timeout=60)

    # ---- 19 content-calendar: client approval links (Phase 2). The token and PIN travel only here.
    def _links(self, method: str, path: str, **kw):
        return self._call("calendar", method, f"{self.s.calendar_url}{path}", key=True, **kw)

    async def client_link_create(self, item_ids: list[int], label: str, days: int, pin: str) -> dict:
        return await self._links("POST", "/client-links", headers=self._actor(),
                                 json={"item_ids": item_ids, "label": label, "days": days, "pin": pin})

    async def client_links(self) -> list[dict]:
        body = await self._links("GET", "/client-links")
        return [x for x in body if isinstance(x, dict)] if isinstance(body, list) else []

    async def client_link_revoke(self, link_id: int) -> dict:
        return await self._links("POST", f"/client-links/{link_id}/revoke", headers=self._actor())

    async def client_resolve(self, token: str, pin: str) -> dict:
        return await self._links("POST", "/client-links/resolve", json={"token": token, "pin": pin})

    async def client_respond(self, body: dict) -> dict:
        return await self._links("POST", "/client-links/respond", json=body)


async def gather_soft(*coros):
    """Run calls in parallel; a failed one becomes its BackendError instead of failing the page."""
    out = await asyncio.gather(*coros, return_exceptions=True)
    for o in out:
        if isinstance(o, BaseException) and not isinstance(o, BackendError):
            raise o
    return out
