"""The MCP server: seven tools over the brand service (05) and the task bridge (88).

Claude can read public facts, make a task pack, submit its own answer for checking, check any text,
and look at a task or the blockers list. It cannot approve, publish, confirm or retire anything:
there is no tool for it, the upstream allowlist has no such path, and the connector holds no key
that would allow it. Every text a tool returns has internal and restricted values replaced with
their slot (see redact.py).
"""
import hashlib
import logging
import threading
import time
from collections import deque
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import upstream
from .config import Config
from .redact import Redactor

log = logging.getLogger("mcp_connector")

TOOL_NAMES = ("get_business_facts", "make_task_pack", "submit_answer", "submit_split", "check_text", "get_task", "get_occasions", "render_template", "post_from_template", "make_quote", "audit_content",
              "list_blockers")
HUMAN_ONLY = ("A person must approve this in the control room; you cannot approve or publish. "
              "Nothing is posted anywhere until a person approves it and posts it.")

INSTRUCTIONS = """Marketing facts and task bridge for one business.

Use it like this:
1. get_business_facts: the public facts you may use (prices, hours, policies...) valid on a date and
   for a site/region/channel. Use only these facts; never invent prices, dates or claims.
2. make_task_pack: start a task (goal, pieces, publish date). Read the pack it returns and follow it:
   write every piece under its === N CHANNEL === marker and write facts as their [[slot]].
3. submit_answer: send your whole answer. Each piece is checked sentence by sentence against the
   facts and placed in the review queue. Fix blocked pieces and submit again.
4. check_text: check any text without creating anything.

You cannot approve, publish, confirm facts or change facts. A person does that in the control room.
Internal facts appear only as [[slots]]; their values are never shown to you."""

TaskId = Annotated[str, Field(pattern=r"^T-[A-Z2-7]{6}$", description="Task id, like T-ABC234")]
Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="A date, YYYY-MM-DD")]
ScopeValue = Annotated[str, Field(min_length=1, max_length=80)]


class Scope(BaseModel):
    """Where the copy will be used. Leave a list empty when it does not matter."""
    model_config = ConfigDict(extra="forbid")
    sites: list[ScopeValue] = Field(default_factory=list, max_length=20, description="Sites or locations")
    regions: list[ScopeValue] = Field(default_factory=list, max_length=20, description="Regions or countries")
    channels: list[ScopeValue] = Field(default_factory=list, max_length=20, description="Channels")
    segments: list[ScopeValue] = Field(default_factory=list, max_length=20, description="Customer segments")
    plan_tiers: list[ScopeValue] = Field(default_factory=list, max_length=20, description="Plan tiers")
    variants: list[ScopeValue] = Field(default_factory=list, max_length=20, description="Product variants")


class PieceSpec(BaseModel):
    """One piece of copy to write."""
    model_config = ConfigDict(extra="forbid")
    channel: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9 _-]+$",
                         description="linkedin, instagram, x, facebook, email, blog, website, sms...")
    kind: str | None = Field(default=None, max_length=40, description="post, caption, ad, email, article...")
    max_chars: int | None = Field(default=None, ge=1, le=100_000, description="Length limit, if any")


class SplitPiece(BaseModel):
    """The text of one piece, for a split made by hand."""
    model_config = ConfigDict(extra="forbid")
    piece_key: str = Field(pattern=r"^[a-z0-9_-]{1,20}$", description="p1, p2... from make_task_pack")
    text: str = Field(min_length=1, max_length=200_000)


class Limiter:
    """At most `per_min` tool calls in any 60 seconds (all transports, all callers together)."""

    def __init__(self, per_min: int):
        self.per_min = per_min
        self._hits: deque = deque()
        self._lock = threading.Lock()

    def hit(self):
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > 60:
                self._hits.popleft()
            if len(self._hits) >= self.per_min:
                raise ToolError("too many tool calls in the last minute; wait a minute and try again")
            self._hits.append(now)


def _day(v: str | None, name: str) -> str | None:
    if v is None:
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise ToolError(f"{name}: {v!r} is not a real date (YYYY-MM-DD)") from None


