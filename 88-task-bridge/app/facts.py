"""Fact helpers shared by the pack, slots and evidence: validity on a day and scope match,
exactly as the phase-1 contract (section 1) defines them. Facts are the v2 dicts 05 returns."""
import re
from datetime import date

DIMENSIONS = ("sites", "regions", "channels", "segments", "plan_tiers", "variants")
SENSITIVITY_RANK = {"public": 0, "internal": 1, "restricted": 2}
KEY_RE = re.compile(r"^[a-z0-9-]{2,60}$")

STOP = frozenset("""a an and are as at be by for from has have in into is it its of on or our per
so that the their them then there these this to up us was we were will with you your yours
all any can more most new now only just also than very get got out off over""".split())


def empty_scope() -> dict:
    return {d: [] for d in DIMENSIONS}


def norm_scope(scope: dict | None) -> dict:
    out = empty_scope()
    for d in DIMENSIONS:
        vals = (scope or {}).get(d) or []
        out[d] = [str(v).strip().lower() for v in vals if str(v).strip()]
    return out


def _day(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def validity(fact: dict, day: date) -> str | None:
    """None when the fact is valid on `day`, else the reason (contract names)."""
    status = fact.get("status") or "active"
    if status != "active":
        return status if status in ("draft", "superseded", "retired", "expired") else "draft"
    start, end = _day(fact.get("valid_from")), _day(fact.get("valid_to"))
    if start and day < start:
        return "not_yet_valid"
    if end and day > end:
        return "expired"
    return None


def scope_reason(fact: dict, scope: dict) -> str | None:
    """None when the fact applies to the task scope, else out_of_scope / scope_unspecified."""
    fs = norm_scope(fact.get("scope"))
    ts = norm_scope(scope)
    unspecified = False
    for d in DIMENSIONS:
        if not fs[d]:
            continue
        if not ts[d]:
            unspecified = True
            continue
        if any(v not in fs[d] for v in ts[d]):
            return "out_of_scope"
    return "scope_unspecified" if unspecified else None


def classify(fact: dict, day: date, scope: dict) -> str | None:
    """None = usable for this task on this day. Restricted facts are never usable in copy."""
    if (fact.get("sensitivity") or "public") == "restricted":
        return "restricted"
    return validity(fact, day) or scope_reason(fact, scope)


def scope_text(fact: dict) -> str:
    fs = norm_scope(fact.get("scope"))
    parts = [f"{d.replace('_', ' ')}: {', '.join(fs[d])}" for d in DIMENSIONS if fs[d]]
    return "; ".join(parts)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", (text or "").lower())


def content_words(text: str) -> set[str]:
    return {w for w in words(text) if len(w) >= 3 and w not in STOP and not w.isdigit()}


def subject_ref(fact: dict) -> str:
    s = fact.get("subject") or {}
    return str(s.get("ref") or "").strip() if isinstance(s, dict) else ""


def subject_kind(fact: dict) -> str:
    s = fact.get("subject") or {}
    return str(s.get("kind") or "") if isinstance(s, dict) else ""


def phrases(values) -> list[str]:
    """forbidden/allowed phrasing may be strings or {"phrase", "why"} dicts (starter kits)."""
    out = []
    for v in values or []:
        p = v.get("phrase") if isinstance(v, dict) else v
        if isinstance(p, str) and p.strip():
            out.append(p.strip())
    return out


def parse_day(v: str) -> date:
    return date.fromisoformat(v)


# Sentences: a line break always ends one; ". ", "! ", "? " end one when the next thing looks
# like a new sentence. "£1.2k", "e.g. this" and "v2.1" stay whole.
_BOUNDARY = re.compile(r"(?<=[.!?…])[\"'”’)]*\s+(?=(?:[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+\s*)?"
                       r"[\"'“‘(\[]?[A-Z0-9£$€#@*•-])|\n+")
_ABBREV = re.compile(r"(?:\b(?:e\.g|i\.e|etc|vs|approx|incl|excl|Dr|Mr|Mrs|Ms|St|No|Ltd|Co|min|max)\.)$", re.I)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in _BOUNDARY.finditer(text):
        if m.group(0).strip() == "" and "\n" not in m.group(0) and _ABBREV.search(text[start:m.start()]):
            continue
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    out = []
    for a, b in spans:
        seg = text[a:b]
        lead = len(seg) - len(seg.lstrip())
        seg = seg.strip()
        if seg:
            out.append((a + lead, a + lead + len(seg)))
    return out


def sentence_at(text: str, pos: int) -> str:
    for a, b in sentence_spans(text):
        if a <= pos < b or (pos == b and b == len(text.rstrip())):
            return text[a:b]
    return text.strip()[:300]
