"""Activity page: what the agent is doing now, what it did, which models it used, what it can do.

Server-side aggregation, read-only. Sources:
- every gateway (GATEWAY_URL = "main", plus ACTIVITY_GATEWAYS): 03 `GET /v1/activity` (metadata
  only: no prompt text, vars or output ever reach this page);
- the calendar (19) `GET /items`: items created and status changes in the last ACTIVITY_DAYS days.
A source that fails is listed as "unreachable" and the rest of the page still works. Gateway URLs
never reach the browser: a gateway is shown by its label.
"""
import asyncio
import ipaddress
import re
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse

from .backends import BackendError

TIMELINE_MAX = 150
FILTERS = ("all", "ai", "content", "errors")
PROFILE_RANK = {"core": 0, "growth": 1, "full": 2}
_TRANSITION = re.compile(r"^\[(\d{4}-\d\d-\d\dT[\d:.]+Z)\]\s+(\w+)\s*->\s*(\w+)")
_CALLER_NUM = re.compile(r"^(\d{2})\b\s*(.*)$")

# Everything the agent can do. (num, name, one sentence, kind, profile, settings attr that must be
# non-empty or None). num is the deploy number; a gateway call whose X-Caller starts with it
# ("26 Social post writer") counts as that ability's activity.
ABILITIES = [
    (24, "Chat agent", "Talks with you in n8n and calls the other tools for you.", "Chat", "core", None),
    (25, "Blog writer", "Writes a blog post draft from a topic and keywords.", "Writer", "core", None),
    (26, "Social post writer", "Writes posts for each social network in your brand voice.", "Writer", "core", None),
    (27, "Ad copy", "Writes ad headlines and descriptions.", "Writer", "core", None),
    (28, "Email newsletter", "Writes a newsletter email.", "Writer", "core", None),
    (29, "SEO brief", "Plans an article around a search keyword.", "Writer", "core", None),
    (30, "Repurpose content", "Turns one piece into posts for other channels.", "Writer", "core", None),
    (31, "Research a URL", "Reads a web page and sums it up.", "Writer", "core", None),
    (32, "Keyword research", "Suggests keywords people search for.", "Writer", "core", None),
    (34, "Knowledge base answer", "Answers a question from your own documents.", "Writer", "core", None),
    (35, "Quality gate", "Checks a draft for brand rules and made-up claims.", "Check", "core", None),
    (47, "Plan a campaign", "Plans a campaign with goals and channels.", "Writer", "core", None),
    (57, "Content formats", "Writes threads, carousels and video scripts.", "Writer", "core", None),
    (64, "Content engine", "Plans a month of content from one pillar.", "Writer", "growth", "engine_url"),
    (77, "Clips from a video", "Finds the best short clips in a long video.", "Writer", "growth", "clips_url"),
    (81, "Track a competitor", "Adds a competitor to watch.", "Writer", "full", "ad_library_url"),
    (36, "Morning trend digest", "Each morning: new posts in your feeds, summed up.", "Scheduled", "core", None),
    (37, "Competitor watch", "Checks competitor pages for changes.", "Scheduled", "core", None),
    (39, "Publisher", "Publishes approved items when their time comes.", "Scheduled", "core", None),
    (40, "Weekly content planner", "Plans next week's posts.", "Scheduled", "core", None),
    (41, "Weekly KPI report", "Sums up last week's numbers.", "Scheduled", "core", None),
    (48, "Campaign drafter", "Drafts every piece a planned campaign needs.", "Scheduled", "core", None),
    (49, "Revise a rejected draft", "Rewrites a draft you rejected, using your reason.", "Scheduled", "core", None),
    (50, "Learn from reviews", "Turns your edits into rules for next time.", "Scheduled", "core", None),
    (52, "Measure campaigns", "Measures running campaigns against their goals.", "Scheduled", "core", None),
    (59, "Winner recycler", "Brings back posts that did well.", "Scheduled", "core", None),
    (60, "Review replies", "Drafts replies to new customer reviews.", "Scheduled", "growth", None),
    (65, "Engine drafter", "Drafts the content engine's planned posts.", "Scheduled", "growth", "engine_url"),
    (66, "Weekly newsletter", "Drafts the weekly newsletter.", "Scheduled", "core", None),
    (68, "Content refresh", "Finds old pages worth updating.", "Scheduled", "growth", None),
    (69, "SEO opportunities", "Finds searches you almost rank for.", "Scheduled", "growth", None),
    (74, "Experiment manager", "Runs A/B tests on posts.", "Scheduled", "growth", None),
    (83, "AI visibility", "Checks if AI assistants mention you.", "Scheduled", "full", None),
    (85, "Monthly client report", "Writes a monthly report you can forward.", "Scheduled", "core", None),
    (44, "Claim checker", "Checks every claim in a draft against your facts.", "Check", "core", None),
    (46, "Learning service", "Keeps the rules learned from your edits.", "Check", "core", None),
    (72, "Voice interview", "Turns your answers into a brand voice profile.", "Check", "core", None),
    (70, "Customer language", "Finds the words your customers use.", "Service", "growth", None),
    (71, "Video maker", "Renders short videos from scripts.", "Service", "growth", "video_url"),
    (73, "Clip finder", "Scores moments in a video for clips.", "Service", "growth", "clips_url"),
    (78, "Positioning map", "Maps what competitors claim, with quotes.", "Service", "full", "ad_library_url"),
    (79, "Site assistant", "Answers visitors' questions on your site.", "Service", "full", None),
    (80, "Lead hub", "Scores and enriches new leads.", "Service", "full", None),
    (84, "Paid ads reports", "Reads ad results and warns about pacing.", "Service", "growth", "ads_url"),
    (86, "Email flows", "Drafts and sends welcome and follow-up emails (dry run).", "Service", "growth", None),
    (87, "Product feed", "Writes better product titles for your shop feed.", "Service", "full", "feed_url"),
]
BY_NUM = {a[0]: a for a in ABILITIES}

