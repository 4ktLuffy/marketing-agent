"""Is a customer phrase woven into a post, or pasted on? Did the post invent a customer? (no LLM)

Writers (26/48/65, and 23 voc_ab) pick one customer phrase in code and ask the model to put it
in the first sentence (`open_with`). A small model meets "the first sentence contains it" by
pasting the phrase in front as a label: "box arrived late We're sorry...", "pause my
subscription | When you pause...". These checks catch that, plus invented customer identities
("our customer Sarah", "a team at Acme"), so the writer can retry once with feedback.

Decision on quotation marks: the phrase must NOT be quoted. 70 phrases are verbatim customer
text but not consented testimonials, and 58 `/testimonials/check` flags every quoted span that
is not a consented testimonial; a quote also tells the reader "a customer said this", which
invites an invented speaker. So the phrase is reused as the brand's own words, unquoted.
"""
import re

QUOTE_CHARS = "\"“”„«»"
# Sentence end: . ! ? … (optionally followed by closing quotes/brackets) then whitespace, or a line break.
SENT_END_RE = re.compile(r"[.!?…][\"”’')\]]*\s+|\n+")
# A separator that makes the phrase a label rather than part of a sentence.
SEP_AFTER_RE = re.compile(r"^[ \t]*(?:[|:•–—]|-\s|/\s|\n)")
SEP_BEFORE_RE = re.compile(r"(?:[|:•–—]|\s-|\s/)[ \t]*$")
# Emoji, pictographs and symbols a post may start with.
DECOR_RE = re.compile(r"[\s←-⯿☀-➿\U0001F000-\U0001FAFF️‍#*]+")
# Closed-class words that make a phrase a clause of its own ("the decaf doesn't taste like
# decaf"), so a sentence that is only the phrase is still a sentence. General English, not
# chosen from any eval phrase.
CLAUSE_WORDS = frozenset("""
is are was were be been am do does did has have had will would can could should may might must
isn't aren't wasn't weren't don't doesn't didn't hasn't haven't hadn't won't wouldn't can't
couldn't shouldn't i'm it's that's there's you're we're they're i've we've you've
""".split())

REASONS = {
    "separator_after": "a separator (| : – — or a line break) follows it",
    "separator_before": "a separator (| : – —) comes right before it",
    "lowercase_start": "the post or sentence starts with it in lowercase",
    "no_join": "it starts the sentence and the next word starts a new sentence with no punctuation",
    "fragment": "it stands alone as a sentence without a verb of its own",
    "dangling_opener": "it hangs in front of the sentence, joined by a comma to a clause about something else",
    "capitalized_mid_sentence": "it is capitalized in the middle of a sentence",
}


