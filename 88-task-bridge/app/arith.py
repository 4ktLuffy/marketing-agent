"""Arithmetic check for copy: a stated total, percentage change or saving that the text's own numbers
(or a known price fact) do not support. Exact Decimal maths. It only speaks when the text gives a
computation; a wrong one is a STRONG conflict_or_expired, a right one is a `match` showing the sum.
Never raises: a check that fails is a check that did not run."""
import re
from datetime import date
from decimal import Decimal

from . import evidence as E
from . import local as L
from . import facts as F

SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}
_NUMWORD = {**E.NUMBER_WORDS, "thirteen": 13, "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30,
            "forty": 40, "fifty": 50, "sixty": 60, "hundred": 100}
_NUMWORD = {k: v for k, v in _NUMWORD.items() if k not in ("a", "an", "single")}
_TIME_NOUNS = {"night", "day", "week", "month", "year", "hour", "minute", "min", "hr", "percent", "star", "point"}
_PERSON_NOUNS = {"guest", "person", "people", "adult", "child", "children", "pax", "head", "traveller", "traveler"}
_STOP = {"for", "at", "in", "on", "to", "from", "will", "would", "cost", "costs", "come", "comes", "is", "are",
         "total", "totals", "totalling", "totaling", "and", "with", "by", "per", "each", "x", "×", "=", "of", "that", "which"}

QTY = re.compile(r"(?<![\w+(\-./,£$€#])(?P<n>\d{1,3}(?:,\d{3})+|\d+|" + "|".join(_NUMWORD) + r")(?![\d,.]*\d)(?![\w]*[/%\-]\d)"
                 r"\s*(?:×|x|\*)?\s+(?P<rest>[A-Za-z][A-Za-z' -]*)", re.I)
NIGHTS = re.compile(r"(?<![\w.,])(?P<n>\d+|" + "|".join(_NUMWORD) + r")[\s-]+nights?\b", re.I)
TOTAL_TRIG = re.compile(r"(?:=|\bfor|\bcosts?|\btotal(?:l?ing|s)?(?:\s+(?:of|is|comes?\s+to))?|\bcomes?\s+to|\bworks?\s+out\s+(?:at|to)|"
                        r"\badds?\s+up\s+to|\bsum\s+of|\bis|\bwill\s+be|\bwould\s+be|\bcome\s+to|\bthat's|\bor)"
                        r"[:\s]*(?:(?:about|around|roughly|approximately|approx\.?|just|only|some|a\s+total\s+of|in\s+total)\s+)*$", re.I)
RATE_AFTER = re.compile(r"^\s*(?:each\b|per\b|a\s|an\s|/|apiece\b|pp\b|nightly\b|daily\b|every\b|\bp\.?p\.?\b)", re.I)
PRICE_MARK = re.compile(r"(?:\bat|\b@|\bof|\bwith)\s*$", re.I)
UP = re.compile(r"\b(?:up|rise|rose|risen|rising|increas\w*|higher|hike[sd]?|raised|jump\w*|more expensive|went up|going up)\b", re.I)
DOWN = re.compile(r"\b(?:down|drop\w*|fall\w*|fell|lower\w*|reduc\w*|cut|cuts|decreas\w*|cheaper|slash\w*)\b", re.I)
PRICE_NOUN = re.compile(r"\b(?:price|prices|rate|rates|cost|costs|fee|fees|fares?|tariffs?)\b", re.I)
SAVE_CUE = re.compile(r"(?:\bsav(?:e|ing|ings)\b[^.\d£$€]{0,12}$|\b(?:discount|reduction)\s+of\s*$)", re.I)
OFF_AFTER = re.compile(r"^\s*(?:off\b|discount\b|cheaper\b|less\b)", re.I)
EXTRAS = re.compile(r"\b(?:plus|shipping|delivery|postage|tax|vat|deposit|fees?|extra|surcharge|tip)\b|\+", re.I)
SAVE_WORDS = re.compile(r"\b(?:now|was|from|instead|usually|normally|regular(?:ly)?|rrp|reduced|only|down\s+from|before|today)\b", re.I)