VERB = {"approved": "You approved", "rejected": "You rejected", "published": "Published",
        "in_review": "Sent for review:", "draft": "Back to draft:", "idea": "Back to idea:"}
CHANNEL = {"linkedin": "LinkedIn", "x": "X", "instagram": "Instagram", "facebook": "Facebook",
           "threads": "Threads", "tiktok": "TikTok", "youtube": "YouTube", "mastodon": "Mastodon",
           "bluesky": "Bluesky", "blog": "the blog", "email": "email", "newsletter": "the newsletter",
           "video": "video"}
ICON = {"ai": "✦", "created": "✎", "approved": "✓", "rejected": "✕", "published": "↗",
        "in_review": "◔", "draft": "↺", "idea": "·", "error": "!"}


# ------------------------------------------------------------------ pure helpers (tested directly)

def is_local(provider: str) -> bool:
    """"ollama", or a hosted base URL's host that is on this machine or a private network."""
    p = str(provider or "").lower()
    if p in ("ollama", "localhost", "host.docker.internal") or p.endswith((".local", ".internal", ".localhost")):
        return True
    if "." not in p:
        return True     # a container name
    try:
        ip = ipaddress.ip_address(p)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def ability_for_caller(caller: str) -> tuple[int | None, str]:
    """"26 Social post writer" -> (26, "Social post writer"). Unknown number -> (None, the text)."""
    c = str(caller or "").strip()
    m = _CALLER_NUM.match(c)
    if m and int(m.group(1)) in BY_NUM:
        return int(m.group(1)), BY_NUM[int(m.group(1))][1]
    if not c or c == "unknown":
        return None, "Unknown caller"
    return None, (m.group(2) if m and m.group(2) else c)


def seconds(ms) -> str:
    if ms is None:
        return "–"
    s = ms / 1000
    return f"{s:.1f} s" if s < 60 else f"{int(s // 60)} min {int(s % 60)} s"


def prompt_words(prompt: str) -> str:
    return str(prompt or "").replace("_", " ")


def ai_event(row: dict, gateway: str) -> dict:
    """One finished gateway call as a timeline row with one plain sentence."""
    num, who = ability_for_caller(row.get("caller"))
    where = "local" if is_local(row.get("provider")) else "hosted"
    ok = bool(row.get("ok"))
    tail = f"{seconds(row.get('duration_ms'))}, " + ("OK" if ok else f"failed ({str(row.get('error') or 'error').replace('_', ' ')})")
    if row.get("retries"):
        tail += f", {row['retries']} retr{'y' if row['retries'] == 1 else 'ies'}"
    return {"id": f"g-{gateway}-{row.get('id')}", "at": row.get("finished_at") or row.get("started_at"),
            "kind": "ai", "ok": ok, "icon": ICON["ai"] if ok else ICON["error"], "ability": num,
            "text": f"{who} asked {row.get('model')} ({where}) for {prompt_words(row.get('prompt'))} — {tail}",
            "link": None, "gateway": gateway}


