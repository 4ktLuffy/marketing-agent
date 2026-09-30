"""Results: is this saving us time, and which AI works best for us?

Worked out from what is already recorded; nothing new is stored anywhere (no table in 88 or 19,
nothing on disk here). Sources, all read-only:
- 88 `GET /tasks?limit=200` then `GET /tasks/{id}` per task: `created_at`, `pieces`, `piece_state`
  (current `draft_id`, `findings`, `checks`, `calendar_item_id`), `drafts` (`kind`, `provider`,
  `parent_id`), `events` (`submitted` with `piece_key`, `draft_id`, `blocked`, `labels`;
  `fact_changed` with `from_status`; `exported`), `exports`;
- 19 `GET /items/{id}/audit` and `/versions` for the calendar item each piece has now;
- the gateway activity log (03 `GET /v1/activity`) for the model calls of AI-assisted steps.
The raw reads are cached for 60 seconds and capped at the newest 200 tasks.

Definitions (also on the page and in the README):
- a *round* is one submit: the `submitted` events for one draft, one per piece;
- *first usable draft*: the first round that covers every piece with none blocked; the time is
  from the task's `created_at` to that round's last event;
- *pastes*: drafts of kind `paste` (a hand split is not a new paste);
- *blocked on first paste*: the piece's first `submitted` event says blocked. Reasons: the blocking
  findings and rule errors when that first submit is still the piece's current one; otherwise the
  event's labels (the rule detail is not kept after a resubmit);
- *approval turnaround*: in 19's audit of the piece's current item, from the last time it entered
  in_review to the first approval after that;
- *reviewer edits*: 19 versions after the first that were written while the item was in_review;
- *by AI*: each piece counts for the AI whose answer was first submitted for it (a hand split
  inherits the provider of the paste it came from).
"""
import asyncio
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse

from . import views
from .activity import is_local
from .backends import BackendError

MAX_TASKS = 200
CACHE_S = 60
MIN_COMPARE = 10          # fewer pieces than this per AI: "too few to compare", never ranked
WINDOW_DAYS = 30
FETCH_CONCURRENCY = 8
PROVIDER_NAMES = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini", "other": "Another AI",
                  "self": "Written by hand"}
NO_PROVIDER = "Not recorded"
NOT_BLOCKING = {"match", "review"}
RULE_WHERE = {"brand": "Brand rule", "platform": "Channel rule"}
# Gateway callers that are AI-assisted steps of this flow (the X-Caller each service sends).
AI_STEPS = [("88", "Model check of pasted drafts (task bridge)"),
            ("44", "Claim checker (also used by other workflows)"),
            ("72 onboarding", "Setting up facts from your website or documents"),
            ("72 voice", "Voice interview")]


# ------------------------------------------------------------------ small helpers (tested directly)