def _money(v: E.Val) -> tuple[Decimal, str]:
    return Decimal(v.key[1]), v.key[2]


def _tol(v: E.Val) -> Decimal:
    """Rounding the stated number allows: a half unit, or half of its last written digit with k/m."""
    t = v.text
    m = re.search(r"(\d[\d,]*)(?:\.(\d+))?\s?(k|m|bn|thousand|million)\b", t, re.I)
    if m:
        mult = Decimal(E.MULT[m.group(3).lower()])
        dec = len(m.group(2) or "")
        return mult * Decimal("0.5") / (Decimal(10) ** dec)
    return Decimal("0.5")


def fmt(amount: Decimal, cur: str) -> str:
    q = amount.quantize(Decimal("0.01"))
    s = f"{q:,.2f}"
    if s.endswith(".00"):
        s = s[:-3]
    return f"{SYMBOL[cur]}{s}" if cur in SYMBOL else f"{s} {L.currency_names().get(cur, cur)}"


def _bare(amount: Decimal, cur: str) -> str:
    """The stated number as written ("32,000"), with a symbol for symbol currencies."""
    t = fmt(amount, cur)
    return t.rsplit(" ", 1)[0] if cur not in SYMBOL else t


def _num(s: str) -> Decimal:
    t = s.lower()
    return Decimal(_NUMWORD[t]) if t in _NUMWORD else Decimal(t.replace(",", ""))


def _stem(w: str) -> str:
    w = w.lower().strip()
    if re.search(r"(?:ss|x|z|ch|sh)es$", w):
        return w[:-2]
    return w[:-1] if w.endswith("s") and not w.endswith("ss") and len(w) > 3 else w


def _price_fact(f: dict) -> bool:
    return f.get("fact_type") == "price" and isinstance(f.get("value"), (int, float, Decimal)) \
        and not isinstance(f.get("value"), bool) and f.get("currency") and f.get("unit") not in ("%", "percent")


def _find(sentence, label, f, quote, detail):
    return {"sentence": sentence[:500], "label": label, "fact_key": f["key"] if f else None, "quote": quote,
            "blocking": label == "conflict_or_expired", "detail": detail}


def _qty_candidates(s: str, vals: list[E.Val]):
    out = []
    for m in QTY.finditer(s):
        a, b = m.start("n"), m.end("n")
        if any(a < v.end and v.start < b for v in vals if v.kind != "qty"):
            continue
        raw = m.group("n")
        if raw.isdigit() and len(raw) == 4 and 1900 <= int(raw) <= 2100:
            continue
        words = []
        for w in re.findall(r"[A-Za-z']+", m.group("rest"))[:4]:
            if w.lower() in _STOP:
                break
            words.append(w)
        if not words:
            continue
        out.append((a, _num(raw), _stem(words[-1]), words))
    return out


def _unit_fact(noun: str, sentence: str, ok: list[dict], text_all: str):
    named = [f for f in ok if _price_fact(f) and E._subject_named(f, sentence)]
    if len(named) > 1:           # several named: keep the one whose full name is written
        full = [f for f in named if F.subject_ref(f) and E.phrase_in(F.subject_ref(f), sentence)]
        named = full if len(full) == 1 else named
    if len(named) == 1:
        return named[0]
    if named:
        return None
    by_unit = [f for f in ok if _price_fact(f) and f.get("unit") and _stem(str(f["unit"]).replace("per ", "")) == noun]
    return by_unit[0] if len(by_unit) == 1 else None


def _counts(noun: str, f: dict) -> bool:
    """The counted noun is what the fact prices: its unit, its basis, or a word of its subject."""
    ok = {_stem(str(f.get("unit") or "").replace("per ", ""))}
    ok |= {_stem(str(f.get("basis") or "").replace("per_", ""))}
    ok |= {_stem(w) for w in F.words(F.subject_ref(f))}
    return noun in ok or (noun in _PERSON_NOUNS and f.get("basis") == "per_person")