def _parse(ts) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def content_events(items: list[dict], since: datetime) -> list[dict]:
    """Items created and status changes (from 19's notes) since `since`, as timeline rows."""
    out = []
    for it in items:
        iid, title = it.get("id"), str(it.get("title") or f"item {it.get('id')}")[:120]
        raw = str(it.get("channel") or "").strip()
        ch = CHANNEL.get(raw.lower(), raw)[:40]
        on = f" for {ch}" if ch else ""
        link = f"/items/{iid}" if isinstance(iid, int) else None
        created = _parse(it.get("created_at"))
        if created and created >= since:
            what = "Idea" if it.get("status") == "idea" and not it.get("notes") else "Draft"
            out.append({"id": f"c-{iid}-new", "at": it.get("created_at"), "kind": "content", "ok": True,
                        "icon": ICON["created"], "ability": None, "link": link,
                        "text": f"{what} ‘{title}’ created{on}"})
        noted = set()
        for n, line in enumerate(str(it.get("notes") or "").splitlines()):
            m = _TRANSITION.match(line.strip())
            if m:
                noted.add(m.group(3))
            if not m or (_parse(m.group(1)) or since) < since:
                continue
            to = m.group(3)
            verb = VERB.get(to, f"Now {to}:")
            text = f"{verb} ‘{title}’" + (f" on {ch}" if to == "published" and ch else "")
            out.append({"id": f"c-{iid}-{n}", "at": m.group(1), "kind": "content", "ok": to != "rejected",
                        "icon": ICON.get(to, "·"), "ability": None, "link": link, "text": text})
        # Set outside the control room (API, n8n): no transition note, so show the current status
        # once, at the last update.
        st, upd = it.get("status"), it.get("updated_at")
        if st in ("approved", "published", "rejected") and st not in noted and (_parse(upd) or since) >= since:
            text = f"{VERB[st]} ‘{title}’" + (f" on {ch}" if st == "published" and ch else "")
            out.append({"id": f"c-{iid}-{st}", "at": upd, "kind": "content", "ok": st != "rejected",
                        "icon": ICON.get(st, "·"), "ability": None, "link": link, "text": text})
    return out


def filter_timeline(events: list[dict], kind: str = "all", ability: int | None = None) -> list[dict]:
    if ability is not None:
        events = [e for e in events if e.get("ability") == ability]
    if kind == "ai":
        return [e for e in events if e["kind"] == "ai"]
    if kind == "content":
        return [e for e in events if e["kind"] == "content"]
    if kind == "errors":
        return [e for e in events if e["kind"] == "ai" and not e["ok"]]
    return events


def spark_points(values: list, w: int = 120, h: int = 28) -> str:
    """SVG polyline points for a tiny sparkline (oldest left). Empty for fewer than 2 values."""
    vals = [v for v in values if isinstance(v, (int, float))]
    if len(vals) < 2:
        return ""
    top = max(vals) or 1
    step = w / (len(vals) - 1)
    return " ".join(f"{i * step:.1f},{h - 2 - (v / top) * (h - 4):.1f}" for i, v in enumerate(vals))


def merge_models(per_gateway: list[tuple[str, dict]]) -> list[dict]:
    """Models from several gateways, one card per (model, provider). avg is weighted by calls;
    p95 across gateways is the highest one (a bound, not exact)."""
    by: dict[tuple, dict] = {}
    for _, data in per_gateway:
        for m in data.get("models") or []:
            if not isinstance(m, dict):
                continue
            k = (str(m.get("model")), str(m.get("provider")))
            c = by.setdefault(k, {"model": k[0], "provider": k[1], "local": is_local(k[1]), "calls_today": 0,
                                  "failures_today": 0, "_ms": 0, "p95_ms": None, "tokens_in": 0, "tokens_out": 0,
                                  "spark": [], "last_at": None})
            calls = int(m.get("calls_today") or 0)
            c["calls_today"] += calls
            c["failures_today"] += int(m.get("failures_today") or 0)
            c["_ms"] += (m.get("avg_ms") or 0) * calls
            if m.get("p95_ms") is not None:
                c["p95_ms"] = max(c["p95_ms"] or 0, m["p95_ms"])
            c["tokens_in"] += int(m.get("tokens_in") or 0)
            c["tokens_out"] += int(m.get("tokens_out") or 0)
            c["spark"] = (c["spark"] + [v for v in m.get("recent_ms") or [] if isinstance(v, (int, float))])[-30:]
            if m.get("last_at") and (c["last_at"] is None or m["last_at"] > c["last_at"]):
                c["last_at"] = m["last_at"]
    out = []
    for c in by.values():
        c["avg_ms"] = int(c.pop("_ms") / c["calls_today"]) if c["calls_today"] else None
        c["points"] = spark_points(c["spark"])
        out.append(c)
    return sorted(out, key=lambda c: (-c["calls_today"], c["model"]))


def abilities_view(settings, recent: list[dict]) -> list[dict]:
    """Each ability: installed or not, and its last gateway call (from the callers in the log)."""
    last: dict[int, dict] = {}
    for r in recent:     # newest first
        num, _ = ability_for_caller(r.get("caller"))
        if num is not None and num not in last:
            last[num] = r
    rank = PROFILE_RANK.get(settings.install_profile, 2)
    out = []
    for num, name, desc, kind, profile, needs in ABILITIES:
        installed = PROFILE_RANK[profile] <= rank and (needs is None or bool(getattr(settings, needs, "")))
        r = last.get(num)
        out.append({"num": num, "name": name, "desc": desc, "kind": kind, "profile": profile,
                    "installed": installed, "last_at": (r or {}).get("finished_at"),
                    "last_ok": None if r is None else bool(r.get("ok")),
                    "last_text": None if r is None else ("OK" if r.get("ok") else
                                                         f"failed ({str(r.get('error') or 'error').replace('_', ' ')})")})
    return out


