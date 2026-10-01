"""Public content drift audit: what the business's own pages, PDFs and old posts say now, against
today's facts. Read-only: nothing is written, no calendar item, no task.

POST /audit {sources: [{"url"} | {"text", "label"}], scope, on} -> per source the sentences that
CONTRADICT a current fact (a price that was true last year, an expired offer, a value that belongs to
another site) or write an internal/restricted value on a public page, with what the source says and
what the facts say now. Matches are only counted. URLs are read through the page extractor (07,
EXTRACTOR_URL), which is SSRF-guarded and reads HTML and text PDFs; an image-only PDF comes back
empty and is reported as such. The sentence checks are the same ones /check runs
(evidence.check_text), so a page and a post are judged alike.

Wired from main.py with `app.include_router(audit.make_router(...))`, passing its auth and rate limit.
"""
from datetime import date

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import evidence, services
from . import facts as F

MAX_SOURCES = 10
MAX_TOTAL_CHARS = 200_000
EXTRACTOR_CAP = 20_000          # 07 caps the text it returns at this many characters
DRIFT_LABELS = {"conflict_or_expired", "wrong_scope", "slot_blocked"}
EMPTY_NOTE = "no text found (image-only PDF?)"


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=MAX_TOTAL_CHARS)
    label: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def one_of(self):
        if bool(self.url and self.url.strip()) == bool(self.text and self.text.strip()):
            raise ValueError("give exactly one of url or text")
        if self.url and not self.url.strip().lower().startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return self


class AuditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[Source] = Field(min_length=1, max_length=MAX_SOURCES)
    scope: dict = Field(default_factory=dict)
    on: str | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.on:
            try:
                date.fromisoformat(self.on)
            except ValueError:
                raise ValueError("on: a date like 2026-10-01") from None
        for d, vals in self.scope.items():
            if d not in F.DIMENSIONS or not isinstance(vals, list) or len(vals) > 20 \
                    or any(not isinstance(v, str) or len(v) > 80 for v in vals):
                raise ValueError(f"scope.{d}: unknown dimension or not a short list of text")
        return self


def extractor_url() -> str:
    import os
    return (os.environ.get("EXTRACTOR_URL") or "").strip().rstrip("/")


def fetch_text(url: str) -> tuple[str, str | None]:
    """(text, note) for a URL through 07. Raises services.ServiceError with a plain reason."""
    base = extractor_url()
    if not base:
        raise services.ServiceError("EXTRACTOR_URL is not set: pages and PDFs cannot be read (paste the text instead)")
    try:
        r = httpx.post(f"{base}/extract", json={"url": url}, headers={**services._key_headers(), "X-Caller": "88 task bridge"},
                       timeout=services._timeout("EXTRACTOR_TIMEOUT", 45.0))
    except httpx.HTTPError as exc:
        raise services.ServiceError(f"page extractor unreachable ({type(exc).__name__})") from exc
    if r.status_code >= 300:
        why = ""
        try:
            d = r.json().get("detail")
            why = f": {d if isinstance(d, str) else ''}"[:200] if d else ""
        except (ValueError, AttributeError):
            pass
        raise services.ServiceError(f"could not read the page (extractor HTTP {r.status_code}{why})")
    try:
        body = r.json()
    except ValueError as exc:
        raise services.ServiceError("page extractor returned no JSON") from exc
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise services.ServiceError("page extractor returned no text")
    pages = body.get("pages")
    if isinstance(pages, list) and pages:      # a PDF: keep its lines, page by page
        joined = "\n".join(str(p.get("text") or "") for p in pages if isinstance(p, dict))
        text = joined if len(joined) >= len(text) else text
    note = None
    if text.strip() and len(text) >= EXTRACTOR_CAP:
        note = f"only the first {EXTRACTOR_CAP:,} characters were read (the extractor's limit)"
    return text, note


def _says(sentence: str, fact: dict | None) -> str:
    """The value in the sentence that the fact does not have; else the sentence."""
    try:
        if fact:
            own = set().union(*evidence.fact_values(fact))
            odd = [v.text for v in evidence.extract(sentence) if v.kind in ("money", "percent", "qty") and v.key not in own]
            if odd:
                return ", ".join(dict.fromkeys(odd))[:120]
    except Exception:           # a wording hint must never break an audit
        pass
    return sentence[:300]


def _public(f: dict) -> bool:
    return (f.get("sensitivity") or "public") == "public"


