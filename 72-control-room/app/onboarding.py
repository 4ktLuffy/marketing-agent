"""Set up from your website or documents: proposed facts from the business's own pages, brochures
and price lists, checked in code, kept only when the owner ticks them, saved as DRAFTS.

Flow: website URL(s), PDF upload(s) or pasted text -> 07 page-extractor (text, page by page for a
PDF) -> chunks of about CHUNK_CHARS -> proposals from EITHER
  (a) the model: 03 gateway `/v1/run` prompt `propose_facts`, once per chunk (only when the gateway
      answers and has that prompt), OR
  (b) any free chatbot: a copyable pack per chunk (instructions + the chunk + the exact line format);
      the person pastes the answer back and `parse_answer` reads it tolerantly.
Every proposal is checked HERE, whoever wrote it (`check`): its source_quote must occur in the
source text (whitespace, case, quote marks and dashes normalised), every number of its value must be
in that quote, every other number in its sentence must be in the source page, and a date survives
only if the quote mentions it. Duplicates of existing facts (05 `/facts/v2`) and of each other are
set aside with the reason. The owner ticks what to keep -> 05 `POST /facts/v2` as DRAFTS (sensitivity
as chosen, default internal; source {kind: url|doc, ref: where + the quote}); questions go to 05
`POST /questions`. Nothing is confirmed here: that stays the owner-key step on the Facts page.

Nothing here reaches the browser but the page: no key, no internal URL. The source text lives in
this process only (`Store`, a few hours); a restart means starting again.
"""
import base64
import re
import secrets
import time
import unicodedata
from datetime import date

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .backends import BackendError
from .facts_page import DIMS, FACT_TYPES, SENSITIVITY, SUBJECT_KINDS, scope_words, slug

CHUNK_CHARS = 4200          # chunk + instructions + known facts must stay within 8,000 characters (free chats; test)
MAX_URLS = 5
MAX_PDFS = 5
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PASTE = 100_000         # pasted source text
MAX_ANSWER = 60_000         # a chatbot's answer
MAX_TOTAL = 300_000         # all sources together
MAX_PROPOSALS = 300
MODEL_BATCH = 3             # parts per "ask the model" click: a local model takes about a minute each
MIN_QUOTE = 8               # normalised characters; "£25" alone proves nothing
KNOWN_MAX_CHARS = 1800      # known public facts listed in a pack or prompt
SID = re.compile(r"^[A-Za-z0-9_-]{20,40}$")
BASES = {"per_unit", "per_person", "per_room", "per_night", "per_seat", "per_month", "per_year", "flat"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]

LINE_FORMAT = 'FACT | subject kind | subject | type | value as written | scope | valid from | valid to | quote: "..."'
QUESTION_FORMAT = 'QUESTION | your question to the owner | quote: "..."'


# ------------------------------------------------------------------ text helpers

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'",
                         "´": "'", "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"',
                         "»": '"', "″": '"', "‐": "-", "‑": "-", "‒": "-", "–": "-",
                         "—": "-", "―": "-", "−": "-", "­": None, "​": None})


def norm(s: str) -> str:
    """For comparing quotes: NFKC (ligatures, no-break spaces, "…"), one kind of quote mark and dash,
    lower case, whitespace collapsed."""
    s = unicodedata.normalize("NFKC", str(s or "")).translate(_QUOTES).lower()
    return " ".join(s.split())


def clean_quote(q: str) -> tuple[str, str | None]:
    """(quote without its wrapping, problem or None). Wrapping = quote marks, "quote:", an ellipsis
    at either end. An ellipsis in the middle means it was shortened: not word for word."""
    q = str(q or "").strip()
    q = re.sub(r"^(?:source[ _]?)?quote\s*[:=\-]\s*", "", q, flags=re.I).strip()
    for _ in range(3):
        q = q.strip().strip("\"'“”‘’«»`").strip()
        q = re.sub(r"^(?:\.\.\.|…)\s*|\s*(?:\.\.\.|…)$", "", q)
    if re.search(r"\.\.\.|…|\[\.\.\.\]", q):
        return q, "the quote was shortened with '...': it must be copied word for word"
    return q, None


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def numbers(s: str) -> set[str]:
    """Numbers as written, without thousands separators: "£1,250.00" -> {"1250.00", "1250"}."""
    out = set()
    for m in _NUM.findall(unicodedata.normalize("NFKC", str(s or ""))):
        flat = m.replace(",", "")
        out.add(flat)
        if "." in flat and set(flat.split(".", 1)[1]) <= {"0"}:
            out.add(flat.split(".", 1)[0])       # "25.00" in a value may be "25" in the quote
    return out


def _has_number(n: str, pool: set[str]) -> bool:
    return n in pool or (n.split(".", 1)[0] in pool and set(n.split(".", 1)[-1]) <= {"0"})