PACK_OF = re.compile(r"\b(?:crate|case|box|pack|carton|tray|bundle|set|bag)s?\s+(?:of|holds|with)\s*$", re.I)


def _qty_in_fact(n: Decimal, f: dict) -> bool:
    text = " ".join(str(f.get(k) or "") for k in ("text", "value_text", "attribute"))
    return any(_num(x) == n for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text) if x.strip(","))


def _in_name_or_pack(s: str, pos: int, n: Decimal, fact: dict | None) -> bool:
    """The number belongs to a name ("Class 4 MOT", "Tempo 3 hybrid": a word right before it is part of a
    fact's subject, or the number is in the subject itself) or to a pack size ("a crate of 24 bottles")."""
    before = s[max(0, pos - 30):pos]
    if PACK_OF.search(before):
        return True
    prev = re.findall(r"[A-Za-z][\w'-]*", before)
    if prev and prev[-1][:1].isupper() and prev[-1].lower() not in {"a", "an", "the", "our", "only", "just", "for", "buy", "book", "order", "get"}:
        return True
    if fact is not None:
        ref = F.subject_ref(fact) or ""
        if any(_num(x) == n for x in re.findall(r"\d[\d,]*(?:\.\d+)?", ref) if x.strip(",")):
            return True
    return False


def _sum_findings(s: str, ok: list[dict]) -> list[dict]:
    vals = E.extract(s)
    monies = [v for v in vals if v.kind == "money"]
    if not monies:
        return []
    totals = []
    for v in monies:
        if TOTAL_TRIG.search(s[max(0, v.start - 24):v.start]) and not RATE_AFTER.match(s[v.end:v.end + 12]):
            totals.append(v)
    if not totals:
        return []
    total = totals[-1]
    tamt, tcur = _money(total)
    cands = [c for c in _qty_candidates(s, vals) if c[2] not in _TIME_NOUNS]
    cands = [c for c in cands if c[0] < total.start or "total" in s[:total.start].lower() or "=" in s]
    nm = NIGHTS.search(s)
    if not cands:
        # real-10: "Three nights in a Family Room at $248 per room per night come to $744": nights alone count
        if nm is None or not re.search(r"(?:per|a)\s+(?:room\s+per\s+)?night", s[:total.start], re.I):
            return []
        cands = [(nm.start(), Decimal(1), "room", "")]
    pos, n, noun, _w = min(cands, key=lambda c: abs(c[0] - total.start))
    nights = _num(nm.group("n")) if nm else None
    # unit price stated in the text ("at $75", "1,700 kora each", "$75 a night")
    stated = None
    for v in monies:
        if v is total:
            continue
        if PRICE_MARK.search(s[max(0, v.start - 10):v.start]) or RATE_AFTER.match(s[v.end:v.end + 12]):
            stated = v
    fact = None
    if stated is not None:
        price, cur = _money(stated)
    else:
        if len([v for v in monies if v is not total]) > 0:
            return []                    # other prices that are not unit prices: do not guess
        if noun in _PERSON_NOUNS:
            fact = _unit_fact(noun, s, [f for f in ok if f.get("basis") == "per_person"], s)
        else:
            fact = _unit_fact(noun, s, ok, s)
        if fact is None or not _counts(noun, fact):
            return []
        price, cur = Decimal(str(fact["value"])), fact["currency"]
        per_night = fact.get("unit") == "night" or fact.get("basis") == "per_night"
        if nights is not None and not per_night:
            return []                    # nights said but the price is not per night: ambiguous
    if cur != tcur:
        return []
    if fact is not None and abs(price - tamt) <= _tol(total):
        return []                        # the amount IS the fact's own price: a price statement, not a total
    if fact is not None and _qty_in_fact(n, fact):
        return []                        # the fact already prices that quantity ("1,000 cartons cost £640")
    if _in_name_or_pack(s, pos, n, fact):
        return []                        # "Class 4 MOT", "Tempo 3", "a crate of 24 bottles": not a count
    if stated is not None and noun in _PERSON_NOUNS:
        # real-10: "for two guests at $157 per room per night": the room is priced, not each guest
        rate = s[stated.end:stated.end + 30].lower()
        if re.search(r"per\s+room|a\s+room|/\s?room|per\s+night|a\s+night", rate) and \
                not re.search(r"per\s+(?:person|guest|head|adult)|\bpp\b|\beach\b", rate):
            n = Decimal(1)
    mult = n * (nights if nights is not None else 1)
    calc = price * mult
    if nights is not None:
        shown = f"{n:g} × {nights:g} nights × {fmt(price, cur)} = {fmt(calc, cur)}"
    else:
        shown = f"{n:g} × {fmt(price, cur)} = {fmt(calc, cur)}"
    quote = fact.get("value_text") if fact else None
    if n == 1 and nights is not None:
        shown = f"{nights:g} nights × {fmt(price, cur)} = {fmt(calc, cur)}"
    if abs(calc - tamt) <= _tol(total):
        return [{**_find(s, "match", fact, quote, "arithmetic: " + shown), "total_amount": str(tamt)}]
    return [_find(s, "conflict_or_expired", fact, quote, f"{shown}, not {_bare(tamt, tcur)}")]