def _current(fact: dict, known: dict, day: date, scope: dict) -> dict | None:
    """The fact that holds today for what `fact` says: itself when valid in scope, else the fact that
    supersedes it, else a valid in-scope fact about the same subject and attribute."""
    def live(f):
        return F.classify(f, day, scope) is None and _public(f)
    if live(fact):
        return fact
    key = fact["key"]
    for f in known.values():
        if f.get("supersedes_key") == key and live(f):
            return f
    ref = F.subject_ref(fact).lower()
    attr = str(fact.get("attribute") or "").strip().lower()
    if not ref:
        return None
    same = [f for f in known.values() if f["key"] != key and live(f) and F.subject_ref(f).lower() == ref
            and (not attr or str(f.get("attribute") or "").strip().lower() == attr)]
    same.sort(key=lambda f: f.get("fact_type") != fact.get("fact_type"))
    return same[0] if same else None


def audit_text(text: str, known: dict, day: date, scope: dict) -> dict:
    """{sentences_checked, drift, ok_matches} for one source's text."""
    findings, _used = evidence.check_text(text, list(known.values()), day, scope, [])
    drift, seen, matched = [], set(), set()
    for x in findings:
        if x["label"] == "match":
            matched.add(x["sentence"])
        if x["label"] not in DRIFT_LABELS:
            continue
        k = (x["sentence"], x.get("fact_key"), x["label"])
        if k in seen:
            continue
        seen.add(k)
        fact = known.get(x.get("fact_key") or "")
        leak = x["label"] == "slot_blocked"
        cur = None if leak or not fact else _current(fact, known, day, scope)
        drift.append({"sentence": x["sentence"], "label": x["label"], "fact_key": x.get("fact_key"),
                      "says": _says(x["sentence"], fact), "should_say": (cur.get("value_text") or cur.get("text")) if cur else None,
                      "current_fact_key": cur["key"] if cur else None,
                      "leak": leak, "detail": str(x.get("detail") or "")[:300]})
    return {"sentences_checked": len(F.sentence_spans(text)), "drift": drift,
            "ok_matches": len(matched - {d["sentence"] for d in drift})}


def make_router(require_key, rate_limit, piece_scope, known_facts) -> APIRouter:
    router = APIRouter()

    @router.post("/audit", dependencies=[Depends(require_key), Depends(rate_limit)])
    def audit(req: AuditIn):
        """Read-only drift audit of public text against the facts valid on `on` (default today)."""
        total = sum(len(s.text) for s in req.sources if s.text)
        if total > MAX_TOTAL_CHARS:
            raise HTTPException(413, f"the pasted text is longer than {MAX_TOTAL_CHARS:,} characters in all")
        day_s = req.on or date.today().isoformat()
        day = date.fromisoformat(day_s)
        scope = piece_scope(req.scope, None)
        try:
            q = services.query_facts(scope, day_s)
            known = known_facts(q, services.all_facts())
        except services.ServiceError as exc:
            raise HTTPException(503 if "is not set" in str(exc) else 502, str(exc)) from None
        budget = MAX_TOTAL_CHARS - total
        out = []
        for i, s in enumerate(req.sources, 1):
            name = s.url.strip() if s.url else (s.label or f"pasted text {i}")
            row = {"source": name, "kind": "url" if s.url else "text", "sentences_checked": 0, "drift": [],
                   "ok_matches": 0, "error": None, "note": None}
            text = s.text
            if s.url:
                try:
                    text, row["note"] = fetch_text(s.url.strip())
                except services.ServiceError as exc:
                    row["error"] = str(exc)
                    out.append(row)
                    continue
                if len(text) > budget:
                    row["error"] = f"over the {MAX_TOTAL_CHARS:,}-character limit for one audit: run this source on its own"
                    out.append(row)
                    continue
                budget -= len(text)
            if not (text or "").strip():
                row["note"] = EMPTY_NOTE
                out.append(row)
                continue
            try:
                row.update(audit_text(text, known, day, scope))
            except Exception:       # one odd source must not take the others down
                row["error"] = "this text could not be checked"
            out.append(row)
        return {"on": day_s, "scope": scope, "fact_set_version": q.get("fact_set_version"), "sources": out,
                "totals": {"sources": len(out), "drift": sum(len(r["drift"]) for r in out),
                           "errors": sum(1 for r in out if r["error"])}}

    return router