def guess_basis(value_text: str) -> str | None:
    v = norm(value_text)
    for pat, basis in ((r"\bper (?:person|guest|adult|child|head)\b|\bpp\b|\bpppn\b", "per_person"),
                       (r"\bper room\b", "per_room"), (r"\bper (?:seat|user)\b", "per_seat"),
                       (r"\bper night\b|/ ?night\b", "per_night"),
                       (r"\bper month\b|/ ?(?:mo|month)\b|\ba month\b|\bmonthly\b", "per_month"),
                       (r"\bper (?:year|annum)\b|/ ?(?:yr|year)\b|\ba year\b|\bannual(?:ly)?\b", "per_year"),
                       (r"\bper (?:unit|item|piece)\b|\beach\b", "per_unit")):
        if re.search(pat, v):
            return basis
    return None


def _date(v) -> str | None:
    v = str(v or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        return None


def date_in_quote(day: str, quote: str) -> bool:
    """A date may be kept only if the quote itself mentions it (its year, or its month by name)."""
    d = date.fromisoformat(day)
    q = norm(quote)
    return str(d.year) in q or MONTHS[d.month - 1] in q or re.search(rf"\b{MONTHS[d.month - 1][:3]}\b", q) is not None


# ------------------------------------------------------------------ sources and chunks

def source_label(src: dict, page: int | None = None) -> str:
    return f"{src['ref']} p.{page}" if page else src["ref"]


def make_chunks(sources: list[dict], size: int = CHUNK_CHARS) -> list[dict]:
    """Chunks never cross sources. A PDF chunk marks its pages with "[page N]" lines."""
    chunks = []
    for si, src in enumerate(sources):
        paged = any(p.get("page") for p in src["pages"])
        units: list[tuple[int | None, str]] = []
        for p in src["pages"]:
            for para in re.split(r"\n\s*\n|\n", p.get("text") or ""):
                para = para.strip()
                while len(para) > size:
                    cut = max(para.rfind(". ", 0, size), para.rfind(" ", 0, size))
                    cut = cut + 1 if cut > size // 2 else size
                    units.append((p.get("page"), para[:cut].strip()))
                    para = para[cut:].strip()
                if para:
                    units.append((p.get("page"), para))
        cur, pages, last = [], [], object()
        for page, text in units:
            marker = f"[page {page}]" if paged and page != last else None
            add = (len(marker) + 1 if marker else 0) + len(text) + 1
            if cur and sum(len(x) + 1 for x in cur) + add > size:
                chunks.append({"source": si, "pages": pages, "text": "\n".join(cur)})
                cur, pages, last = [], [], object()
                marker = f"[page {page}]" if paged else None
            if marker:
                cur.append(marker)
                last = page
            if page and page not in pages:
                pages.append(page)
            cur.append(text)
        if cur:
            chunks.append({"source": si, "pages": pages, "text": "\n".join(cur)})
    for i, c in enumerate(chunks):
        c["n"] = i + 1
    return chunks


def find_quote(quote: str, sources: list[dict]) -> tuple[int, int | None] | None:
    """(source index, page) where the quote occurs, normalised; None if nowhere."""
    q = norm(quote)
    if not q:
        return None
    for si, src in enumerate(sources):
        for p in src["pages"]:
            if q in norm(p.get("text") or ""):
                return si, p.get("page")
    return None


def context(quote: str, text: str, around: int = 120) -> tuple[str, str, str]:
    """(before, the quote as the source writes it, after), to show the quote highlighted in place."""
    base = unicodedata.normalize("NFKC", text or "").translate(_QUOTES)
    words = unicodedata.normalize("NFKC", quote).translate(_QUOTES).split()
    m = re.search(r"\s+".join(re.escape(w) for w in words), base, re.I) if words else None
    if not m:
        return "", quote, ""
    a, b = m.span()
    before = ("…" if a > around else "") + " ".join(base[max(0, a - around):a].split())
    after = " ".join(base[b:b + around].split()) + ("…" if len(base) > b + around else "")
    return before + (" " if before and base[a - 1:a].isspace() else ""), " ".join(m.group(0).split()), \
        (" " if after and base[b:b + 1].isspace() else "") + after


def page_text(sources: list[dict], si: int, page: int | None) -> str:
    for p in sources[si]["pages"]:
        if p.get("page") == page:
            return p.get("text") or ""
    return ""


# ------------------------------------------------------------------ existing facts: dedupe

def existing_index(facts: list[dict]) -> dict:
    """Signatures of the facts 05 has (any status, derived ones too) for spotting duplicates."""
    texts, values = {}, {}
    for f in facts:
        key = str(f.get("key") or "")
        if f.get("text"):
            texts.setdefault(norm(f["text"]), key)
        subj = (f.get("subject") or {}).get("ref") or ""
        if f.get("value_text"):
            values.setdefault((norm(subj), norm(f["value_text"])), key)
    return {"texts": texts, "values": values, "keys": {str(f.get("key")) for f in facts if f.get("key")}}


def duplicate_of(p: dict, index: dict) -> str | None:
    if norm(p["text"]) in index["texts"]:
        return index["texts"][norm(p["text"])]
    if p.get("value_text"):
        return index["values"].get((norm(p["subject"]["ref"]), norm(p["value_text"])))
    return None


def known_lines(facts: list[dict]) -> str:
    """Known facts for a pack or the prompt: PUBLIC ones only (an internal fact never leaves as text)."""
    out, n = [], 0
    for f in facts:
        if f.get("sensitivity") != "public" or f.get("status") not in ("active", "draft") or not f.get("text"):
            continue
        line = "- " + " ".join(str(f["text"]).split())[:300]
        if n + len(line) > KNOWN_MAX_CHARS:
            break
        out.append(line)
        n += len(line) + 1
    return "\n".join(out)


# ------------------------------------------------------------------ checking one proposal

KIND_WORDS = {"branch": "site", "location": "site", "store": "site", "shop": "site", "venue": "site",
              "company": "business", "brand": "business", "tier": "plan", "subscription": "plan",
              "menu item": "menu_item", "menu-item": "menu_item", "dish": "menu_item", "room": "package",
              "promotion": "offer", "deal": "offer", "staff": "person", "model": "variant"}
TYPE_WORDS = {"pricing": "price", "prices": "price", "fee": "price", "fees": "price", "rate": "price",
              "cost": "price", "opening hours": "hours", "opening_hours": "hours", "included": "inclusion",
              "includes": "inclusion", "rule": "policy", "terms": "policy", "certificate": "certification",
              "specification": "spec", "contact details": "contact", "credentials": "credential"}
SCOPE_WORDS = {"site": "sites", "sites": "sites", "branch": "sites", "branches": "sites", "location": "sites",
               "store": "sites", "region": "regions", "regions": "regions", "area": "regions", "country": "regions",
               "channel": "channels", "channels": "channels", "customers": "segments", "customer": "segments",
               "segment": "segments", "segments": "segments", "group": "segments", "plan": "plan_tiers",
               "plans": "plan_tiers", "tier": "plan_tiers", "plan_tiers": "plan_tiers", "variant": "variants",
               "variants": "variants", "size": "variants", "model": "variants"}


def _kind(v) -> str:
    v = str(v or "").strip().lower()
    v = KIND_WORDS.get(v, v).replace(" ", "_").replace("-", "_")
    return v if v in SUBJECT_KINDS else "business"


def _ftype(v) -> str:
    v = str(v or "").strip().lower()
    v = TYPE_WORDS.get(v, v).replace(" ", "_")
    return v if v in FACT_TYPES else "claim"


def _scope(hints) -> dict:
    out = {d: [] for d, _ in DIMS}
    if isinstance(hints, dict):
        for d, _ in DIMS:
            vals = hints.get(d)
            if isinstance(vals, str):
                vals = [vals]
            if isinstance(vals, list):
                out[d] = list(dict.fromkeys(str(x).strip()[:80] for x in vals if str(x or "").strip()))[:50]
    return out


def _s(v, n: int) -> str:
    return " ".join(str(v).split())[:n] if v not in (None, "") else ""


def check(raw: dict, sources: list[dict], index: dict, taken: list[dict], origin: str) -> tuple[dict | None, str | None]:
    """A raw proposal (model JSON or a parsed pack line) -> (proposal, None) or (None, why not)."""
    if not isinstance(raw, dict):
        return None, "not a fact"
    quote, problem = clean_quote(raw.get("source_quote") or "")
    subject = raw.get("subject") if isinstance(raw.get("subject"), dict) else {}
    p = {"subject": {"kind": _kind(subject.get("kind")), "ref": _s(subject.get("ref"), 120)},
         "fact_type": _ftype(raw.get("fact_type")), "attribute": _s(raw.get("attribute"), 60),
         "value_text": _s(raw.get("value_text"), 200), "quote": quote[:400], "origin": origin, "notes": []}
    text = _s(raw.get("text"), 500)
    if not text:
        text = quote if (not p["subject"]["ref"] or norm(p["subject"]["ref"]) in norm(quote)) else f"{p['subject']['ref']}: {quote}"
    cond = _s(raw.get("conditions_text"), 300)
    if cond and norm(cond) not in norm(text):
        text = f"{text.rstrip('.')} ({cond})."
    p["text"] = text[:500]
    p["label"] = p["text"]
    if problem:
        return None, problem
    if len(norm(quote)) < MIN_QUOTE:
        return None, "the quote is missing or too short to check"
    where = find_quote(quote, sources)
    if where is None:
        return None, "the quote is not in the source text (not copied word for word, or invented)"
    si, page = where
    qnums, pnums = numbers(quote), numbers(page_text(sources, si, page))
    for n in sorted(numbers(p["value_text"])):
        if not _has_number(n, qnums):
            return None, f"the value has {n}, which the quote does not say"
    for n in sorted(numbers(p["text"])):
        if not _has_number(n, qnums | pnums):
            return None, f"the sentence has {n}, which the source does not say"
    src = sources[si]
    p["source"] = {"kind": src["kind"], "ref": source_label(src, page)}
    p["where"] = source_label(src, page)
    p["context"] = context(quote, page_text(sources, si, page))
    p["scope"] = _scope(raw.get("scope_hints"))
    for name in ("valid_from", "valid_to"):
        day = _date(raw.get(name))
        if raw.get(name) and not day:
            p["notes"].append(f"{name.replace('_', ' ')} left out: not a date like 2026-10-01")
        elif day and not date_in_quote(day, quote):
            p["notes"].append(f"{name.replace('_', ' ')} {day} left out: the quote does not state it")
            day = None
        p[name] = day
    if p["valid_from"] and p["valid_to"] and p["valid_from"] > p["valid_to"]:
        p["notes"].append("the dates were the wrong way round: left out")
        p["valid_from"] = p["valid_to"] = None
    basis = raw.get("basis") if raw.get("basis") in BASES else guess_basis(p["value_text"])
    p["basis"] = basis
    val = raw.get("value")
    p["value"] = val if isinstance(val, (int, float)) and not isinstance(val, bool) else (
        _s(val, 200) if isinstance(val, str) and val.strip() else None)
    if p["value"] is not None and not _has_number(str(p["value"]), qnums) and isinstance(p["value"], (int, float)):
        p["value"] = None      # a number the quote does not have is not kept, even as a field
    cur = str(raw.get("currency") or "").strip().upper()
    p["currency"] = cur if re.fullmatch(r"[A-Z]{3}", cur) else None
    p["unit"] = _s(raw.get("unit"), 30) or None
    hint = _s(raw.get("required_disclosure_hint"), 200)
    p["disclosures"] = [hint] if hint else []
    dup = duplicate_of(p, index)
    if dup:
        return None, f"already in your facts ({dup})"
    for i, t in enumerate(taken):
        if norm(t["text"]) == norm(p["text"]) or (p["value_text"] and norm(t["value_text"]) == norm(p["value_text"])
                                                  and norm(t["subject"]["ref"]) == norm(p["subject"]["ref"])):
            return None, f"same as proposal {i + 1}"
    p["scope_words"] = scope_words(p["scope"])
    return p, None


def check_question(raw: dict, sources: list[dict], origin: str) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict):
        return None, "not a question"
    text = _s(raw.get("text"), 300)
    if len(text) < 3:
        return None, "the question is empty"
    quote, problem = clean_quote(raw.get("source_quote") or "")
    if problem:
        return None, problem
    where = None
    if quote:
        where = find_quote(quote, sources)
        if where is None:
            return None, "the quote is not in the source text"
    return {"text": text, "quote": quote[:400], "origin": origin,
            "where": source_label(sources[where[0]], where[1]) if where else ""}, None


