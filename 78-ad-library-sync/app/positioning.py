"""Competitor positioning map: which messaging themes each competitor claims, and we claim.

For every active competitor it reads what they say now (the latest 09 snapshots of their key
pages and their active ads stored here) and, for us, the approved facts (05) plus our own pages
if 09 watches them. The LLM (prompt `positioning_themes`, via the gateway) sorts claims into a
fixed list of themes, each with a quote and its source. Code keeps only quotes that are an
exact part of the source they cite. The result is a matrix (themes x brands, evidence counts and
quotes), the crowded themes, the white space (themes no competitor claims that one of OUR
APPROVED FACTS supports: our web pages alone never make white space) and the shifts since the
previous month. One snapshot per month; building again in the same month replaces it.
"""
import json
import math
import re
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.main import db, env, host_of, log, now, require_key, scrub, service_url, competitor_out

router = APIRouter()

SCHEMA = """
CREATE TABLE IF NOT EXISTS positioning (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL UNIQUE,
    built_at TEXT NOT NULL,
    snapshot TEXT NOT NULL
);
"""
# Default themes (key: what counts). Override with POSITIONING_THEMES (a JSON object).
DEFAULT_THEMES = {
    "price": "price, discounts, free trials, free shipping, value for money",
    "freshness": "roast or production date, freshly made, shipped fresh",
    "convenience": "subscriptions, delivery schedule, skip or pause, no effort, easy to use",
    "quality": "taste, ingredients, origin, craft, awards, specialty grade",
    "ethics": "sourcing, farmers, sustainability, packaging, fair pay, certifications",
    "speed": "fast delivery, next-day shipping, quick setup",
    "team_office": "teams, offices, workplace perks, buying for colleagues",
    "gifting": "gifts, gift cards, occasions",
    "guarantee": "refunds, returns, guarantees, risk-free trial",
}
MAX_WORDS, MIN_WORDS = 25, 3
MAX_QUOTES_SHOWN = 5  # per cell; the count covers all valid quotes
QUOTE_CHARS = "\"“”„«»"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


# ---------- config


def themes() -> dict[str, str]:
    raw = env("POSITIONING_THEMES")
    if raw:
        try:
            t = json.loads(raw)
            if isinstance(t, dict) and t and all(re.fullmatch(r"[a-z][a-z0-9_]{0,30}", k) and isinstance(v, str)
                                                 for k, v in t.items()):
                return {k: v.strip()[:200] for k, v in t.items()}
        except ValueError:
            pass
        log.warning("POSITIONING_THEMES is not a JSON object of lowercase keys to text; using the defaults")
    return dict(DEFAULT_THEMES)


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(env(name, str(default))), hi))
    except ValueError:
        return default


def month_label(month: str) -> str:
    y, m = month.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


# ---------- verbatim check (the model proposes, code decides)


def _norm_chars(s: str) -> str:
    return s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def find_verbatim(q: str, text: str) -> str | None:
    """The exact span of `text` that the quote copies. Case, whitespace and apostrophe style may
    differ; words and punctuation may not. None when it is not an exact part of the text."""
    q = (q or "").strip().strip(QUOTE_CHARS + "'").strip()
    q = re.sub(r"(\.\.\.|…)$", "", q).strip()
    parts = q.split()
    if not parts:
        return None
    pattern = r"\s+".join(re.escape(_norm_chars(p)).replace("'", "['’‘]").replace('"', '["“”]') for p in parts)
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    return text[m.start():m.end()]