def parse_ts(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def duration(sec) -> str:
    if sec is None:
        return "–"
    sec = max(0, int(sec))
    if sec < 60:
        return "under a minute"
    m = sec // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} day{'s' if d != 1 else ''} {h} h" if h else f"{d} day{'s' if d != 1 else ''}"


def median(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.median(xs) if xs else None


def share(k: int, n: int) -> dict:
    """A rate always carries its counts: {k, n, pct, text: "3 of 12 (25%)"}."""
    pct = round(100 * k / n) if n else None
    return {"k": k, "n": n, "pct": pct, "text": f"{k} of {n} ({pct}%)" if n else "none yet"}


def provider_name(p) -> str:
    return PROVIDER_NAMES.get(str(p), NO_PROVIDER) if p else NO_PROVIDER


def rule_words(rule) -> str:
    return re.sub(r"[_-]+", " ", str(rule or "rule")).strip()[:80] or "rule"


# ------------------------------------------------------------------ one task

def _provider_of(drafts: dict, did) -> str | None:
    seen = 0
    while did in drafts and seen < 20:
        d = drafts[did]
        if d.get("provider"):
            return d["provider"]
        did, seen = d.get("parent_id"), seen + 1
    return None


def item_timeline(audit: list, versions: list) -> dict:
    """From 19's audit and versions of one item: approved?, turnaround seconds, edits in review."""
    rows = [(parse_ts(a.get("at")), a) for a in audit if isinstance(a, dict)]
    rows = [(t, a) for t, a in rows if t]
    approved_at = review_at = None
    for t, a in rows:
        if a.get("to_status") == "approved" and a.get("from_status") != "approved":
            approved_at = t
            break
        if a.get("to_status") == "in_review" and a.get("from_status") != "in_review":
            review_at = t
    edits = 0
    for v in versions:
        if not isinstance(v, dict) or not isinstance(v.get("n"), int) or v["n"] < 2:
            continue
        at = parse_ts(v.get("created_at"))
        status = None
        for t, a in rows:
            if at and t <= at:
                status = a.get("to_status")
        if status == "in_review":
            edits += 1
    turnaround = (approved_at - review_at).total_seconds() if approved_at and review_at else None
    return {"approved": approved_at is not None, "turnaround": turnaround, "edits": edits}


def analyse_task(t: dict, items: dict) -> dict:
    """One 88 task (GET /tasks/{id}) plus 19 reads of its items -> the numbers for this task.
    items: {item_id: {"audit": [...], "versions": [...]}}; a missing item = not readable."""
    created = parse_ts(t.get("created_at"))
    drafts = {d.get("draft_id"): d for d in t.get("drafts") or [] if isinstance(d, dict)}
    events = [e for e in t.get("events") or [] if isinstance(e, dict) and isinstance(e.get("detail"), dict)]
    state = {str(p.get("piece_key")): p for p in t.get("piece_state") or [] if isinstance(p, dict)}
    keys = [str(p.get("key")) for p in t.get("pieces") or [] if isinstance(p, dict) and p.get("key")] or list(state)
    subs = [e for e in events if e.get("kind") == "submitted"]

    rounds, cur = [], None
    for e in subs:
        d = e["detail"]
        k, did = str(d.get("piece_key")), d.get("draft_id")
        if cur is None or cur["draft_id"] != did or k in cur["pieces"]:
            cur = {"draft_id": did, "pieces": {}, "at": e.get("at")}
            rounds.append(cur)
        cur["pieces"][k] = bool(d.get("blocked"))
        cur["at"] = e.get("at")
    usable = next((r for r in rounds if keys and set(keys) <= set(r["pieces"]) and not any(r["pieces"].values())), None)
    usable_at = parse_ts(usable["at"]) if usable else None
    to_usable = (usable_at - created).total_seconds() if usable_at and created else None

    first, times = {}, Counter()
    for e in subs:
        k = str(e["detail"].get("piece_key"))
        times[k] += 1
        first.setdefault(k, e)
    pieces = []
    for k in keys:
        ps = state.get(k) or {}
        e = first.get(k)
        p = {"key": k, "channel": ps.get("channel") or "", "submitted": e is not None}
        if e is None:
            pieces.append(p)
            continue
        d = e["detail"]
        blocked = bool(d.get("blocked"))
        labels, rules, rules_known = [], [], False
        if blocked:
            if times[k] == 1 and ps.get("draft_id") == d.get("draft_id"):
                labels = sorted({f.get("label") for f in ps.get("findings") or []
                                 if isinstance(f, dict) and f.get("blocking") and f.get("label")})
                checks = ps.get("checks") if isinstance(ps.get("checks"), dict) else {}
                rules = [f"{RULE_WHERE[w]}: {rule_words(v.get('rule'))}" for w in RULE_WHERE
                         for v in checks.get(w) or [] if isinstance(v, dict) and v.get("severity") == "error"]
                rules_known = True
            else:
                labels = sorted({str(x) for x in d.get("labels") or []} - NOT_BLOCKING)
        item_id = ps.get("calendar_item_id")
        it = items.get(item_id) if isinstance(item_id, int) else None
        tl = item_timeline(it.get("audit") or [], it.get("versions") or []) if it else None
        p.update(provider=_provider_of(drafts, d.get("draft_id")), blocked_first=blocked, labels=labels,
                 rules=rules, rules_known=rules_known, known=tl is not None,
                 approved=bool(tl and tl["approved"]), turnaround=tl["turnaround"] if tl else None,
                 edits=tl["edits"] if tl else None)
        pieces.append(p)

    exported = bool(t.get("exports")) or any(e.get("kind") == "exported" for e in events)
    changed = sum(1 for e in events if e.get("kind") == "fact_changed"
                  and e["detail"].get("from_status") in ("approved", "published"))
    return {"id": str(t.get("id") or ""), "goal": str(t.get("goal") or "")[:200], "created_at": t.get("created_at"),
            "created": created, "pastes": sum(1 for d in drafts.values() if d.get("kind") == "paste"),
            "to_usable": to_usable, "pieces": pieces, "exported": exported,
            "pieces_exported": len(keys) if exported else 0, "facts_changed_after_approval": changed}


# ------------------------------------------------------------------ all tasks

def summarise(analysed: list[dict], now: datetime, days: int | None) -> dict:
    """Numbers for the page. days=None = every task read (the newest MAX_TASKS)."""
    since = now - timedelta(days=days) if days else None
    ts = [a for a in analysed if since is None or (a["created"] and a["created"] >= since)]
    subm = [p for a in ts for p in a["pieces"] if p["submitted"]]
    known = [p for p in subm if p["known"]]
    usable = [a["to_usable"] for a in ts if a["to_usable"] is not None]
    blocked = [p for p in subm if p["blocked_first"]]
    by_label, by_rule = Counter(), Counter()
    rules_unknown = 0
    for p in blocked:
        for lab in p["labels"]:
            by_label[views.EVIDENCE.get(lab, (lab.replace("_", " "), ""))[0]] += 1
        for r in p["rules"]:
            by_rule[r] += 1
        if not p["rules_known"]:
            rules_unknown += 1
    turn = [p["turnaround"] for p in known if p["turnaround"] is not None]
    edits = [p["edits"] for p in known if p["edits"] is not None]
    overall = {
        "tasks": len(ts), "pieces": sum(len(a["pieces"]) for a in ts), "submitted": len(subm),
        "usable": share(len(usable), len(ts)), "to_usable_median": median(usable),
        "to_usable_min": min(usable) if usable else None, "to_usable_max": max(usable) if usable else None,
        "pastes": sum(a["pastes"] for a in ts), "pastes_median": median([a["pastes"] for a in ts]),
        "blocked_first": share(len(blocked), len(subm)),
        "reasons_label": by_label.most_common(), "reasons_rule": by_rule.most_common(),
        "rules_unknown": rules_unknown,
        "turnaround_median": median(turn), "turnaround_n": len(turn),
        "edits_total": sum(edits), "edited_pieces": share(sum(1 for e in edits if e), len(edits)),
        "edits_median": median(edits),
        "approved": share(sum(1 for p in known if p["approved"]), len(known)),
        "not_readable": len(subm) - len(known),
        "pieces_exported": sum(a["pieces_exported"] for a in ts),
        "tasks_exported": sum(1 for a in ts if a["exported"]),
        "facts_changed": sum(a["facts_changed_after_approval"] for a in ts),
    }
    return {"overall": overall, "by_ai": by_ai(subm), "tasks": ts}


def by_ai(pieces: list[dict]) -> dict:
    groups: dict[str, list] = {}
    for p in pieces:
        groups.setdefault(provider_name(p.get("provider")), []).append(p)
    rows = []
    for name, ps in groups.items():
        known = [p for p in ps if p["known"]]
        rows.append({"name": name, "pieces": len(ps), "enough": len(ps) >= MIN_COMPARE,
                     "blocked_first": share(sum(1 for p in ps if p["blocked_first"]), len(ps)),
                     "approved": share(sum(1 for p in known if p["approved"]), len(known)),
                     "edits_median": median([p["edits"] for p in known if p["edits"] is not None]),
                     "edits_n": sum(1 for p in known if p["edits"] is not None)})
    rows.sort(key=lambda r: (-r["pieces"], r["name"]))
    comparable = [r for r in rows if r["enough"]]
    best = None
    if len(comparable) >= 2:
        low = min(comparable, key=lambda r: r["blocked_first"]["pct"])
        if sum(1 for r in comparable if r["blocked_first"]["pct"] == low["blocked_first"]["pct"]) == 1:
            best = low["name"]
    return {"rows": rows, "comparable": len(comparable), "fewest_blocked": best}


def ai_steps(rows: list[dict], since: datetime | None) -> dict:
    """Gateway calls of this flow's AI-assisted steps, grouped by step."""
    steps: dict[str, dict] = {}
    oldest = None
    for r in rows:
        at = parse_ts(r.get("finished_at") or r.get("started_at"))
        if at and (oldest is None or at < oldest):
            oldest = at
        if since and at and at < since:
            continue
        caller = str(r.get("caller") or "")
        name = next((n for pre, n in AI_STEPS if caller == pre or caller.startswith(pre + " ")), None)
        if name is None:
            continue
        s = steps.setdefault(name, {"name": name, "calls": 0, "failed": 0, "ms": 0, "local": 0, "hosted": 0})
        s["calls"] += 1
        s["failed"] += 0 if r.get("ok") else 1
        s["ms"] += int(r.get("duration_ms") or 0)
        s["local" if is_local(r.get("provider")) else "hosted"] += 1
    out = sorted(steps.values(), key=lambda s: -s["calls"])
    for s in out:
        s["time"] = duration(s["ms"] / 1000) if s["ms"] >= 60000 else f"{s['ms'] / 1000:.1f} s"
    return {"steps": out, "oldest": oldest.strftime("%Y-%m-%d") if oldest else None, "log_rows": len(rows)}


# ------------------------------------------------------------------ reading (cached 60 s)

async def read_raw(B) -> dict:
    """88's newest MAX_TASKS tasks with detail, and 19's audit and versions for their current items."""
    listed = await B.task_list(MAX_TASKS)
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    problems = []

    async def one(coro):
        async with sem:
            try:
                return await coro
            except BackendError as e:
                return e

    ids = [str(t.get("id")) for t in listed[:MAX_TASKS] if t.get("id")]
    got = await asyncio.gather(*(one(B.task(i)) for i in ids))
    tasks = [g for g in got if isinstance(g, dict)]
    if len(tasks) < len(ids):
        problems.append(f"{len(ids) - len(tasks)} task(s) could not be read from the task bridge (88)")
    item_ids = sorted({p.get("calendar_item_id") for t in tasks for p in t.get("piece_state") or []
                       if isinstance(p, dict) and isinstance(p.get("calendar_item_id"), int)})
    audits = await asyncio.gather(*(one(B.item_audit(i)) for i in item_ids))
    versions = await asyncio.gather(*(one(B.item_versions(i)) for i in item_ids))
    items, missing = {}, 0
    for i, a, v in zip(item_ids, audits, versions):
        if isinstance(a, list) and isinstance(v, list):
            items[i] = {"audit": a, "versions": v}
        else:
            missing += 1
    if missing:
        problems.append(f"{missing} calendar item(s) could not be read from the calendar (19): "
                        "their approvals and edits are left out")
    return {"tasks": tasks, "items": items, "problems": problems, "listed": len(listed),
            "read_at": datetime.now(timezone.utc)}


async def read_gateways(B, s) -> tuple[list[dict], list[str]]:
    gateways = ([("main", s.gateway_url)] if s.gateway_url else []) + \
        [(label, url) for label, url in s.activity_gateways if url != s.gateway_url]
    rows, failed = [], []
    for label, url in gateways:
        try:
            data = await B.gateway_activity(url)
            rows += [r for r in data.get("recent") or [] if isinstance(r, dict)]
        except (BackendError, ValueError):
            failed.append(label)
    return rows, failed


def register(app, page, current):
    s = app.state.settings
    B = lambda: app.state.backends  # noqa: E731

    @app.get("/tasks/results", response_class=HTMLResponse)
    async def results(request: Request, range: str = "30d", session=Depends(current)):
        rng = "all" if range == "all" else "30d"
        if not s.tasks_url:
            return page(request, "results.html", session, nav="more", not_installed=True, range=rng)
        try:
            raw = await B()._cached("results_raw", CACHE_S, lambda: read_raw(B()))
        except BackendError as e:
            return page(request, "results.html", session, nav="more", range=rng,
                        error=f"The task bridge (88) did not answer: {e.detail}")
        days = WINDOW_DAYS if rng == "30d" else None
        now = datetime.now(timezone.utc)
        data = summarise([analyse_task(t, raw["items"]) for t in raw["tasks"]], now, days)
        gw = None
        if s.gateway_url or s.activity_gateways:
            rows, failed = await B()._cached("results_gateway", CACHE_S, lambda: read_gateways(B(), s))
            gw = {**ai_steps(rows, now - timedelta(days=days) if days else None), "failed": failed}
        return page(request, "results.html", session, nav="more", range=rng, data=data, raw=raw, gw=gw,
                    duration=duration, capped=raw["listed"] >= MAX_TASKS, max_tasks=MAX_TASKS,
                    min_compare=MIN_COMPARE, window_days=WINDOW_DAYS)