# ------------------------------------------------------------------ the pack (any free chatbot)

def build_pack(chunk: dict, total: int, source: dict, business_type: str, known: str) -> str:
    """Plain text to paste into any chat. Holds the chunk, the public known facts and the format."""
    what = "document" if source["kind"] == "doc" else "website page"
    lines = [
        f"You help a business set up its list of facts. Below is text from the business's own {what}.",
        "List the facts it states: prices, opening hours, what is included, policies, services, specifications,",
        "certifications, locations, contact details.",
        "",
        "Rules:",
        "- Only facts stated in the text. Never infer, work out or round a price, a number or a date.",
        "- One fact per line. A price for one branch, product, plan or season is its own fact.",
        '- Keep the scope words with the fact: "at our Leeds branch", "per person", "weekdays only".',
        '- The quote must be copied exactly from the text: same words, same numbers. Never shorten it with "...".',
        '- Something mentioned without its price or value (like "airport transfers available"): write a QUESTION line.',
        "- Skip facts listed under \"Already known\".",
        "- Answer ONLY with lines in this format, one per line, nothing else:",
        "",
        LINE_FORMAT,
        QUESTION_FORMAT,
        "",
        "subject kind: " + ", ".join(SUBJECT_KINDS),
        "type: " + ", ".join(FACT_TYPES),
        'scope: "everywhere", or like "site: Leeds; customers: students; channel: online; plan: Pro; variant: XL; region: UK"',
        'valid from / valid to: YYYY-MM-DD only when the text states the date, otherwise "-"',
        "",
        "Example of the format (not about this business):",
        'FACT | service | Bike hire | price | £12 per person per day | site: Harbour shop | - | - | quote: "Bike hire at our Harbour shop: £12 per person per day."',
        'QUESTION | What does delivery cost? | quote: "Delivery available."',
        "",
    ]
    if business_type:
        lines += [f"Type of business: {business_type}", ""]
    if known:
        lines += ["Already known (do not repeat):", known, ""]
    lines += [f"Text (part {chunk['n']} of {total}, from {source['ref']}):", '"""', chunk["text"], '"""']
    return "\n".join(lines)


