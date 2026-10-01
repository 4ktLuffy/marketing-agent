"""Owner-confirmed starter-kit rules applied to copy in /check (and so in every 88 submit).

Forbidden phrasings are errors. A required disclosure is enforced only when its `when` text says
when in a way we can test (a price, an offer, Amharic text, or "any … advertisement / post"); the
rest are notes for people (e.g. "hold for human review") and are never demanded in the copy."""
import re

MONEY = re.compile(r"(?:[$£€]|\b(?:ETB|USD|EUR|GBP|birr|br)\b)\s?\d|\d[\d,.]*\s?(?:birr|ETB|USD|br)\b", re.I)
OFFER = re.compile(r"\b(?:package|offer|deal|discount|promotion|promo|% off|free)\b", re.I)
ETHIOPIC = re.compile(r"[ሀ-፿]")


def applies(when: str | None, text: str) -> bool | None:
    """True/False when the rule's condition can be tested on this text; None = a note, not enforced."""
    w = (when or "").lower()
    if not w or "recommendation" in w or "owner must confirm" in w:
        return None
    if "amharic" in w:
        return bool(ETHIOPIC.search(text))
    if re.search(r"\b(?:rate|price|prices|rates)\b", w):
        return bool(MONEY.search(text))
    if re.search(r"\b(?:offer|package|promotion|deal)\b", w):
        return bool(OFFER.search(text))
    if w.startswith("any "):
        return True
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w%+ ]", " ", s.lower())).strip()


def said(disclosure: str, text: str) -> bool:
    d, t = _norm(disclosure), _norm(text)
    if d and d in t:
        return True
    # the under-21 warning in common short forms ("under 21", "21+", "No sale to under-21s")
    if re.search(r"\b21\b", d) and re.search(r"\bunder[- ]?21s?\b|\b21\s*\+|\bover 21\b", text, re.I):
        return True
    return False


BUSINESS_NOUNS = r"(?:margins?|profits?|profitability|sales|turnover|stock|cash\s*flow|business|returns?|growth|demand|orders?)"


def business_sense(text: str, word: str) -> bool:
    """A health word used of money or trade ("healthy margins", "margins stay healthy"), every time it occurs."""
    w = re.escape(word)
    uses = list(re.finditer(r"(?i)(?<![\w-])" + w + r"(?![\w-])", text))
    if not uses or word.lower() not in ("healthy", "health", "healthier", "strong", "energy", "boost"):
        return False
    near = re.compile(r"(?i)" + BUSINESS_NOUNS + r"(?:\s+\w+){0,3}\s+" + w + r"\b|\b" + w + r"\s+(?:\w+\s+){0,1}" + BUSINESS_NOUNS)
    return all(near.search(text[max(0, m.start() - 40):m.end() + 40]) for m in uses)


def check(rules: list[dict], text: str, find_token) -> list[dict]:
    out = []
    for r in rules:
        if r.get("status") != "active":
            continue
        if r["kind"] == "forbidden_phrase":
            found = find_token(text, r["phrase"])
            if found and business_sense(text, found):
                found = None             # "keeps margins healthy" is about the shop's margins, not a health claim
            if found:
                out.append({"rule": "kit_forbidden", "severity": "error", "match": found,
                            "detail": f"'{found}' is not allowed ({r.get('kit')} rule: {(r.get('why') or '')[:140]})"})
        elif r["kind"] == "required_disclosure":
            if applies(r.get("why"), text) and not said(r["phrase"], text):
                out.append({"rule": "kit_disclosure", "severity": "error",
                            "detail": f"add \"{r['phrase']}\" ({r.get('kit')} rule, when: {(r.get('why') or '')[:100]})"})
    return out