def _pct_change(old: Decimal, new: Decimal) -> Decimal:
    return (new - old) / old * 100


def _pcts(s: str, vals: list[E.Val]):
    out = []
    for v in vals:
        if v.kind == "percent":
            out.append((v, Decimal(v.key[1])))
    return out


def _change_pairs(ok: list[dict], all_facts: list[dict], by_key: dict, day: date):
    pairs = []
    for f in ok:
        if not _price_fact(f):
            continue
        old = by_key.get(f.get("supersedes_key") or "")
        if old is None or not _price_fact(old):
            cands = []
            for g in all_facts:
                if g is f or not _price_fact(g) or g.get("currency") != f.get("currency"):
                    continue
                if F.subject_ref(g).lower() != F.subject_ref(f).lower() or not F.subject_ref(f):
                    continue
                if (g.get("attribute") or "") != (f.get("attribute") or "") or g.get("unit") != f.get("unit"):
                    continue
                end = F._day(g.get("valid_to"))
                if (g.get("status") in ("expired", "superseded")) or (end and end < day):
                    cands.append((str(g.get("valid_to") or ""), g))
            old = max(cands, key=lambda c: c[0])[1] if cands else None
        if old is not None and _price_fact(old) and Decimal(str(old["value"])) != 0 and old.get("currency") == f.get("currency"):
            pairs.append((f, old))
    return pairs