def check_claims(proposed, sources: dict[str, dict], theme_keys, stats: Counter) -> list[dict]:
    """Keep a claim only when its theme is known, its source id is one we sent, and its quote is
    an exact part of that source (3-25 words). Duplicates (same theme, same span) are merged."""
    kept, seen = [], set()
    for c in proposed if isinstance(proposed, list) else []:
        stats["proposed"] += 1
        if not isinstance(c, dict):
            stats["malformed"] += 1
            continue
        theme = str(c.get("theme") or "").strip().lower()
        sid = str(c.get("source_id") or "").strip().strip("[]")
        if theme not in theme_keys:
            stats["unknown_theme"] += 1
            continue
        src = sources.get(sid)
        if src is None:
            stats["unknown_source"] += 1
            continue
        span = find_verbatim(str(c.get("quote") or ""), src["text"])
        if span is None:
            stats["not_verbatim"] += 1
            continue
        n = len(span.split())
        if n < MIN_WORDS or n > MAX_WORDS:
            stats["length"] += 1
            continue
        key = (theme, " ".join(re.sub(r"[^\w\s]", " ", span.lower()).split()))
        if key in seen:
            stats["duplicate"] += 1
            continue
        seen.add(key)
        stats["kept"] += 1
        kept.append({"theme": theme, "quote": span, "source": {k: src[k] for k in ("kind", "ref", "url")}})
    return kept


# ---------- gathering what each brand says


def _get(client: httpx.Client, url: str, params=None, key=False):
    r = client.get(url, params=params, headers={"X-API-Key": env("INTERNAL_API_KEY")} if key else None)
    r.raise_for_status()
    return r.json()


def ad_sources(conn, cid: int) -> list[dict]:
    out = []
    rows = conn.execute("SELECT * FROM ads WHERE competitor_id = ? AND status = 'active' ORDER BY first_seen DESC, ad_id",
                        (cid,)).fetchall()
    for r in rows:
        texts = [t for k in ("bodies", "link_titles", "link_descriptions") for t in json.loads(r[k])]
        text = "\n".join(dict.fromkeys(t.strip() for t in texts if t.strip()))
        if text:
            out.append({"kind": "ad", "ref": f"ad {r['ad_id']}", "text": text,
                        "url": f"https://www.facebook.com/ads/library/?id={quote(r['ad_id'])}"})
    return out


def page_source(s: dict) -> dict:
    label = str(s.get("label") or "")
    kind = label.split(" · ")[-1] if " · " in label else "page"
    return {"kind": f"page: {kind}", "ref": label or s["url"], "text": str(s.get("text") or ""), "url": s["url"],
            "checked_at": s.get("last_checked_at")}


def gather(conn) -> tuple[list[dict], dict, list[str]]:
    """[{name, kind, competitor_id, sources}] for active competitors, our brand, and warnings."""
    warnings: list[str] = []
    monitor, brand = service_url("MONITOR_URL"), service_url("BRAND_URL")
    snaps: list[dict] = []
    ours = {"name": "Us", "kind": "ours", "competitor_id": None, "sources": []}
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        if monitor:
            try:
                snaps = [s for s in _get(client, f"{monitor}/snapshots", key=True)
                         if isinstance(s, dict) and s.get("url") and s.get("text")]
            except (httpx.HTTPError, ValueError) as e:
                warnings.append(scrub(f"change-monitor (09) snapshots failed: {type(e).__name__}"))
        else:
            warnings.append("MONITOR_URL is not set: no page texts, only ads")
        own_hosts = {host_of(d) for d in env("OWN_DOMAINS").split(",") if d.strip()}
        if brand:
            try:
                prof = _get(client, f"{brand}/profile")
                ours["name"] = str(prof.get("name") or "Us")[:120]
                if prof.get("website"):
                    own_hosts.add(host_of(prof["website"]))
                facts = _get(client, f"{brand}/facts").get("facts") or []
                ours["sources"] += [{"kind": "fact", "ref": f"fact {f.get('id')}", "text": str(f.get("text") or ""),
                                     "url": None} for f in facts if isinstance(f, dict) and f.get("text")]
            except (httpx.HTTPError, ValueError, AttributeError) as e:
                warnings.append(f"brand-service (05) failed: {type(e).__name__}: no approved facts, so no white space")
        else:
            warnings.append("BRAND_URL is not set: no approved facts, so no white space")
    own_hosts.discard("")
    ours["sources"] += [page_source(s) for s in snaps
                        if not str(s.get("tag") or "").startswith("competitor:") and host_of(s["url"]) in own_hosts]
    brands = []
    for row in conn.execute("SELECT * FROM competitors WHERE status = 'active' ORDER BY id").fetchall():
        c = competitor_out(row)
        srcs = [page_source(s) for s in snaps if s.get("tag") == f"competitor:{c['id']}"] + ad_sources(conn, c["id"])
        brands.append({"name": c["name"], "kind": "competitor", "competitor_id": c["id"], "sources": srcs})
    return brands, ours, warnings