# ------------------------------------------------------------------ reading a chatbot's answer

_EMPTY = {"", "-", "--", "—", "–", "none", "n/a", "na", "null", "unknown", "not stated", "not given", "?", "tbc"}
_HEAD = re.compile(r"^\s*(FACTS?|QUESTIONS?|Q|MISSING)\b\s*[:\-–—]?\s*(.*)$", re.I)


def _cell(v: str) -> str:
    v = v.strip().strip("*_`").strip()
    return "" if v.lower().strip(" .") in _EMPTY else v


def parse_scope(text: str) -> tuple[dict, str]:
    """"site: Leeds; customers: students" -> scope dict; words it cannot place -> conditions text."""
    scope = {d: [] for d, _ in DIMS}
    rest = []
    t = _cell(text)
    if t.lower() in ("everywhere", "all", "any", "global", "everyone"):
        return scope, ""
    for part in re.split(r"[;\n]|,(?=\s*[A-Za-z_ ]{2,20}\s*[:=])", t):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([A-Za-z_ ]{2,20})\s*[:=]\s*(.+)$", part)
        dim = SCOPE_WORDS.get(m.group(1).strip().lower()) if m else None
        if dim:
            for v in re.split(r",|\bor\b|/", m.group(2)):
                v = v.strip().strip("\"'")
                if v and v.lower() not in _EMPTY and v not in scope[dim]:
                    scope[dim].append(v[:80])
        else:
            rest.append(part)
    return scope, "; ".join(rest)