def _change_findings(s: str, ok: list[dict], all_facts: list[dict], by_key: dict, day: date) -> list[dict]:
    vals = E.extract(s)
    pcts = [(v, d) for v, d in _pcts(s, vals) if not OFF_AFTER.match(s[v.end:v.end + 10])
            and not re.search(r"(?:\bsav\w*|\bdiscount\s+of)\s*$", s[max(0, v.start - 14):v.start], re.I)]
    if not pcts or not (PRICE_NOUN.search(s) or True):
        return []
    up, down = UP.search(s), DOWN.search(s)
    if bool(up) == bool(down):
        return []
    sign = 1 if up else -1
    cue = (up or down)
    pv, stated = min(pcts, key=lambda p: abs(p[0].start - cue.start()))
    if abs(pv.start - cue.start()) > 40:
        return []
    stated *= sign
    monies = [v for v in vals if v.kind == "money"]
    old = new = None
    f = None
    if len(monies) == 2 and _money(monies[0])[1] == _money(monies[1])[1] and _money(monies[0])[0] > 0 \
            and re.search(r"\bfrom\b|\bwas\b|\bto\b|→|->|\bnow\b", s):
        (a, _c), (b, _c2) = _money(monies[0]), _money(monies[1])
        old, new = (a, b) if re.search(r"\bfrom\b|\bwas\b|^[^→>]*" + re.escape(monies[0].text) + r"\s*(?:→|->|to)", s) else (None, None)
        if old is None:
            return []
        cur = _c
    elif not monies and PRICE_NOUN.search(s):
        pairs = _change_pairs(ok, all_facts, by_key, day)
        named = [p for p in pairs if E._subject_named(p[0], s)]
        if not named and any(_price_fact(g) and E._subject_named(g, s) for g in ok):
            return []                    # the sentence is about something else that has no change on record
        pairs = named or (pairs if len(pairs) == 1 else [])
        if len(pairs) != 1:
            return []
        f, o = pairs[0]
        old, new, cur = Decimal(str(o["value"])), Decimal(str(f["value"])), f["currency"]
    else:
        return []
    actual = _pct_change(old, new)
    shown = f"{fmt(old, cur)} → {fmt(new, cur)} = {actual:+.2f}%".replace(".00%", "%")
    shown = re.sub(r"(\.\d)0%", r"\1%", shown)
    if abs(actual - stated) <= Decimal("0.5"):
        return [_find(s, "match", f, f.get("value_text") if f else None, "arithmetic: " + shown)]
    return [_find(s, "conflict_or_expired", f, f.get("value_text") if f else None,
                  f"{shown}, not {stated:+g}%")]


def _saving_findings(s: str) -> list[dict]:
    if EXTRAS.search(s):
        return []
    vals = E.extract(s)
    monies = [v for v in vals if v.kind == "money"]
    pcts = _pcts(s, vals)
    if not SAVE_WORDS.search(s) and not re.search(r"\bsav", s, re.I):
        return []
    # save/off as an amount
    amt = None
    for v in monies:
        if SAVE_CUE.search(s[max(0, v.start - 30):v.start]) or OFF_AFTER.match(s[v.end:v.end + 10]):
            amt = v
    if amt is not None:
        others = [v for v in monies if v is not amt and _money(v)[1] == _money(amt)[1]]
        if len(others) == 2:
            hi, lo = sorted(others, key=lambda v: _money(v)[0], reverse=True)
            (h, cur), (l, _c) = _money(hi), _money(lo)
            want = h - l
            x = _money(amt)[0]
            shown = f"{fmt(h, cur)} − {fmt(l, cur)} = {fmt(want, cur)}"
            if abs(want - x) <= max(_tol(amt), _tol(hi)):
                return [_find(s, "match", None, None, "arithmetic: " + shown)]
            return [_find(s, "conflict_or_expired", None, None, f"{shown}, not {fmt(x, cur)}")]
        return []
    # save/off as a percentage with both prices in the text
    for pv, p in pcts:
        near = s[max(0, pv.start - 14):pv.start]
        if not (OFF_AFTER.match(s[pv.end:pv.end + 10]) or re.search(r"\bsav\w*[^.\d]{0,12}$", near, re.I)):
            continue
        if len(monies) == 2 and _money(monies[0])[1] == _money(monies[1])[1] and SAVE_WORDS.search(s):
            hi, lo = sorted(monies, key=lambda v: _money(v)[0], reverse=True)
            (h, cur), (l, _c) = _money(hi), _money(lo)
            if h <= 0 or h == l:
                return []
            actual = (h - l) / h * 100
            shown = f"({fmt(h, cur)} − {fmt(l, cur)}) / {fmt(h, cur)} = {actual:.2f}%".replace(".00%", "%")
            if abs(actual - p) <= Decimal("0.5"):
                return [_find(s, "match", None, None, "arithmetic: " + shown)]
            return [_find(s, "conflict_or_expired", None, None, f"{shown}, not {p:g}%")]
    return []


COMPARE = re.compile(r"^\s*(?:more|dearer|higher|pricier|above|less|cheaper|lower|below)\s+(?:expensive\s+)?than\b", re.I)
COMPARE_DOWN = re.compile(r"^\s*(?:less|cheaper|lower|below)\b", re.I)