def source_block(sources: list[dict], budget: int) -> tuple[str, dict]:
    """Numbered sources for the prompt, within `budget` characters. Returns (block, {id: source});
    a source cut short is checked against its full text anyway."""
    lines, by_id, used = [], {}, 0
    per = max(300, budget // max(1, len(sources)))
    for i, s in enumerate(sources, 1):
        text = " ".join(s["text"].split())
        room = min(per if len(sources) > 1 else budget, budget - used)
        if room < 80:
            break
        if len(text) > room:
            text = text[:room].rsplit(" ", 1)[0] + " ..."
        sid = f"s{i}"
        lines.append(f"[{sid}] ({s['kind']}) {text}")
        by_id[sid] = s
        used += len(text)
    return "\n".join(lines), by_id


def run_gateway(company: str, block: str, max_claims: int) -> tuple[list, dict]:
    base = service_url("GATEWAY_URL")
    if not base:
        raise RuntimeError("GATEWAY_URL is not set")
    theme_lines = "\n".join(f"- {k}: {v}" for k, v in themes().items())
    try:
        with httpx.Client(timeout=float(_int("POSITIONING_TIMEOUT", 300, 10, 1800)), trust_env=False) as client:
            r = client.post(f"{base}/v1/run",
                            headers={"X-API-Key": env("INTERNAL_API_KEY"), "X-Caller": "78 positioning map"},
                            json={"prompt": "positioning_themes",
                                  "vars": {"company": company, "themes": theme_lines, "sources": block,
                                           "max_claims": max_claims}})
    except httpx.HTTPError as e:
        raise RuntimeError(f"gateway unreachable ({type(e).__name__})")
    if r.status_code != 200:
        raise RuntimeError(scrub(f"gateway {r.status_code}: {r.text[:200]}"))
    body = r.json()
    out = body.get("output")
    if not isinstance(out, dict):
        raise RuntimeError("gateway returned no JSON object")
    return out.get("claims") or [], body.get("usage") or {}


# ---------- the map


def build_matrix(brands: list[dict], theme_keys: list[str]) -> dict:
    """cells[theme][brand] = {count, sources, quotes (up to 5), from_facts}."""
    cells = {t: {} for t in theme_keys}
    for b in brands:
        for t in theme_keys:
            # Approved facts first: the white space shows them.
            qs = sorted((c for c in b.get("claims") or [] if c["theme"] == t), key=lambda c: c["source"]["kind"] != "fact")
            cells[t][b["name"]] = {
                "count": len(qs),
                "sources": len({q["source"]["ref"] for q in qs}),
                "from_facts": sum(1 for q in qs if q["source"]["kind"] == "fact"),
                "quotes": qs[:MAX_QUOTES_SHOWN],
            }
    return cells


def classify(cells: dict, brands: list[dict], ours_name: str | None) -> dict:
    comps = [b["name"] for b in brands if b["kind"] == "competitor" and not b.get("error")]
    need = max(2, math.ceil(len(comps) / 2))
    crowded, white, open_no_fact, ours_missing = [], [], [], []
    for t, row in cells.items():
        claimers = [n for n in comps if row.get(n, {}).get("count")]
        ours = row.get(ours_name) if ours_name else None
        if len(claimers) >= need and len(comps) >= 2:
            crowded.append({"theme": t, "competitors": claimers})
        if not claimers:
            if ours and ours["from_facts"]:
                facts = [q for q in ours["quotes"] if q["source"]["kind"] == "fact"]
                white.append({"theme": t, "facts": facts})
            else:
                open_no_fact.append(t)
        elif ours is not None and not ours["count"]:
            ours_missing.append({"theme": t, "competitors": claimers})
    return {"crowded": crowded, "white_space": white, "open_no_fact": open_no_fact, "they_claim_we_dont": ours_missing,
            "crowded_min": need}


def _flat(s: str) -> str:
    return " ".join(_norm_chars(s).lower().split())


def corpus(sources: list[dict]) -> str:
    """Everything a brand said this month, normalized: next month's shifts check quotes against it."""
    return _flat("\n".join(s["text"] for s in sources))[:200000]


def shifts(old: dict | None, new: dict) -> tuple[list[dict], int]:
    """What changed between two snapshots, per brand and theme: started / stopped talking about a
    theme, or a clear change in how much (the count at least doubled or halved, by 2 or more).

    The model files the same words under a theme one month and not the next. So when both
    snapshots keep the brands' texts (`_corpus`), a "started" whose quotes were all already in
    last month's text, or a "stopped" whose old quotes are still in this month's text, is not a
    shift: it is dropped and counted (second value)."""
    if not old:
        return [], 0
    out, suppressed = [], 0
    old_corpus, new_corpus = old.get("_corpus") or {}, new.get("_corpus") or {}
    old_names = {b["name"] for b in old["brands"] if not b.get("error")}
    new_names = {b["name"] for b in new["brands"] if not b.get("error")}
    label = month_label(new["month"])
    for b in new["brands"]:
        name = b["name"]
        if b.get("error") or name not in old_names:
            continue
        for t in new["cells"]:
            if t not in old["cells"]:  # a theme added since: no "before" to compare with
                continue
            a = old["cells"][t].get(name, {}).get("count", 0)
            z = new["cells"][t].get(name, {}).get("count", 0)
            if a == 0 and z > 0:
                kind, text = "started", f"{name} started talking about {t} in {label}"
            elif a > 0 and z == 0:
                kind, text = "stopped", f"{name} stopped talking about {t} in {label}"
            elif abs(z - a) >= 2 and (z >= 2 * a or 2 * z <= a):
                kind = "more" if z > a else "less"
                text = f"{name} talks {'more' if z > a else 'less'} about {t} ({a} → {z} quotes) in {label}"
            else:
                continue
            if kind == "started" and name in old_corpus and all(
                    _flat(q["quote"]) in old_corpus[name] for q in new["cells"][t][name]["quotes"]):
                suppressed += 1
                continue
            if kind == "stopped" and name in new_corpus and any(
                    _flat(q["quote"]) in new_corpus[name] for q in old["cells"][t][name]["quotes"]):
                suppressed += 1
                continue
            q = new["cells"][t][name]["quotes"][:1] if z else []
            out.append({"brand": name, "theme": t, "kind": kind, "before": a, "after": z, "text": text, "quote": q})
    for name in sorted(new_names - old_names):
        out.append({"brand": name, "theme": None, "kind": "new_brand", "before": None, "after": None,
                    "text": f"{name} is on the map for the first time in {label}", "quote": []})
    order = {"started": 0, "stopped": 1, "more": 2, "less": 3, "new_brand": 4}
    return sorted(out, key=lambda s: (order[s["kind"]], s["brand"], s["theme"] or "")), suppressed


def markdown(s: dict) -> str:
    names = [b["name"] for b in s["brands"]]
    lines = [f"# Positioning map {month_label(s['month'])}", "",
             f"Built {s['built_at'][:10]} from {sum(b['sources'] for b in s['brands'])} sources "
             f"(competitor page snapshots, active ads, our approved facts and pages). Every quote is copied "
             f"exactly from its source; {s['stats'].get('kept', 0)} of {s['stats'].get('proposed', 0)} proposed "
             f"quotes passed that check.", "",
             "| Theme | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for t, row in s["cells"].items():
        lines.append(f"| {t} | " + " | ".join(str(row.get(n, {}).get("count", 0)) for n in names) + " |")
    lines += ["", "## White space (no competitor claims it; our approved facts back it)"]
    lines += [f"- **{w['theme']}**: " + "; ".join(f"\"{q['quote']}\" ({q['source']['ref']})" for q in w["facts"][:2])
              for w in s["white_space"]] or ["- none this month"]
    lines += ["", "## Crowded (most competitors claim it)"]
    lines += [f"- **{c['theme']}**: {', '.join(c['competitors'])}" for c in s["crowded"]] or ["- none"]
    if s["they_claim_we_dont"]:
        lines += ["", "## They claim it, we say nothing"]
        lines += [f"- **{m['theme']}**: {', '.join(m['competitors'])}" for m in s["they_claim_we_dont"]]
    if s["open_no_fact"]:
        lines += ["", "## Nobody claims it, and we have no approved fact for it",
                  "- " + ", ".join(s["open_no_fact"]) + " (add a fact in 05 before claiming one)"]
    lines += ["", "## Shifts since last month"]
    lines += [f"- {x['text']}" + (f": \"{x['quote'][0]['quote']}\" ({x['quote'][0]['source']['ref']})" if x["quote"] else "")
              for x in s["shifts"]] or ["- none (or this is the first map)"]
    if s.get("shifts_suppressed"):
        lines.append(f"- _{s['shifts_suppressed']} change(s) left out: the model filed words that did not change "
                     "under a different theme_")
    lines += ["", "## Quotes"]
    for t, row in s["cells"].items():
        for n in names:
            for q in row.get(n, {}).get("quotes", []):
                src = q["source"]
                lines.append(f"- {t} · {n}: \"{q['quote']}\" ({src['ref']}{', ' + src['url'] if src.get('url') else ''})")
    if s["warnings"]:
        lines += ["", "_Warnings: " + " · ".join(s["warnings"]) + "_"]
    return "\n".join(lines)


def _db():
    conn = db()
    conn.executescript(SCHEMA)
    return conn


def _load(row, corpus: bool = False) -> dict:
    s = json.loads(row["snapshot"])
    s["id"] = row["id"]
    if not corpus:
        s.pop("_corpus", None)  # stored for the shift check; not part of the answer
    return s


@router.post("/positioning/build", dependencies=[Depends(require_key)])
def build_positioning(month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    """Build this month's map (or `month`), replacing that month's snapshot. 502 when every
    LLM call failed; nothing is stored then."""
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    keys = list(themes())
    budget = _int("POSITIONING_MAX_CHARS", 6000, 1000, 40000)
    max_claims = _int("POSITIONING_MAX_CLAIMS", 24, 4, 40)
    stats: Counter = Counter()
    usage = Counter()
    with closing(_db()) as conn:
        comps, ours, warnings = gather(conn)
        brands = comps + ([ours] if ours["sources"] else [])
        if not ours["sources"]:
            warnings.append("no approved facts or own pages: our column is empty and there is no white space")
        ran = 0
        for b in brands:
            b["sources_n"] = len(b["sources"])
            if not b["sources"]:
                b["error"] = "nothing to read yet (no checked page snapshot, no active ad)"
                continue
            block, by_id = source_block(b["sources"], budget)
            try:
                proposed, u = run_gateway(b["name"], block, max_claims)
            except RuntimeError as e:
                b["error"] = str(e)[:300]
                continue
            ran += 1
            usage.update({k: int(v or 0) for k, v in u.items() if isinstance(v, (int, float))})
            b["claims"] = check_claims(proposed, by_id, set(keys), stats)
        if brands and not ran and any(b["sources"] for b in brands):
            raise HTTPException(502, "every LLM call failed: " + "; ".join(
                f"{b['name']}: {b.get('error')}" for b in brands if b.get("error"))[:600])
        cells = build_matrix(brands, keys)
        snap = {
            "month": month, "built_at": now(), "themes": [{"key": k, "description": v} for k, v in themes().items()],
            "brands": [{"name": b["name"], "kind": b["kind"], "competitor_id": b["competitor_id"],
                        "sources": b["sources_n"], "pages": sum(1 for s in b["sources"] if s["kind"].startswith("page")),
                        "ads": sum(1 for s in b["sources"] if s["kind"] == "ad"),
                        "facts": sum(1 for s in b["sources"] if s["kind"] == "fact"),
                        "error": b.get("error")} for b in brands],
            "cells": cells,
            "stats": dict(stats), "usage": dict(usage), "warnings": warnings,
        }
        snap.update(classify(cells, snap["brands"], ours["name"] if ours["sources"] else None))
        prev = conn.execute("SELECT * FROM positioning WHERE month < ? ORDER BY month DESC LIMIT 1", (month,)).fetchone()
        snap["previous_month"] = json.loads(prev["snapshot"])["month"] if prev else None
        snap["_corpus"] = {b["name"]: corpus(b["sources"]) for b in brands if not b.get("error")}
        snap["shifts"], snap["shifts_suppressed"] = shifts(json.loads(prev["snapshot"]) if prev else None, snap)
        snap["markdown"] = markdown(snap)
        with conn:
            conn.execute("DELETE FROM positioning WHERE month = ?", (month,))
            cur = conn.execute("INSERT INTO positioning (month, built_at, snapshot) VALUES (?, ?, ?)",
                               (month, snap["built_at"], json.dumps(snap)))
        snap["id"] = cur.lastrowid
    snap.pop("_corpus")
    return snap


@router.get("/positioning")
def list_positioning():
    with closing(_db()) as conn:
        rows = conn.execute("SELECT * FROM positioning ORDER BY month DESC").fetchall()
    out = []
    for r in rows:
        s = _load(r)
        out.append({"id": s["id"], "month": s["month"], "built_at": s["built_at"],
                    "brands": [b["name"] for b in s["brands"]], "white_space": [w["theme"] for w in s["white_space"]],
                    "crowded": [c["theme"] for c in s["crowded"]], "shifts": len(s["shifts"])})
    return out


@router.get("/positioning/latest")
def latest_positioning():
    with closing(_db()) as conn:
        row = conn.execute("SELECT * FROM positioning ORDER BY month DESC LIMIT 1").fetchone()
    if row is None:
        raise HTTPException(404, "no positioning map yet: POST /positioning/build")
    return _load(row)


@router.get("/positioning/diff")
def diff_positioning(from_: int = Query(alias="from"), to: int = Query()):
    """Shifts between two stored snapshots (by id), older first."""
    with closing(_db()) as conn:
        a = conn.execute("SELECT * FROM positioning WHERE id = ?", (from_,)).fetchone()
        b = conn.execute("SELECT * FROM positioning WHERE id = ?", (to,)).fetchone()
    if a is None or b is None:
        raise HTTPException(404, "snapshot not found")
    old, new = _load(a, corpus=True), _load(b, corpus=True)
    if old["month"] > new["month"]:
        old, new = new, old
    return {"from": {"id": old["id"], "month": old["month"]}, "to": {"id": new["id"], "month": new["month"]},
            "shifts": shifts(old, new)[0]}


@router.get("/positioning/{sid}")
def get_positioning(sid: int):
    with closing(_db()) as conn:
        row = conn.execute("SELECT * FROM positioning WHERE id = ?", (sid,)).fetchone()
    if row is None:
        raise HTTPException(404, f"positioning snapshot {sid} not found")
    return _load(row)