def parse_answer(text: str) -> tuple[list[dict], list[dict], list[str]]:
    """A chatbot's answer -> (raw facts, raw questions, problems). Tolerates preambles, sign-offs,
    code fences, bullets and numbering, bold, markdown tables, smart quotes, missing "quote:", a
    "FACT:" label, lower case, empty cells written as "-" / "n/a", and pipes inside the quote."""
    facts, questions, problems = [], [], []
    for n, line in enumerate(str(text or "")[:MAX_ANSWER].splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("```") or re.fullmatch(r"[|:\-\s]+", s):
            continue
        s = s.replace("\\|", "|").replace("｜", "|").replace("¦", "|").replace("\t", " | ")
        s = re.sub(r"^(?:[-*•>]+|\d{1,3}[.)])\s+", "", s)
        s = s.replace("**", "").replace("__", "").strip().strip("`")
        if "|" not in s:
            continue   # preamble, sign-off, a heading
        s = s.strip().strip("|").strip()
        cells = [c.strip() for c in s.split("|")]
        m = _HEAD.match(cells[0])
        if not m:
            if len(cells) >= 5:
                problems.append(f"Line {n} was not read: it does not start with FACT or QUESTION")
            continue
        head, rest = m.group(1).upper(), m.group(2).strip()
        cells = ([rest] if rest else []) + cells[1:]
        if cells and cells[0].lower().replace("_", " ") in ("subject kind", "your question to the owner", "question"):
            continue   # the header row of a table
        qi = next((i for i, c in enumerate(cells) if re.match(r"^\s*(?:source[ _]?)?quote\s*[:=\-]", c, re.I)), None)
        if qi is None:
            qi = len(cells) - 1
        quote = " | ".join(cells[qi:]) if cells else ""
        before = cells[:qi]
        if head.startswith("Q") or head == "MISSING":
            if not before and qi == 0 and cells:   # "QUESTION | what is the price?" with no quote
                before, quote = cells, ""
            questions.append({"text": _cell(before[0]) if before else "", "source_quote": quote})
            continue
        if len(before) < 4:
            problems.append(f"Line {n} was not read: too few columns for a FACT line")
            continue
        before = (before + [""] * 7)[:7]
        kind, subject, ftype, value, scope_text, vfrom, vto = (_cell(c) for c in before)
        scope, cond = parse_scope(scope_text)
        facts.append({"subject": {"kind": kind, "ref": subject}, "fact_type": ftype, "value_text": value,
                      "scope_hints": scope, "valid_from": vfrom or None, "valid_to": vto or None,
                      "conditions_text": cond or None, "source_quote": quote, "attribute": ""})
    return facts, questions, problems


# ------------------------------------------------------------------ in-memory work store

class Store:
    """sid -> one onboarding run (sources, chunks, proposals). Memory only, per process."""

    def __init__(self, ttl_s: float = 6 * 3600, max_runs: int = 20):
        self.ttl, self.max = ttl_s, max_runs
        self.runs: dict[str, dict] = {}

    def new(self, run: dict) -> str:
        now = time.time()
        self.runs = {k: v for k, v in self.runs.items() if now - v["at"] < self.ttl}
        while len(self.runs) >= self.max:
            self.runs.pop(min(self.runs, key=lambda k: self.runs[k]["at"]))
        sid = secrets.token_urlsafe(18)
        self.runs[sid] = {**run, "at": now}
        return sid

    def get(self, sid: str) -> dict | None:
        run = self.runs.get(sid) if SID.match(sid or "") else None
        if run and time.time() - run["at"] >= self.ttl:
            self.runs.pop(sid, None)
            return None
        return run


def add_results(run: dict, facts: list, questions: list, origin: str) -> tuple[int, int]:
    """Check raw proposals against the run's sources and add them. -> (kept, set aside)."""
    kept = aside = 0
    for raw in facts[:MAX_PROPOSALS]:
        if len(run["proposals"]) >= MAX_PROPOSALS:
            break
        p, why = check(raw, run["sources"], run["index"], run["proposals"], origin)
        if p:
            p["id"] = run["next_id"]
            run["next_id"] += 1
            run["proposals"].append(p)
            kept += 1
        else:
            aside += 1
            r = raw if isinstance(raw, dict) else {}
            subj = _s((r.get("subject") or {}).get("ref") if isinstance(r.get("subject"), dict) else "", 120)
            label = _s(r.get("text"), 200) or ": ".join(x for x in (subj, _s(r.get("value_text"), 200)) if x)
            run["rejected"].append({"label": label or "(no text)", "quote": clean_quote(r.get("source_quote") or "")[0][:300],
                                    "why": why, "origin": origin})
    for raw in questions[:30]:
        q, why = check_question(raw, run["sources"], origin)
        if q and norm(q["text"]) not in {norm(x["text"]) for x in run["questions"]}:
            q["id"] = run["next_id"]
            run["next_id"] += 1
            run["questions"].append(q)
        elif why:
            r = raw if isinstance(raw, dict) else {}
            run["rejected"].append({"label": _s(r.get("text"), 200) or "(question)",
                                    "quote": clean_quote(r.get("source_quote") or "")[0][:300], "why": why, "origin": origin})
    return kept, aside


def fact_body(p: dict, key: str, text: str, sensitivity: str, vfrom: str | None, vto: str | None) -> dict:
    """A kept proposal -> 05 Fact (contract §1). No status: 05 makes it a draft."""
    ref = f'{p["where"]}: "{p["quote"]}"'
    return {"key": key, "subject": p["subject"], "fact_type": p["fact_type"], "attribute": p["attribute"] or None,
            "value": p["value"], "unit": p["unit"], "currency": p["currency"], "value_text": p["value_text"] or None,
            "basis": p["basis"], "conditions": [], "scope": p["scope"], "valid_from": vfrom, "valid_to": vto,
            "review_by": None, "source": {"kind": p["source"]["kind"], "ref": ref[:300]}, "claim_class": "none",
            "required_disclosures": p["disclosures"], "sensitivity": sensitivity, "text": text}


def new_key(p: dict, taken: set[str]) -> str:
    base = slug(" ".join(x for x in (p["subject"]["ref"], p["attribute"] or p["fact_type"]) if x)) or "fact"
    base = base[:54].strip("-") or "fact"
    if len(base) < 2:
        base = f"{base}-fact"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    taken.add(key)
    return key


# ------------------------------------------------------------------ routes

def _why(e: BackendError) -> str:
    return re.sub(r"^HTTP \d+: ", "", e.detail)


def register(app, page, current, csrf, B):
    s = app.state.settings
    store = Store()
    app.state.onboarding = store

    def off(request, session):
        return page(request, "onboarding.html", session, nav="more", not_installed=True, step="start")

    async def model_state() -> tuple[bool, str]:
        """(usable, words). Usable only when the gateway answers AND has the propose_facts prompt."""
        if not s.gateway_url:
            return False, "No model is connected (the gateway is not installed)."
        try:
            health, prompts = await B().gateway_health(), await B().gateway_prompts()
        except BackendError:
            return False, "The model gateway does not answer right now."
        if "propose_facts" not in prompts:
            return False, "The model gateway does not have the propose_facts prompt yet (update 04)."
        hosted = health.get("provider") == "openai"
        return True, ("uses your hosted model: the source text goes to that provider" if hosted
                      else "uses your local model: nothing leaves this server")

    async def kits():
        try:
            return [(k.get("id"), k.get("label") or k.get("id")) for k in await B().starter_kits() if k.get("id")]
        except BackendError:
            return []

    @app.get("/facts/setup", response_class=HTMLResponse)
    async def setup_start(request: Request, session=Depends(current)):
        if not s.brand_url:
            return off(request, session)
        return page(request, "onboarding.html", session, nav="more", step="start", kits=await kits(),
                    extractor=bool(s.extractor_url), errors=[], values={})

    @app.post("/facts/setup", response_class=HTMLResponse)
    async def setup_sources(request: Request, session=Depends(csrf)):
        if not s.brand_url:
            return off(request, session)
        form = await request.form()
        urls = [u.strip() for u in re.split(r"[\s,]+", str(form.get("urls", ""))[:4000]) if u.strip()]
        pasted = str(form.get("text", "") or "")[:MAX_PASTE + 1]
        kit = str(form.get("business_type", "") or "")[:40]
        uploads = [f for f in form.getlist("pdfs") if hasattr(f, "read") and getattr(f, "filename", "")]
        errors, sources = [], []
        all_kits = await kits()
        values = {"urls": "\n".join(urls), "text": pasted[:MAX_PASTE], "business_type": kit}
        if len(urls) > MAX_URLS:
            errors.append(f"At most {MAX_URLS} web addresses at a time.")
        if len(uploads) > MAX_PDFS:
            errors.append(f"At most {MAX_PDFS} PDFs at a time.")
        if len(pasted) > MAX_PASTE:
            errors.append(f"Pasted text is at most {MAX_PASTE:,} characters.")
        if (urls or uploads) and not s.extractor_url:
            errors.append("Reading web pages and PDFs needs the page extractor (07), which isn't installed: paste the text instead.")
        if not (urls or uploads or pasted.strip()):
            errors.append("Give at least one web address, PDF or pasted text.")
        if not errors:
            for u in urls:
                if not re.match(r"^https?://", u, re.I):
                    errors.append(f"{u[:100]}: a web address starts with https://")
                    continue
                try:
                    r = await B().extract({"url": u})
                except BackendError as e:
                    errors.append(f"{u[:100]}: {_why(e)}")
                    continue
                sources.append(_source_from(r, "url", str(r.get("url") or u)))
            for f in uploads:
                name = re.sub(r"[^\w .()-]", "_", str(f.filename))[:120] or "upload.pdf"
                raw = await f.read(MAX_PDF_BYTES + 1)
                if len(raw) > MAX_PDF_BYTES:
                    errors.append(f"{name}: larger than 10 MB")
                    continue
                if not raw.lstrip()[:5].startswith(b"%PDF-"):
                    errors.append(f"{name}: not a PDF")
                    continue
                try:
                    r = await B().extract({"pdf_base64": base64.b64encode(raw).decode(), "filename": name})
                except BackendError as e:
                    errors.append(f"{name}: {_why(e)}")
                    continue
                sources.append(_source_from(r, "doc", name))
            if pasted.strip():
                sources.append({"kind": "doc", "ref": f"pasted text ({date.today().isoformat()})", "title": None,
                                "pages": [{"page": None, "text": pasted.strip()}]})
            sources = [x for x in sources if any((p.get("text") or "").strip() for p in x["pages"])]
            if sum(len(p["text"] or "") for x in sources for p in x["pages"]) > MAX_TOTAL:
                errors.append(f"All sources together are over {MAX_TOTAL:,} characters: start with fewer.")
            elif not sources and not errors:
                errors.append("No text was found in what you gave.")
        if errors:
            return page(request, "onboarding.html", session, 422, nav="more", step="start", kits=all_kits,
                        extractor=bool(s.extractor_url), errors=errors, values=values)
        try:
            existing = [f for f in (await B().facts_v2()).get("facts") or [] if isinstance(f, dict)]
        except BackendError as e:
            return page(request, "onboarding.html", session, 502, nav="more", step="start", kits=all_kits,
                        extractor=bool(s.extractor_url), values=values,
                        errors=[f"The brand service (05) did not answer: {_why(e)}"])
        label = dict(all_kits).get(kit, "")
        sid = store.new({"sources": sources, "chunks": make_chunks(sources, CHUNK_CHARS), "business_type": label,
                         "index": existing_index(existing), "known": known_lines(existing), "proposals": [],
                         "rejected": [], "questions": [], "problems": [], "done": [], "next_id": 1})
        return RedirectResponse(f"/facts/setup/{sid}", status_code=303)

    def load(sid: str) -> dict:
        run = store.get(sid)
        if run is None:
            raise HTTPException(404, "This setup has expired or the control room restarted: start again.")
        return run

    def packs(run):
        total = len(run["chunks"])
        return [{"n": c["n"], "source": run["sources"][c["source"]]["ref"], "pages": c["pages"],
                 "chars": len(c["text"]), "done": c["n"] in run["done"],
                 "pack": build_pack(c, total, run["sources"][c["source"]], run["business_type"], run["known"])}
                for c in run["chunks"]]

    async def work_page(request, session, sid, run, status=200, **ctx):
        ready, words = await model_state()
        return page(request, "onboarding.html", session, status, nav="more", step="work", sid=sid, run=run,
                    packs=packs(run), model_ready=ready, model_words=words, batch=MODEL_BATCH, **ctx)

    @app.get("/facts/setup/{sid}", response_class=HTMLResponse)
    async def setup_work(request: Request, sid: str, session=Depends(current)):
        if not s.brand_url:
            return off(request, session)
        return await work_page(request, session, sid, load(sid))

    @app.post("/facts/setup/{sid}/paste", response_class=HTMLResponse)
    async def setup_paste(request: Request, sid: str, session=Depends(csrf)):
        if not s.brand_url:
            return off(request, session)
        run = load(sid)
        form = await request.form()
        answer = str(form.get("answer", "") or "")
        n = str(form.get("chunk", ""))
        if not n.isdigit() or not 1 <= int(n) <= len(run["chunks"]):
            raise HTTPException(404, "no such part")
        if not answer.strip():
            return await work_page(request, session, sid, run, 422, paste_error=(int(n), "Paste the chatbot's answer first."))
        if len(answer) > MAX_ANSWER:
            return await work_page(request, session, sid, run, 422,
                                   paste_error=(int(n), f"An answer is at most {MAX_ANSWER:,} characters."))
        facts, questions, problems = parse_answer(answer)
        if not facts and not questions:
            return await work_page(request, session, sid, run, 422, paste_error=(int(n), (
                "No FACT or QUESTION line was found in that answer. Ask the chatbot to answer only with lines "
                "in the format shown, then paste again.")))
        add_results(run, facts, questions, f"chatbot, part {n}")
        run["problems"] += [f"Part {n}: {p}" for p in problems]
        if int(n) not in run["done"]:
            run["done"].append(int(n))
        return RedirectResponse(f"/facts/setup/{sid}/review", status_code=303)

    @app.post("/facts/setup/{sid}/model", response_class=HTMLResponse)
    async def setup_model(request: Request, sid: str, session=Depends(csrf)):
        if not s.brand_url:
            return off(request, session)
        run = load(sid)
        ready, _ = await model_state()
        if not ready:
            return await work_page(request, session, sid, run, 503,
                                   error="The model is not available: use the free chatbot way below.")
        todo = [c for c in run["chunks"] if c["n"] not in run["done"]][:MODEL_BATCH]
        for c in todo:
            try:
                out = await B().propose_facts(c["text"], run["business_type"], run["known"])
            except BackendError as e:
                run["problems"].append(f"Part {c['n']}: the model did not answer ({_why(e)})")
                continue
            add_results(run, out.get("facts") or [], out.get("questions") or [], f"model, part {c['n']}")
            run["done"].append(c["n"])
        return RedirectResponse(f"/facts/setup/{sid}/review", status_code=303)

    def review_page(request, session, sid, run, status=200, **ctx):
        return page(request, "onboarding.html", session, status, nav="more", step="review", sid=sid, run=run,
                    sensitivity=SENSITIVITY, left=[c["n"] for c in run["chunks"] if c["n"] not in run["done"]], **ctx)

    @app.get("/facts/setup/{sid}/review", response_class=HTMLResponse)
    async def setup_review(request: Request, sid: str, session=Depends(current)):
        if not s.brand_url:
            return off(request, session)
        return review_page(request, session, sid, load(sid))

    @app.post("/facts/setup/{sid}/save", response_class=HTMLResponse)
    async def setup_save(request: Request, sid: str, session=Depends(csrf)):
        if not s.brand_url:
            return off(request, session)
        run = load(sid)
        form = await request.form()
        keep = [p for p in run["proposals"] if form.get(f"keep_{p['id']}") == "yes"]
        asks = [q for q in run["questions"] if form.get(f"ask_{q['id']}") == "yes"]
        if not keep and not asks:
            return review_page(request, session, sid, run, 422, error="Tick at least one fact or question to keep.")
        try:
            existing = [f for f in (await B().facts_v2()).get("facts") or [] if isinstance(f, dict)]
        except BackendError as e:
            return review_page(request, session, sid, run, 502, error=f"The brand service (05) did not answer: {_why(e)}")
        taken = {str(f.get("key")) for f in existing if f.get("key")}
        saved, failed = 0, []
        for p in keep:
            i = p["id"]
            text = " ".join(str(form.get(f"text_{i}", "") or p["text"]).split())[:500] or p["text"]
            sens = str(form.get(f"sens_{i}", "internal"))
            sens = sens if sens in SENSITIVITY else "internal"
            vfrom, vto = _date(form.get(f"from_{i}")), _date(form.get(f"to_{i}"))
            if vfrom and vto and vfrom > vto:
                failed.append((p, "'valid to' is before 'valid from'"))
                continue
            p["text"], p["sensitivity"], p["valid_from"], p["valid_to"] = text, sens, vfrom, vto
            try:
                await B().fact_create(fact_body(p, new_key(p, taken), text, sens, vfrom, vto))
            except BackendError as e:
                failed.append((p, _why(e)))
                continue
            run["proposals"].remove(p)
            run["index"]["texts"].setdefault(norm(text), "saved just now")
            if p["value_text"]:
                run["index"]["values"].setdefault((norm(p["subject"]["ref"]), norm(p["value_text"])), "saved just now")
            saved += 1
        asked = 0
        for q in asks:
            text = q["text"] + (f' (source {q["where"]}: "{q["quote"]}")' if q["quote"] else "")
            try:
                await B().question_create({"kind": "missing_fact", "text": text[:1000]})
            except BackendError as e:
                failed.append(({"label": q["text"]}, _why(e)))
                continue
            run["questions"].remove(q)
            asked += 1
        run["saved"] = run.get("saved", 0) + saved
        if failed:
            return review_page(request, session, sid, run, 200 if saved or asked else 422,
                               error=f"Saved {saved} draft(s) and {asked} question(s). Not saved:",
                               failed=[(x.get("label") or x.get("text"), why) for x, why in failed])
        return RedirectResponse(f"/facts?show=drafts&done=onboarded:{saved}-{asked}", status_code=303)


def _source_from(r: dict, kind: str, ref: str) -> dict:
    """07's answer -> a source: PDF pages keep their numbers; a web page is one page."""
    pages = r.get("pages") if isinstance(r.get("pages"), list) else None
    if pages:
        pages = [{"page": int(p.get("page") or i + 1), "text": str(p.get("text") or "")}
                 for i, p in enumerate(pages) if isinstance(p, dict)]
    else:
        pages = [{"page": None, "text": str(r.get("text") or "")}]
    return {"kind": kind, "ref": ref[:200], "title": (str(r.get("title") or "")[:200] or None), "pages": pages}