def _compare_findings(s: str) -> list[dict]:
    """real-8: "A at $111 costs 25 percent more than B at $99": the percentage between two prices in the
    same sentence (A before the percentage, B after "than"). Exact maths; 1 point of slack for rounding."""
    vals = E.extract(s)
    monies = [v for v in vals if v.kind == "money"]
    for pv, p in _pcts(s, vals):
        m = COMPARE.match(s[pv.end:pv.end + 40])
        if not m:
            continue
        before = [v for v in monies if v.end <= pv.start]
        after = [v for v in monies if v.start >= pv.end + m.end()]
        if not before or not after:
            continue
        (a, cur), (b, cur_b) = _money(before[-1]), _money(after[0])
        if cur != cur_b or b <= 0 or a == b:
            continue
        down = bool(COMPARE_DOWN.match(s[pv.end:pv.end + 40]))
        actual = (b - a) / b * 100 if down else (a - b) / b * 100
        word = "less" if down else "more"
        shown = (f"({fmt(b, cur)} − {fmt(a, cur)}) / {fmt(b, cur)}" if down else f"({fmt(a, cur)} − {fmt(b, cur)}) / {fmt(b, cur)}") + \
            f" = {actual:.1f}% {word}".replace(".0%", "%")
        if abs(actual - p) <= 1:
            return [_find(s, "match", None, None, "arithmetic: " + shown)]
        return [_find(s, "conflict_or_expired", None, None, f"{shown}, not {p:g}%")]
    return []


UNCHANGED = re.compile(r"\b(?:holds?|held|holding|stays?|stayed|remains?|remained|still|unchanged|steady|"
                       r"same\s+(?:price|as\s+before)|no\s+(?:price\s+)?(?:change|increase|rise))\b", re.I)


def _unchanged_findings(s: str, ok: list[dict], all_facts: list[dict], by_key: dict, day: date) -> list[dict]:
    """held-out trial: "Brand A 33cl holds at 2,080 kora per crate" when it rose from 1,980 on 17 August: a
    price said to be unchanged that changed (its previous fact had another value) since its own valid_from."""
    if not UNCHANGED.search(s):
        return []
    vals = [v for v in E.extract(s) if v.kind == "money"]
    for v in vals:
        cue = UNCHANGED.search(s[max(0, v.start - 30):v.start]) or UNCHANGED.search(s[v.end:v.end + 25])
        if not cue:
            continue
        amt, cur = _money(v)
        for f, old in _change_pairs(ok, all_facts, by_key, day):
            if f.get("currency") != cur or Decimal(str(f["value"])) != amt:
                continue
            ref = F.subject_ref(f)
            if ref and not E.phrase_in(ref, s):
                continue
            if Decimal(str(old["value"])) == amt:
                continue
            since = f.get("valid_from")
            return [_find(s, "conflict_or_expired", f, f.get("value_text"),
                          f"said to be unchanged, but {ref or 'the price'} changed from {_bare(Decimal(str(old['value'])), cur)}"
                          + (f" on {since}" if since else ""))]
    return []


def check(text: str, all_facts: list[dict], day: date, scope: dict) -> list[dict]:
    """Findings for every sentence that states a computation. Never raises."""
    try:
        fs = E.sort_facts(all_facts, day, scope)
        by_key = {f["key"]: f for f in all_facts}
        out = []
        for a, b in F.sentence_spans(text):
            s = text[a:b]
            for fn in (lambda: _sum_findings(s, fs.ok), lambda: _change_findings(s, fs.ok, all_facts, by_key, day),
                       lambda: _saving_findings(s), lambda: _compare_findings(s),
                       lambda: _unchanged_findings(s, fs.ok, all_facts, by_key, day)):
                try:
                    r = fn()
                except Exception:
                    r = []
                if r:
                    out += r
                    break
        return out
    except Exception:
        return []