def now_cards(per_gateway: list[tuple[str, dict]]) -> list[dict]:
    out = []
    for label, data in per_gateway:
        for c in data.get("running") or []:
            if not isinstance(c, dict):
                continue
            num, who = ability_for_caller(c.get("caller"))
            out.append({"id": f"g-{label}-{c.get('id')}", "ability": num, "who": who,
                        "prompt": prompt_words(c.get("prompt")), "model": c.get("model"),
                        "local": is_local(c.get("provider")), "elapsed_ms": int(c.get("elapsed_ms") or 0),
                        "gateway": label})
    return sorted(out, key=lambda c: -c["elapsed_ms"])


# ------------------------------------------------------------------ gathering

async def gather(app, kind: str = "all", ability: int | None = None) -> dict:
    s, B = app.state.settings, app.state.backends
    gateways = ([("main", s.gateway_url)] if s.gateway_url else []) + \
        [(label, url) for label, url in s.activity_gateways if url != s.gateway_url]
    sources, per_gateway = [], []
    answers = await asyncio.gather(*(B.gateway_activity(url) for _, url in gateways), return_exceptions=True)
    for (label, _), a in zip(gateways, answers):
        if isinstance(a, dict):
            per_gateway.append((label, a))
            sources.append({"name": f"{label} gateway", "ok": True, "detail": "ok"})
        elif isinstance(a, (BackendError, ValueError)):
            sources.append({"name": f"{label} gateway", "ok": False, "detail": _problem(a)})
        else:
            raise a
    now = datetime.now(timezone.utc)
    events, recent = [], []
    for label, data in per_gateway:
        rows = [r for r in data.get("recent") or [] if isinstance(r, dict)]
        recent += rows
        events += [ai_event(r, label) for r in rows]
    if s.calendar_url:
        try:
            items = await B._cached("activity_items", 15, lambda: B.items())
            events += content_events(items, now - timedelta(days=s.activity_days))
            sources.append({"name": "calendar", "ok": True, "detail": "ok"})
        except (BackendError, ValueError) as e:
            sources.append({"name": "calendar", "ok": False, "detail": _problem(e)})
    # Newest first; within one second a status change comes before the item's creation.
    events.sort(key=lambda e: (_parse(e["at"]) or now, not e["id"].endswith("-new")), reverse=True)
    recent.sort(key=lambda r: str(r.get("finished_at") or ""), reverse=True)
    return {"updated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "profile": s.install_profile, "sources": sources, "now": now_cards(per_gateway),
            "timeline": filter_timeline(events, kind, ability)[:TIMELINE_MAX],
            "filter": kind, "ability": ability,
            "models": merge_models(per_gateway), "abilities": abilities_view(s, recent)}


def _problem(e: Exception) -> str:
    """"unreachable", or a short reason without any URL."""
    detail = e.detail if isinstance(e, BackendError) else "answer is not JSON"
    if "unreachable" in detail:
        return "unreachable"
    return re.sub(r"https?://\S+", "", str(detail))[:80] or "error"


def _args(filter: str, ability: str) -> tuple[str, int | None]:
    kind = filter if filter in FILTERS else "all"
    num = int(ability) if ability.isdigit() and int(ability) in BY_NUM else None
    return kind, num


def group_days(events: list[dict]) -> list[dict]:
    """[{day: "2026-09-28", events: [...]}] in the timeline's order (newest first), by UTC day.
    The page's script regroups by the viewer's own day."""
    out: list[dict] = []
    for e in events:
        d = str(e.get("at") or "")[:10]
        if not out or out[-1]["day"] != d:
            out.append({"day": d, "events": []})
        out[-1]["events"].append(e)
    return out


def register(app, page, current):
    @app.get("/activity", response_class=HTMLResponse)
    async def activity_page(request: Request, filter: str = "all", ability: str = "", session=Depends(current)):
        kind, num = _args(filter[:10], ability[:4])
        data = await gather(app, kind, num)
        return page(request, "activity.html", session, nav="activity", data=data, days=group_days(data["timeline"]),
                    ability_name=BY_NUM[num][1] if num else None, seconds=seconds,
                    filters=[("all", "All"), ("ai", "AI calls"), ("content", "Content"), ("errors", "Errors")])

    @app.get("/activity/data")
    async def activity_data(filter: str = "all", ability: str = "", session=Depends(current)):
        kind, num = _args(filter[:10], ability[:4])
        return await gather(app, kind, num)
