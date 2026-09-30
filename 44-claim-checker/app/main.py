"""Claim checker: flag statements in marketing copy that the approved facts don't support.

How it decides (each step was measured on labelled claims, see README):
- Numbers are checked in code: every number in the copy must appear in the evidence.
- Every sentence is split into its specific details by the LLM WITHOUT seeing the
  evidence (with the evidence in view it restated the facts instead of the claim).
- Each detail is looked up in the evidence by the LLM, which must quote where it is
  stated; the quote has to really exist in the evidence. In "strict" mode the detail's
  own words must also appear in that quote ("every Friday" vs a quote saying Tuesday).
- The LLM never gives a yes/no verdict on a whole claim: asked that way, qwen2.5:7b
  called 10 of 13 invented claims supported.
- Values are compared in code: a detail's number words, weekdays and months must be in the
  evidence, and a detail whose quote states a different value of the same kind (another
  number, day or month) is flagged even when the model called it stated.
- Second chance (lenient mode): a detail found "not stated" is asked once more, alone, against
  the few evidence lines closest to it ("do these lines imply it?"). It passes only with a real
  quote from those lines that contains all of its values and names, and without widening the
  scope ("every order" when the quote has no every/all/any). This cut paraphrase false alarms
  ("somewhere new" for "a different origin").
"""
import hmac
import os
import re

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://llm-gateway:8000").rstrip("/")
BRAND_URL = os.getenv("BRAND_URL", "http://brand-service:8000").rstrip("/")
KB_URL = os.getenv("KB_URL", "").rstrip("/")
VERIFIER_MODEL = os.getenv("VERIFIER_MODEL") or None
CHECK_MODE = os.getenv("CHECK_MODE", "lenient")       # lenient | strict (see README)
KB_MIN_SCORE = float(os.getenv("KB_MIN_SCORE", "0.35"))
# Knowledge-base documents written from untrusted input (RSS, Reddit, competitor pages,
# summarised by an LLM) must never count as approved facts (security audit H1).
KB_UNTRUSTED_SOURCES = {s.strip() for s in os.getenv(
    "KB_UNTRUSTED_SOURCES", "trend-digest,competitor-watch").split(",") if s.strip()}
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "300"))
# Every sentence costs LLM calls (possibly on a paid hosted verifier): cap the work per request.
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "20000"))
MAX_SENTENCES = int(os.getenv("MAX_SENTENCES", "80"))
# A caller may send its own fact lines (88 task bridge: the facts that went into the task pack).
MAX_FACTS = 200
MAX_FACT_CHARS = 500

app = FastAPI(title="claim-checker")

URL_RE = re.compile(r"https?://\S+|www\.\S+")
TAG_RE = re.compile(r"[#@]\w+")
LIST_MARK_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+", re.M)
# A comma is a thousands separator only before exactly 3 digits: "10,000" is one number,
# "every 1,2 or 4 weeks" is three (it used to be read as "12").
NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


class VerifyRequest(BaseModel):
    text: str = Field(max_length=MAX_TEXT_CHARS)
    context: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)  # brief / source text
    extra_facts: list[str] = Field(default=[], max_length=50)
    # When given, these lines ARE the approved facts ([s1], [s2] ...): 05 /facts is not read.
    facts: list[str] | None = Field(default=None, max_length=MAX_FACTS)


def require_key(x_api_key: str | None = Header(default=None)):
    """Enforced whenever INTERNAL_API_KEY is set (the stack sets it); open for local evals."""
    expected = os.environ.get("INTERNAL_API_KEY")
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(401, "missing or wrong X-API-Key")


def key_headers() -> dict:
    """Sent to the brand service (05), which wants the same key on reads."""
    key = os.environ.get("INTERNAL_API_KEY")
    return {"X-API-Key": key} if key else {}


class CheckError(Exception):
    pass


def numbers(text: str) -> set[str]:
    """Numbers a reader would take as facts: not inside links, hashtags or list markers."""
    text = LIST_MARK_RE.sub("", TAG_RE.sub("", URL_RE.sub("", text)))
    return {n.replace(",", "").rstrip(".") for n in NUM_RE.findall(text)}