def _norm(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'")


def find_phrase(text: str, phrase: str, start: int = 0) -> tuple[int, int] | None:
    """(start, end) of the first word-for-word occurrence (case, whitespace, apostrophe style may differ)."""
    words = _norm(phrase).split()
    if not words:
        return None
    body = r"\s+".join(re.escape(w).replace("'", "['’‘]") for w in words)
    m = re.compile(r"(?<![A-Za-z0-9'’])" + body + r"(?![A-Za-z0-9'’])", re.I).search(text, start)
    return (m.start(), m.end()) if m else None


def first_sentence(text: str) -> str:
    """The text up to the first sentence end (. ! ? … followed by a space) or line break."""
    text = text.strip()
    for part in re.split(r"(?<=[.!?…])\s+|\n+", text):
        if part.strip():
            return part.strip()
    return ""


def opens_with(text: str, phrase: str) -> bool:
    return find_phrase(first_sentence(text), phrase) is not None


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    s = 0
    for m in SENT_END_RE.finditer(text, 0, start):
        s = m.end()
    m = SENT_END_RE.search(text, end)
    return s, (m.start() if m else len(text))


def bolted_on(text: str, phrase: str) -> list[str]:
    """Why the phrase reads as a pasted label, or [] when it sits inside a sentence (or is absent).
    Looks at the occurrence in the first sentence, else the first occurrence in the post."""
    fs = first_sentence(text)
    hit = find_phrase(fs, phrase) if fs else None
    at = (text.find(fs) + hit[0], text.find(fs) + hit[1]) if hit else find_phrase(text, phrase)
    if not at:
        return []
    start, end = at
    occ = text[start:end]
    s, e = _sentence_bounds(text, start, end)
    before, after = text[s:start], text[end:e]
    at_sentence_start = DECOR_RE.sub("", before).strip(" \t\"“”'‘’([") == ""
    reasons = []
    if SEP_AFTER_RE.match(text[end:]):
        reasons.append("separator_after")
    if SEP_BEFORE_RE.search(before):
        reasons.append("separator_before")
    first_letter = next((c for c in occ if c.isalpha()), "")
    phrase_letter = next((c for c in phrase if c.isalpha()), "")
    if at_sentence_start:
        if first_letter.islower():
            reasons.append("lowercase_start")
        rest = after
        if rest and re.match(r"[ \t]+(?:[A-Z]|[^\w\s,.!?;:'’\"”)\]-])", rest):
            reasons.append("no_join")
        is_clause = bool(set(_norm(phrase).lower().split()) & CLAUSE_WORDS)
        if not re.findall(r"[A-Za-z0-9]", rest) and not is_clause:
            reasons.append("fragment")
        # "<verbless phrase>, our X is ..." : the phrase dangles before a new subject (a comma
        # splice / dangling modifier). A join word after the comma ("..., and it's") is fine.
        if not is_clause and re.match(r",\s*(?:our|we|we're|we've|it|it's|this|that|these|the|your|you|you're|they|i|i'm|"
                                      r"here's|there's)\b", rest, re.I):
            reasons.append("dangling_opener")
    elif first_letter.isupper() and phrase_letter.islower() and not before.rstrip().endswith(tuple(QUOTE_CHARS)):
        reasons.append("capitalized_mid_sentence")
    return list(dict.fromkeys(reasons))


def quoted(text: str, phrase: str) -> bool:
    """The phrase is put in quotation marks (presented as a quote)."""
    at = find_phrase(text, phrase)
    while at:
        before, after = text[:at[0]].rstrip(), text[at[1]:].lstrip(".,!?…")
        if before.endswith(tuple(QUOTE_CHARS)) or after.startswith(tuple(QUOTE_CHARS)):
            return True
        at = find_phrase(text, phrase, at[1])
    return False


# ---------- invented customers

PERSON_NOUNS = r"(?:customer|subscriber|client|member|reader|fan|user|buyer|shopper|regular|barista|teammate|colleague|coworker|co-worker|friend|neighbou?r|mom|dad|wife|husband)"
GROUP_NOUNS = r"(?:team|office|company|startup|agency|firm|crew|folks|staff|people|studio|colleagues|friends|family)"
SAY_VERBS = r"(?:told|tells|said|says|wrote|writes|asked|asks|shared|shares|messaged|emailed|mentioned|swears|swore|reviewed|raved|loves|loved)"
NAME = r"([A-Z][a-z]+(?:\s+[A-Z][a-z]*\.?)?)"
ORG = r"([A-Z][\w&'’.-]*(?:\s+(?:&\s+)?[A-Z][\w&'’.-]*)*)"
NOT_NAMES = frozenset("""
I I'm I've I'd I'll The A An Our We You Your Their They This That These Those It Its Here There
What When Where Why How Who Every Each Some Any No Not And But Or So If Just Now Then Today
Monday Tuesday Wednesday Thursday Friday Saturday Sunday
""".split())

INVENTED_PATTERNS = [
    # "our customer Sarah", "subscriber Tom K."
    ("named_customer", re.compile(r"\b" + PERSON_NOUNS + r"s?,?\s+(?:named\s+|called\s+)?" + NAME)),
    # "Sarah, a longtime subscriber", "Tom from our Team Box crew"
    ("named_customer", re.compile(NAME + r",\s+(?:a|an|one of our|our)\s+(?:[\w-]+\s+){0,2}" + PERSON_NOUNS + r"s?\b")),
    # "Maria in Denver says"
    ("named_customer", re.compile(NAME + r"\s+(?:from|in|at)\s+" + ORG + r"\s+" + SAY_VERBS + r"\b")),
    # "a team at Acme Tech", "the folks from Brightpath"
    ("named_organization", re.compile(r"\b" + GROUP_NOUNS + r"\s+(?:at|from)\s+" + ORG)),
    # "a subscriber told us", "one of our customers wrote" (a story nobody can check)
    ("anonymous_testimonial", re.compile(
        r"\b(?:(?:one of our|some of our)\s+(?:[\w-]+\s+){0,2}" + PERSON_NOUNS + r"s|(?:a|an|another|our|this|one)\s+"
        r"(?:[\w-]+\s+){0,2}" + PERSON_NOUNS + r")\s+(?:just\s+|recently\s+|once\s+)?" + SAY_VERBS + r"\b", re.I)),
]


def invented_customers(text: str, allowed_text: str = "") -> list[dict]:
    """Customer identities or stories the post makes up. A name or organization that appears in
    `allowed_text` (the real sources, brand profile, facts, topic) is not flagged."""
    allowed = set(re.findall(r"[A-Za-z][\w'’&.-]*", allowed_text))
    out = []
    for kind, rx in INVENTED_PATTERNS:
        for m in rx.finditer(text):
            if kind == "anonymous_testimonial":
                out.append({"kind": kind, "text": m.group(0)})
                continue
            names = [g for g in m.groups() if g]
            words = [w for g in names for w in re.findall(r"[A-Za-z][\w'’&.-]*", g)]
            real = [w for w in words if w not in NOT_NAMES and w.rstrip(".") not in allowed]
            if real:
                out.append({"kind": kind, "text": m.group(0)})
    seen, uniq = set(), []
    for o in out:
        if o["text"] not in seen:
            seen.add(o["text"])
            uniq.append(o)
    return uniq


# ---------- one call for writers


def feedback(phrase: str | None, result: dict) -> str:
    """One retry message covering every problem, or "" when the post is fine."""
    parts = []
    fs = result.get("first_sentence", "")
    if phrase and not result["opens_with"]:
        parts.append(f'Its first sentence was "{fs}". Write the post again so that its first sentence contains '
                     f'"{phrase}" word for word, inside a full sentence of your own.')
    elif phrase and result["bolted_on"]:
        why = "; ".join(REASONS[r] for r in result["bolted_reasons"])
        parts.append(f'It pasted "{phrase}" on as a label ({why}): "{fs}". Write it again so the phrase sits inside '
                     f'one grammatical sentence, with your own words before or after it, no | : – — or line break '
                     f'next to it, and the normal capitalization of a sentence.')
    if phrase and result.get("quoted"):
        parts.append(f'Do not put "{phrase}" in quotation marks; use it as your own words.')
    if result["invented_customers"]:
        found = ", ".join(f'"{i["text"]}"' for i in result["invented_customers"])
        parts.append(f"It made up a customer or a customer's story ({found}). Do not name or describe any "
                     f"customer, person, team or company, and do not say what a customer told you.")
    return ("Your previous answer broke a rule. " + " ".join(parts)) if parts else ""


def capitalize_start(text: str) -> str:
    """Fix in code what code can fix: a post that starts in lowercase (the usual trace of a pasted
    phrase, 12 of the 14 bolted-on v1/v2 posts) gets a capital first letter."""
    m = re.match(DECOR_RE.pattern + r"|", text)
    i = m.end() if m else 0
    return text[:i] + text[i].upper() + text[i + 1:] if i < len(text) and text[i].islower() else text


def check_post(text: str, phrase: str | None, allowed_text: str = "") -> dict:
    """All checks on the post after `capitalize_start`; `text` is that repaired post (use it)."""
    phrase = (phrase or "").strip() or None
    fixed = capitalize_start(text)
    res = {"text": fixed, "repaired": fixed != text,
           "raw_bolted_reasons": bolted_on(text, phrase) if phrase and opens_with(text, phrase) else []}
    text = fixed
    res |= {"first_sentence": first_sentence(text), "opens_with": None, "bolted_on": None, "bolted_reasons": [],
           "quoted": None, "invented_customers": invented_customers(text, allowed_text)}
    if phrase:
        res["opens_with"] = opens_with(text, phrase)
        res["bolted_reasons"] = bolted_on(text, phrase) if res["opens_with"] else []
        res["bolted_on"] = bool(res["bolted_reasons"])
        res["quoted"] = quoted(text, phrase)
    res["ok"] = (not phrase or (res["opens_with"] and not res["bolted_on"] and not res["quoted"])) \
        and not res["invented_customers"]
    res["feedback"] = feedback(phrase, res)
    return res