def _sha8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _fail(exc: upstream.UpstreamError) -> ToolError:
    d = exc.detail
    if isinstance(d, dict):
        msg = str(d.get("message") or d)
        extra = [f"{k}: {v}" for k, v in d.items() if k in ("problems", "missing", "reasons", "piece_key")]
        return ToolError("; ".join([msg] + extra)[:1500])
    if isinstance(d, list):  # FastAPI validation errors: field and message only, never the input
        parts = [f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg')}" for e in d if isinstance(e, dict)]
        return ToolError(("rejected: " + "; ".join(parts))[:1500])
    if exc.status in (404, 409, 413, 422) and d:
        return ToolError(f"{d}"[:1500])
    return ToolError(str(exc))


def build_server(cfg: Config) -> MCPServer:
    mcp = MCPServer("marketing-facts", title="Marketing facts and task bridge", instructions=INSTRUCTIONS,
                    version="1.0.0", log_level="WARNING")
    limiter = Limiter(cfg.tool_calls_per_min)
    max_text = cfg.max_text_chars
    Text = Annotated[str, Field(min_length=1, max_length=max_text,
                                description=f"Plain text, at most {max_text} characters")]

    async def up(service, method, path, **kw):
        try:
            return await upstream.call(cfg, service, method, path, **kw)
        except upstream.UpstreamError as exc:
            raise _fail(exc) from None

    async def redactor() -> Redactor:
        """Which facts are not public. Fail closed: no redactor, no text back."""
        out = await up("brand", "GET", "/facts/v2")
        facts = out.get("facts") if isinstance(out, dict) else None
        if not isinstance(facts, list):
            raise ToolError("cannot tell which facts are internal right now, so no checked text is shown; try again")
        return Redactor.from_facts(facts)

    def rules(checks: dict | None, r: Redactor) -> list[dict]:
        out = []
        for source in ("brand", "platform"):
            for v in (checks or {}).get(source) or []:
                if isinstance(v, dict):
                    out.append({"source": source, "rule": v.get("rule"), "severity": v.get("severity"),
                                "detail": r.text(v.get("detail"))})
        return out

    def pieces_out(results: list[dict], r: Redactor) -> list[dict]:
        out = []
        for p in results:
            findings = [r.finding(x) for x in p.get("findings") or [] if isinstance(x, dict)]
            out.append({
                "piece_key": p.get("piece_key"),
                "blocked": bool(p.get("blocked")),
                "status": p.get("status") or p.get("state"),
                "calendar_item_id": p.get("calendar_item_id"),
                "findings": findings,
                "blocking_findings": sum(1 for x in findings if x["blocking"]),
                "rule_problems": rules(p.get("checks"), r),
                "notes": [r.text(n) for n in p.get("notes") or [] if isinstance(n, str)],
                "text_with_internal_values_hidden": r.text(p.get("filled_text")),
            })
        return out

    async def submit(task_id: str, draft_id: int, r: Redactor) -> dict:
        res = await up("tasks", "POST", f"/tasks/{task_id}/submit", json={"draft_id": draft_id})
        pieces = pieces_out(res.get("pieces") or [], r)
        blocked = [p["piece_key"] for p in pieces if p["blocked"]]
        log.info("submit task=%s draft=%s pieces=%d blocked=%d", task_id, draft_id, len(pieces), len(blocked))
        if blocked:
            summary = (f"{len(blocked)} of {len(pieces)} pieces are blocked ({', '.join(blocked)}): they stay drafts. "
                       "Fix the blocking findings and call submit_answer again with the whole answer.")
        else:
            summary = f"All {len(pieces)} pieces passed the checks and are waiting for review."
        return {"task_id": task_id, "draft_id": draft_id, "summary": summary, "message": HUMAN_ONLY,
                "pieces": pieces}

    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    rw = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

    @mcp.tool(annotations=ro)
    async def get_business_facts(
        site: Annotated[ScopeValue | None, Field(description="Site or location, if the copy is for one")] = None,
        region: Annotated[ScopeValue | None, Field(description="Region or country")] = None,
        channel: Annotated[ScopeValue | None, Field(description="Channel, e.g. linkedin")] = None,
        segment: Annotated[ScopeValue | None, Field(description="Customer segment")] = None,
        plan_tier: Annotated[ScopeValue | None, Field(description="Plan tier")] = None,
        variant: Annotated[ScopeValue | None, Field(description="Product variant")] = None,
        on_date: Annotated[Day | None, Field(description="Date the copy will be published (default today)")] = None,
    ) -> dict[str, Any]:
        """The business's PUBLIC facts that are valid on a date and for this site/region/channel/segment/plan/variant.

        Use only these facts in copy. Each fact has its text, the exact value text to write, the disclosures that
        must appear with it, where it applies and the dates it is valid. `excluded` lists facts you must not use,
        with the reason (expired, out_of_scope, internal...); internal and restricted values are never shown."""
        limiter.hit()
        params: dict[str, Any] = {"max_sensitivity": "public"}
        for name, v in (("site", site), ("region", region), ("channel", channel), ("segment", segment),
                        ("plan_tier", plan_tier), ("variant", variant)):
            if v:
                params[name] = [v]
        if on_date:
            params["at"] = _day(on_date, "on_date")
        out = await up("brand", "GET", "/facts/query", params=params)
        facts, excluded = [], []
        for f in out.get("facts") or []:
            if not isinstance(f, dict) or not isinstance(f.get("key"), str):
                continue
            if (f.get("sensitivity") or "").lower() != "public":  # never trust the filter alone
                excluded.append({"key": f["key"], "reason": (f.get("sensitivity") or "not_public")})
                continue
            facts.append({"key": f["key"], "slot": f"[[{f['key']}]]", "text": f.get("text"),
                          "value_text": f.get("value_text"),
                          "required_disclosures": f.get("required_disclosures") or [],
                          "forbidden_phrasing": f.get("forbidden_phrasing") or [],
                          "scope": f.get("scope") or {}, "valid_from": f.get("valid_from"),
                          "valid_to": f.get("valid_to")})
        for e in out.get("excluded") or []:
            if isinstance(e, dict) and isinstance(e.get("key"), str):
                excluded.append({"key": e["key"], "reason": str(e.get("reason") or "excluded")})
        log.info("get_business_facts facts=%d excluded=%d", len(facts), len(excluded))
        result = {"at": out.get("at"), "fact_set_version": out.get("fact_set_version"), "facts": facts,
                  "excluded": excluded}
        unspecified = [e for e in excluded if e["reason"] == "scope_unspecified"]
        if unspecified and len(unspecified) * 2 >= len(excluded) + len(facts):
            # Most facts are for a named site/region/...: say which values to pass (scope values only,
            # never a fact's value), so a single-site business doesn't look empty.
            try:
                every = await up("brand", "GET", "/facts/v2")
                dims: dict[str, set] = {}
                for f in every.get("facts") or []:
                    if isinstance(f, dict) and f.get("status") == "active":
                        for dim, vals in (f.get("scope") or {}).items():
                            for v in vals or []:
                                dims.setdefault(dim, set()).add(str(v)[:80])
                arg = {"sites": "site", "regions": "region", "channels": "channel", "segments": "segment",
                       "plan_tiers": "plan_tier", "variants": "variant"}
                if dims:
                    result["hint"] = ("Most facts apply to a named " + ", ".join(arg.get(d, d) for d in sorted(dims))
                                      + ". Call again with one of: " + "; ".join(
                                          f"{arg.get(d, d)}={sorted(v)[:10]}" for d, v in sorted(dims.items())))
            except upstream.UpstreamError:
                pass
        return result

    @mcp.tool(annotations=rw)
    async def make_task_pack(
        goal: Annotated[str, Field(min_length=3, max_length=2000, description="What the copy is for")],
        pieces: Annotated[list[PieceSpec], Field(min_length=1, max_length=10, description="The pieces to write")],
        publish_on: Annotated[Day, Field(description="Date the copy will be published, YYYY-MM-DD")],
        scope: Annotated[Scope | None, Field(description="Where the copy applies (sites, regions...)")] = None,
        audience: Annotated[str | None, Field(max_length=500, description="Who it is for")] = None,
    ) -> dict[str, Any]:
        """Start a copy task. Returns the task id, the task pack (instructions plus the facts valid on the publish
        date for this scope) and a preview of exactly which facts were shared and which were held back.

        Next: write every piece following the pack (under its === N CHANNEL === marker, facts as [[slots]]), then
        call submit_answer with the task id and your whole answer."""
        limiter.hit()
        body = {"goal": goal, "publish_on": _day(publish_on, "publish_on"),
                "pieces": [{"key": f"p{i}", "channel": p.channel.strip().lower(), "kind": p.kind,
                            "max_chars": p.max_chars} for i, p in enumerate(pieces, 1)],
                "scope": (scope or Scope()).model_dump(), "audience": audience}
        out = await up("tasks", "POST", "/tasks", json=body)
        log.info("make_task_pack task=%s pieces=%d pack_chars=%d", out.get("id"), len(pieces), len(out.get("pack") or ""))
        return {"task_id": out.get("id"), "publish_on": body["publish_on"],
                "fact_set_version": out.get("fact_set_version"),
                "pieces": [{"piece_key": b["key"], "channel": b["channel"], "kind": b["kind"]} for b in body["pieces"]],
                "pack": out.get("pack"), "share_preview": out.get("share_preview"),
                "notes": out.get("notes") or [],
                "next_step": "Write the pieces as the pack asks, then call submit_answer(task_id, text)."}

    @mcp.tool(annotations=rw)
    async def submit_answer(
        task_id: TaskId,
        text: Annotated[str, Field(min_length=1, max_length=max_text,
                                   description="Your whole answer, all pieces, with the === N CHANNEL === markers")],
        provider: Literal["claude", "chatgpt", "gemini", "other", "self"] = "claude",
    ) -> dict[str, Any]:
        """Submit your answer to a task for checking. It is split into pieces at the markers; every sentence is
        checked against the facts; each piece goes to the review queue (blocked pieces stay drafts).

        Returns per piece: blocked or not, findings (label, sentence, quote from the fact, why), rule problems and
        the calendar item id. If the split into pieces has problems, nothing is checked: the problems and a draft
        id come back, and you call submit_split. You cannot approve or publish; a person does that."""
        limiter.hit()
        r = await redactor()
        pasted = await up("tasks", "POST", f"/tasks/{task_id}/paste", json={"text": text, "provider": provider})
        draft_id = pasted.get("draft_id")
        problems = pasted.get("problems") or []
        log.info("submit_answer task=%s chars=%d sha=%s draft=%s problems=%d", task_id, len(text), _sha8(text),
                 draft_id, len(problems))
        if problems:
            return {"task_id": task_id, "draft_id": draft_id, "status": "split_has_problems", "submitted": False,
                    "problems": [r.text(str(p)) for p in problems],
                    "split": [{"piece_key": s.get("piece_key"), "chars": len(s.get("text") or ""),
                               "starts_with": r.text((s.get("text") or "")[:120])}
                              for s in pasted.get("split") or [] if isinstance(s, dict)],
                    "next_step": "Nothing was checked. Call submit_split(task_id, draft_id, pieces=[{piece_key, "
                                 "text}, ...]) with every piece's text, or fix the markers and call submit_answer "
                                 "again.", "message": HUMAN_ONLY}
        return await submit(task_id, int(draft_id), r)

    @mcp.tool(annotations=rw)
    async def submit_split(
        task_id: TaskId,
        draft_id: Annotated[int, Field(ge=1, description="The draft id submit_answer returned")],
        pieces: Annotated[list[SplitPiece], Field(min_length=1, max_length=10,
                                                  description="Every piece of the task exactly once")],
    ) -> dict[str, Any]:
        """Split an answer into pieces by hand (when submit_answer reported split problems), then check and submit
        it. Give every piece of the task exactly once. Returns the same per-piece results as submit_answer."""
        limiter.hit()
        total = sum(len(p.text) for p in pieces)
        if total > max_text:
            raise ToolError(f"the pieces are longer than {max_text} characters together")
        r = await redactor()
        out = await up("tasks", "POST", f"/tasks/{task_id}/drafts/{draft_id}/split",
                       json={"pieces": [p.model_dump() for p in pieces]})
        log.info("submit_split task=%s from_draft=%s chars=%d new_draft=%s", task_id, draft_id, total, out.get("draft_id"))
        return await submit(task_id, int(out["draft_id"]), r)

    @mcp.tool(annotations=ro)
    async def check_text(
        text: Text,
        publish_on: Annotated[Day, Field(description="Date the text will be published, YYYY-MM-DD")],
        scope: Annotated[Scope | None, Field(description="Where the text applies")] = None,
        channel: Annotated[str | None, Field(max_length=30, description="Channel, e.g. linkedin")] = None,
    ) -> dict[str, Any]:
        """Check any text against the facts valid on the publish date for this scope, sentence by sentence.
        Nothing is created or queued. Returns whether it would be blocked and each finding with its label, the
        sentence, a quote from the fact and why."""
        limiter.hit()
        r = await redactor()
        body = {"text": text, "publish_on": _day(publish_on, "publish_on"), "scope": (scope or Scope()).model_dump(),
                "channel": channel}
        out = await up("tasks", "POST", "/check", json=body)
        findings = [r.finding(x) for x in out.get("findings") or [] if isinstance(x, dict)]
        log.info("check_text chars=%d sha=%s findings=%d", len(text), _sha8(text), len(findings))
        return {"blocked": bool(out.get("blocked")), "findings": findings,
                "brand_and_channel_rules": rules(out.get("checks"), r),
                "text_with_internal_values_hidden": r.text(out.get("filled_text"))}

    @mcp.tool(annotations=ro)
    async def get_task(task_id: TaskId) -> dict[str, Any]:
        """A task's state: its pieces (review status, blocked or not, findings, calendar item id), drafts, the pack
        and whether it is ready to export (a person approves and exports in the control room)."""
        limiter.hit()
        r = await redactor()
        t = await up("tasks", "GET", f"/tasks/{task_id}")
        pieces = pieces_out(t.get("piece_state") or [], r)
        for p, raw in zip(pieces, t.get("piece_state") or []):
            p["channel"] = raw.get("channel")
            p["stale_facts"] = raw.get("stale_facts") or []
        export = t.get("export") if isinstance(t.get("export"), dict) else {}
        return {"task_id": t.get("id"), "goal": t.get("goal"), "status": t.get("status"),
                "publish_on": t.get("publish_on"), "scope": t.get("scope"), "fact_set_version": t.get("fact_set_version"),
                "pieces": pieces,
                "drafts": [{k: d.get(k) for k in ("draft_id", "kind", "provider", "chars", "problems", "created_at")}
                           for d in t.get("drafts") or [] if isinstance(d, dict)],
                "export": {k: (r.text(v) if isinstance(v, str) else [r.text(x) for x in v] if isinstance(v, list) else v)
                           for k, v in export.items()},
                "share_preview": t.get("share_preview"), "pack": t.get("pack"), "message": HUMAN_ONLY}

    TemplateName = Literal["price_list", "rate_card", "facts_digest"]

    @mcp.tool(annotations=ro)
    async def render_template(
        template: Annotated[TemplateName, Field(description="price_list, rate_card or facts_digest")],
        channel: Annotated[str, Field(min_length=1, max_length=30, description="telegram, whatsapp, sms, facebook...")],
        publish_on: Annotated[Day, Field(description="Publish date, YYYY-MM-DD")],
        scope: Annotated[Scope | None, Field(description="Site, region, etc.")] = None,
        title: Annotated[str | None, Field(max_length=120)] = None,
        intro: Annotated[str | None, Field(max_length=400)] = None,
    ) -> dict[str, Any]:
        """Write a repetitive post (a price list, a rate card, a facts digest) straight from the business's
        current public facts, with zero model tokens: values, shared disclosures, business rules (e.g. a legal
        warning) and channel limits are applied for you. Nothing is saved. Prefer this over writing such lists
        yourself."""
        limiter.hit()
        body = {"template": template, "channel": channel, "publish_on": _day(publish_on, "publish_on"),
                "scope": (scope or Scope()).model_dump(), "title": title, "intro": intro}
        return await up("tasks", "POST", "/templates/render", json={k: v for k, v in body.items() if v is not None})

    @mcp.tool(annotations=rw)
    async def post_from_template(
        template: Annotated[TemplateName, Field(description="price_list, rate_card or facts_digest")],
        channels: Annotated[list[str], Field(min_length=1, max_length=5, description="Channels, one piece each")],
        publish_on: Annotated[Day, Field(description="Publish date, YYYY-MM-DD")],
        scope: Annotated[Scope | None, Field(description="Site, region, etc.")] = None,
        title: Annotated[str | None, Field(max_length=120)] = None,
        intro: Annotated[str | None, Field(max_length=400)] = None,
    ) -> dict[str, Any]:
        """Render a template post and send it for checks and human review in one step (it lands in the
        approval queue; nothing is published)."""
        limiter.hit()
        body = {"template": template, "channels": channels, "publish_on": _day(publish_on, "publish_on"),
                "scope": (scope or Scope()).model_dump(), "title": title, "intro": intro}
        out = await up("tasks", "POST", "/templates/task", json={k: v for k, v in body.items() if v is not None})
        out = dict(out) if isinstance(out, dict) else {"result": out}
        out["message"] = HUMAN_ONLY
        return out

    @mcp.tool(annotations=ro)
    async def make_quote(
        lines: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=50,
                         description='[{"fact_key": "...", "quantity": 20, "nights": 3}] using public price facts')],
        publish_on: Annotated[Day, Field(description="Date the quote is for, YYYY-MM-DD")],
        scope: Annotated[Scope | None, Field(description="Site, region, etc.")] = None,
    ) -> dict[str, Any]:
        """Exact quote from the business's PUBLIC prices (quantity × nights × price, totals, disclosures).
        Use it instead of doing the arithmetic yourself. Internal prices are never available here."""
        limiter.hit()
        clean = []
        for ln in lines:
            if not isinstance(ln, dict) or not isinstance(ln.get("fact_key"), str):
                raise ToolError("each line needs a fact_key and a quantity")
            clean.append({k: ln[k] for k in ("fact_key", "quantity", "nights", "guests") if k in ln})
        body = {"lines": clean, "publish_on": _day(publish_on, "publish_on"), "scope": (scope or Scope()).model_dump()}
        return await up("tasks", "POST", "/quote", json=body)

    @mcp.tool(annotations=ro)
    async def audit_content(
        urls: Annotated[list[str] | None, Field(max_length=10, description="Public pages or PDFs of the business")] = None,
        text: Annotated[str | None, Field(max_length=40_000, description="Or paste old posts / page text")] = None,
        scope: Annotated[Scope | None, Field(description="Site, region, etc.")] = None,
    ) -> dict[str, Any]:
        """Check what the business's own pages, PDFs or old posts say against today's facts: lists only
        contradictions (old prices, expired offers, another branch's facts). Nothing is changed."""
        limiter.hit()
        sources = [{"url": u} for u in (urls or []) if isinstance(u, str) and u.strip()]
        if text and text.strip():
            sources.append({"text": text, "label": "pasted"})
        if not sources:
            raise ToolError("give urls or text")
        return await up("tasks", "POST", "/audit", json={"sources": sources[:10], "scope": (scope or Scope()).model_dump()})

    @mcp.tool(annotations=ro)
    async def get_occasions(
        on_date: Annotated[Day | None, Field(description="Publish date to look around, YYYY-MM-DD (default today)")] = None,
        days: Annotated[int, Field(ge=1, le=366, description="How many days ahead")] = 45,
    ) -> dict[str, Any]:
        """Holidays, seasons and fasts near a publish date, with the local calendar date and notes for
        marketers (e.g. alcohol brands may not sponsor holidays in Ethiopia). Use it before planning a
        campaign. Movable or unverified dates say so."""
        limiter.hit()
        params: dict[str, Any] = {"days": days}
        if on_date:
            params["on"] = _day(on_date, "on_date")
        return await up("tasks", "GET", "/occasions", params=params)

    @mcp.tool(annotations=ro)
    async def list_blockers() -> dict[str, Any]:
        """What is stopping copy from going out: facts nobody has confirmed, blocked slots, facts that changed or
        expired since copy was checked, facts past their review date. Only a person can fix most of these."""
        limiter.hit()
        out = await up("tasks", "GET", "/blockers")
        items = [{k: b.get(k) for k in ("kind", "count", "text", "link")} for b in out or [] if isinstance(b, dict)]
        return {"blockers": items, "none": not items}

    return mcp