def gateway(prompt: str, variables: dict) -> dict:
    body = {"prompt": prompt, "vars": variables}
    if VERIFIER_MODEL:
        body["model"] = VERIFIER_MODEL
    try:
        key = os.getenv("INTERNAL_API_KEY")
        r = httpx.post(f"{GATEWAY_URL}/v1/run", json=body, headers={"X-Caller": "44 claim checker",
                                                                      **({"X-API-Key": key} if key else {})},
                       timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise CheckError(f"gateway unreachable: {exc}") from exc
    if r.status_code != 200:
        raise CheckError(f"gateway {r.status_code} on {prompt}: {r.text[:200]}")
    return r.json()["output"]


# Slot keys written by 88 ("- [[weekday-rate]] = £45 — ..."): names, not facts. Removed so a key
# like [[rate-2026]] never counts as the number 2026 being in the facts.
SLOT_KEY_RE = re.compile(r"^\s*[-*•]\s+|\[\[[^\]\n]*\]\]\s*(?:=\s*)?")


def gather_evidence(req: VerifyRequest) -> list[str]:
    if req.facts is not None:
        # The caller's facts replace 05's; brief, extra facts and KB still add to them as before.
        lines = [f"[s{i + 1}] {t}" for i, f in enumerate(req.facts) if (t := " ".join(SLOT_KEY_RE.sub("", f).split()))]
    else:
        try:
            r = httpx.get(f"{BRAND_URL}/facts", headers=key_headers(), timeout=10)
            r.raise_for_status()
            lines = [f"[{f['id']}] {f['text']}" for f in r.json()["facts"]]
        except httpx.HTTPError as exc:
            raise CheckError(f"brand-service facts unavailable: {exc}") from exc
    lines += [f"[x{i + 1}] {f}" for i, f in enumerate(req.extra_facts)]
    if req.context:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", req.context) if s.strip()]
        lines += [f"[c{i + 1}] {s}" for i, s in enumerate(sentences)]
    if KB_URL:
        try:
            r = httpx.post(f"{KB_URL}/search", json={"query": req.text[:500], "k": 4}, timeout=30)
            hits = [h for h in r.json().get("results", []) if h.get("score", 0) >= KB_MIN_SCORE
                    and (h.get("source") or "") not in KB_UNTRUSTED_SOURCES]
            lines += [f"[k{i + 1}] {h['chunk']}" for i, h in enumerate(hits)]
        except (httpx.HTTPError, ValueError):
            pass  # the knowledge base only adds evidence; missing it makes the check stricter
    return lines


def product_subjects() -> list[tuple[str, re.Pattern]]:
    """Product names and aliases from the brand profile, each as a whole-word pattern.

    Used to keep a product's facts to that product: without it, "Our decaf has chocolate and
    hazelnut notes" passed because the Desk Blend's fact says "chocolate and hazelnut", and
    "Team Box costs $18" passed on the Desk Blend's price (caught 1 of 7 such claims on Groq).
    """
    try:
        r = httpx.get(f"{BRAND_URL}/profile", headers=key_headers(), timeout=10)
        r.raise_for_status()
        prods = r.json().get("products") or []
    except Exception:  # noqa: BLE001 - any failure only turns scoping off
        return []  # no scoping: the check falls back to all evidence, as before
    subjects = []
    for p in prods:
        names = [p.get("name") or ""] + list(p.get("aliases") or [])
        names = sorted({n.strip().lower() for n in names if n and n.strip()}, key=len, reverse=True)
        if names:
            subjects.append((names[0], re.compile(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b", re.I)))
    return subjects


def mentioned(text: str, subjects: list[tuple[str, re.Pattern]]) -> set[str]:
    return {name for name, pattern in subjects if pattern.search(text)}


def scoped_evidence(sentence: str, lines: list[str], subjects: list[tuple[str, re.Pattern]]) -> list[str]:
    """Evidence for one sentence: lines about the products it names, plus lines about none.

    A sentence naming no product keeps all evidence. A line naming several products is kept
    if it names any product of the sentence.
    """
    named = mentioned(sentence, subjects)
    if not named:
        return lines
    return [line for line in lines if not (m := mentioned(line, subjects)) or m & named]


ID_RE = re.compile(r"\[[a-z]\d+\]\s*")
WORD_RE = re.compile(r"[a-z0-9$%]+")


def in_evidence(quote: str, evidence: str) -> bool:
    """True if the quote really comes from the evidence.

    Models add the [f1] ids, join two fact lines, or trim a fact slightly, so each quoted
    line is accepted when at least 80% of its words occur in one evidence line. A quote
    the model made up ("won the 2025 Golden Bean award") shares too few words to pass.
    """
    ev_lines = [set(WORD_RE.findall(ID_RE.sub("", line.lower()))) for line in evidence.splitlines()]
    parts = [ID_RE.sub("", p).strip() for p in quote.lower().splitlines()]
    parts = [set(WORD_RE.findall(p)) for p in parts if p]
    if not parts:
        return False
    return all(any(len(p & e) >= 0.8 * len(p) for e in ev_lines) for p in parts)


LINE_ID_RE = re.compile(r"^\[([^\]\s]+)\]\s*")


def cite(line: str, quote: str | None = None) -> dict:
    """{"id", "quote"} for an evidence line: the quote given, else the line's own text."""
    m = LINE_ID_RE.match(line)
    return {"id": m.group(1) if m else "", "quote": (quote if quote is not None else ID_RE.sub("", line)).strip()[:300]}


def cite_quote(quote: str, evidence: str) -> list[dict]:
    """Map a model quote (possibly several joined lines) back to the evidence lines it came from,
    using the same 80%-of-words rule as in_evidence; each part goes to its best-matching line."""
    ev = [line for line in evidence.splitlines() if line.strip()]
    ev_words = [set(WORD_RE.findall(ID_RE.sub("", line.lower()))) for line in ev]
    out = []
    for part in (ID_RE.sub("", p).strip() for p in quote.splitlines()):
        pw = set(WORD_RE.findall(part.lower()))
        if not pw:
            continue
        best = max(range(len(ev)), key=lambda i: len(pw & ev_words[i]), default=None)
        if best is not None and len(pw & ev_words[best]) >= 0.8 * len(pw):
            out.append(cite(ev[best], part))
    return out


def cite_numbers(sentence: str, lines: list[str]) -> list[dict]:
    """The first evidence line holding each number of the sentence."""
    out = []
    for n in sorted(numbers(sentence)):
        line = next((line for line in lines if n in numbers(ID_RE.sub("", line))), None)
        if line:
            out.append(cite(line))
    return out


STOP = set("""a an the and or of to in on for with within from by at as is are be been our we us you
your it its this that per any all every each can may will has have more most very just so than into
about""".split())
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
# A detail that is a number plus units ("48 hours", "per 250 g bag", "$18") is decided by
# the exact number check; the LLM skipped or misjudged these often.
UNIT_WORDS = set("""g kg ml l oz lb lbs gram grams kilo kilos percent usd eur hour hours day days week
weeks month months year years minute minutes bag bags cup cups per a an each every within of
off discount price costs cost""".split())


def words(s: str) -> set[str]:
    out = set()
    for w in WORD_RE.findall(ID_RE.sub("", s.lower())):
        if w not in STOP:
            out.add(w[:5])  # 5-letter prefix: ships~shipping, subscription~subscribers
    return out


# Layout labels written by the format tools (57): "On screen: ...", "Subject: ...". They are not
# claims; the checker flagged them as details. A fixed list, so product names ("Team Box: ...")
# are never stripped (they decide which facts a sentence may use).
LABEL_RE = re.compile(r"^[ \t]*(?:hook|say|on screen|close|caption|subject|preview|button|cta|"
                      r"social proof|q|a|headline|subheadline)[ \t]*:[ \t]*", re.I | re.M)


def sentences(text: str) -> list[str]:
    """Sentences that can assert something: questions ("Changed your mind?") are skipped."""
    clean = LABEL_RE.sub("", TAG_RE.sub("", URL_RE.sub("", text)))
    parts = [s.strip(" -*•#") for s in SENTENCE_RE.split(clean)]
    return [s for s in parts if re.search(r"[A-Za-z]{3}", s) and not s.endswith("?")]


def is_number_detail(detail: str) -> bool:
    if not NUM_RE.search(detail):
        return False
    rest = NUM_RE.sub(" ", detail.lower())
    return all(w in UNIT_WORDS for w in re.findall(r"[a-z]+", rest))


# Values of a kind a paraphrase must never change. Number words become digits so "three blends"
# is compared with "two blends" / "2". "one" is left out: "the one you love", "one of our".
NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero _ two three four five six seven eight nine ten eleven twelve".split()) if w != "_"}
NUMBER_WORDS |= {"dozen": "12", "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50",
                 "hundred": "100", "half": "0.5", "twice": "2"}
WEEKDAYS = "monday tuesday wednesday thursday friday saturday sunday".split()
# Months only when capitalised ("May" vs "may"): details copy the claim's own capitalisation.
MONTH_RE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|"
                      r"November|December)\b")
UNIVERSAL = {"all", "any", "every", "each", "everyone", "anyone", "everything", "anything",
             "anywhere", "everywhere", "worldwide", "always", "forever", "lifetime"}


def values(text: str) -> dict[str, set[str]]:
    """The values in a text by kind: numbers (digits and number words), weekdays, months."""
    text = ID_RE.sub("", text)
    low = text.lower()
    nums = numbers(text) | {NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", low) if w in NUMBER_WORDS}
    days = {d for d in WEEKDAYS if re.search(rf"\b{d}s?\b", low)}
    months = {m.lower() for m in MONTH_RE.findall(text)}
    return {"number": nums, "day": days, "month": months}


def word_values_missing(detail: str, evidence: str) -> set[str]:
    """Number words, weekdays and months of a detail that the evidence never mentions.

    Digits are checked for the whole sentence already; these were left to the model, which
    missed "three blends" and "every Thursday" (README known gaps).
    """
    dv, ev = values(detail), values(evidence)
    digits = numbers(detail)
    return ((dv["number"] - digits) - ev["number"]) | (dv["day"] - ev["day"]) | (dv["month"] - ev["month"])


def value_conflict(detail: str, quote: str) -> list[str]:
    """Values of the detail that differ from same-kind values in its quote ("every Thursday" vs a
    quote saying "every Tuesday", "within 60 days" vs "within 30 days")."""
    dv, qv = values(detail), values(quote)
    return sorted(v for kind in dv if qv[kind] for v in dv[kind] - qv[kind])


def capital_names(detail: str) -> set[str]:
    """Capitalised words after the first word and all-caps words: names, places ("UK")."""
    toks = re.findall(r"[A-Za-z][A-Za-z'-]*", detail)
    return {t.lower() for i, t in enumerate(toks) if (i > 0 and t[0].isupper()) or (len(t) > 1 and t.isupper())}


def closest_lines(text: str, evidence: str, k: int = 3) -> list[str]:
    """The evidence lines sharing the most words (5-letter prefixes) with the text."""
    want = words(text)
    scored = [(len(want & words(line)), i, line) for i, line in enumerate(evidence.splitlines()) if line.strip()]
    scored = [x for x in scored if x[0] > 0]
    return [line for _, _, line in sorted(scored, key=lambda x: (-x[0], x[1]))[:k]]


def second_chance(detail: str, sentence: str, evidence: str) -> str | None:
    """Ask once more, narrowly, whether the closest evidence lines imply a rejected detail.

    Accepted only when the model says yes AND in code: the quote really is in those lines, the
    detail's numbers/days/months are all in the quote, its names/places are in the lines, and a
    detail with every/all/any is backed by a quote that also has one. So it can turn a paraphrase
    false alarm into a pass but cannot approve a changed value or a wider scope. Returns the
    accepted quote, or None when the detail stays unsupported.
    """
    lines = closest_lines(f"{detail} {sentence}", evidence)
    if not lines:
        return None
    block = "\n".join(lines)
    out = gateway("detail_entails", {"detail": detail, "sentence": sentence, "evidence": block})
    quote = str(out.get("quote") or "")
    if not (out.get("implied") is True and quote.strip() and in_evidence(quote, block)):
        return None
    dv, qv = values(detail), values(quote)
    if any(dv[kind] - qv[kind] for kind in dv):
        return None
    if capital_names(detail) - set(WORD_RE.findall(ID_RE.sub("", block.lower()))):
        return None
    qwords = set(re.findall(r"[a-z]+", quote.lower()))
    if set(re.findall(r"[a-z]+", detail.lower())) & UNIVERSAL and not qwords & UNIVERSAL:
        return None
    return quote


def check_one(detail: str, evidence: str) -> dict | None:
    """Ask about a single detail (used when the model skipped it in the batch)."""
    checks = gateway("detail_check", {"details": detail, "evidence": evidence}).get("checks", [])
    return checks[0] if len(checks) == 1 else None


def check_sentence(sentence: str, evidence: str) -> tuple[list[str], list[dict]]:
    """(reasons it is not supported, citations of the evidence that supported its details)."""
    details = [d for d in gateway("claim_details", {"claim": sentence}).get("details", []) if d.strip()]
    details = [d for d in details if not is_number_detail(d)]
    # A detail whose words all appear in one evidence line is supported without asking the
    # model (it rejected "roasted in small batches" although a fact says exactly that).
    ev_text = [line for line in evidence.splitlines() if line.strip()]
    ev_lines = [words(line) for line in ev_text]
    cited, rest = [], []
    for d in details:
        hit = next((i for i, line in enumerate(ev_lines) if words(d) and words(d) <= line), None)
        if hit is None:
            rest.append(d)
        else:
            cited.append(cite(ev_text[hit]))
    details = rest
    if not details:
        return [], cited
    checks = gateway("detail_check", {"details": "\n".join(details), "evidence": evidence}).get("checks", [])
    by_detail = {c.get("detail", "").strip().lower(): c for c in checks}
    reasons = []
    for detail in details:
        # Match the answer by the detail's text only: matching by position once paired
        # "caramel notes" with the answer about "medium roast". A detail without its own
        # answer is asked again alone; still unanswered counts as unsupported.
        c = by_detail.get(detail.strip().lower())
        if c is None:
            c = check_one(detail, evidence)
        if c is None:
            reasons.append(f"not in the facts: {detail}")
            continue
        quote = c.get("quote", "")
        ok = bool(c.get("stated")) and in_evidence(quote, evidence)
        if ok and CHECK_MODE == "strict":
            ok = words(detail) <= words(quote)
        # Values are decided in code, whatever the model said.
        missing = word_values_missing(detail, evidence)
        conflict = value_conflict(detail, quote) if ok else []
        if missing or conflict:
            reasons.append(f"value not in the facts: {', '.join(sorted(missing) or conflict)} ({detail})")
        elif ok:
            cited += cite_quote(quote, evidence)
        elif CHECK_MODE == "lenient" and (second := second_chance(detail, sentence, evidence)):
            cited += cite_quote(second, evidence)
        else:
            reasons.append(f"not in the facts: {detail}")
    return reasons, cited


@app.get("/health")
def health():
    return {"status": "ok", "mode": CHECK_MODE}


@app.post("/verify", dependencies=[Depends(require_key)])
def verify(req: VerifyRequest):
    if not req.text.strip():
        raise HTTPException(422, "text is empty")
    if len(sentences(req.text)) > MAX_SENTENCES:
        raise HTTPException(413, f"more than {MAX_SENTENCES} sentences; check the copy in parts")
    if any(len(f) > 1000 for f in req.extra_facts):
        raise HTTPException(422, "each extra fact must be at most 1000 characters")
    if req.facts is not None and any(len(f) > MAX_FACT_CHARS for f in req.facts):
        raise HTTPException(422, f"each fact line must be at most {MAX_FACT_CHARS} characters")
    try:
        lines = gather_evidence(req)
        evidence = "\n".join(lines)
        # Strip the [f15] ids first, or "15" would count as a number the facts contain.
        evidence_numbers = numbers(ID_RE.sub("", evidence))
        number_results = [{"value": n, "supported": n in evidence_numbers} for n in sorted(numbers(req.text))]

        subjects = product_subjects()
        results, flagged_numbers = [], set()
        for sentence in sentences(req.text):
            reasons = []
            own = scoped_evidence(sentence, lines, subjects)
            bad = sorted(numbers(sentence) - numbers(ID_RE.sub("", "\n".join(own))))
            if bad:
                flagged_numbers.update(bad)
                reasons.append("numbers not in the facts: " + ", ".join(bad))
            more, cited = check_sentence(sentence, "\n".join(own))
            reasons += more
            ev = [] if reasons else cite_numbers(sentence, own) + cited
            ev = [e for i, e in enumerate(ev) if e not in ev[:i]]
            results.append({"claim": sentence, "supported": not reasons, "reasons": reasons, "evidence": ev})
    except CheckError as exc:
        raise HTTPException(502, str(exc)) from exc

    unsupported = [r["claim"] for r in results if not r["supported"]]
    # Numbers are reported even when sentence splitting put them somewhere unexpected.
    unsupported += [f"the number {n['value']}" for n in number_results
                    if not n["supported"] and n["value"] not in flagged_numbers]
    return {
        "ok": not unsupported,
        "unsupported": unsupported,
        "claims": results,
        "numbers": number_results,
        "evidence_lines": len(lines),
    }
