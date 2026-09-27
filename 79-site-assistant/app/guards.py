"""Deterministic rules of the site assistant: what a visitor's message is, and which sentences
of a model answer may never reach a visitor.

No LLM here. Every rule is a regular expression or a set lookup, so it behaves the same on
every run and can be tested. The model is asked to follow the same rules (prompt
`site_answer`); these are the ones that hold when it does not.
"""
import re

# ---------- visitor message: routing (checked in this order, before any LLM call)

# Attempts to change, reveal or replace the assistant's rules. A refusal costs no LLM call.
INJECTION_RE = re.compile(
    r"\b(ignore|disregard|forget|override|bypass|skip)\b[^.?!\n]{0,40}\b(rules?|instructions?|prompts?|"
    r"guidelines|guardrails|restrictions|above|previous|system)\b"
    r"|\b(system|developer|hidden|initial|original)\s+(prompt|message|instructions?)\b"
    r"|\byou\s+are\s+now\b|\bfrom\s+now\s+on,?\s+(you|act|answer|respond|reply)\b"
    r"|\b(act|behave)\s+as\s+(if|a|an|my|the)\b|\bpretend\s+(to\s+be|you|that)\b|\brole-?play\b"
    r"|\bjailbreak|\bdeveloper\s+mode\b|\bDAN\b"
    r"|\b(new|change|update|replace)\s+(your\s+)?(rules?|instructions?|policy|policies)\b|\blegally\s+binding\b"
    r"|\b(reveal|show|print|repeat|tell\s+me)\s+(me\s+)?(your|the)\s+(rules|prompt|instructions|system)\b"
    r"|\bwhat\s+(are|were)\s+your\s+(instructions|rules)\b"
    r"|<\|?\s*(system|im_start|assistant)|\[/?(INST|SYS)\]",
    re.I,
)
# "Are you human?" is answered in code: the answer must always be "no".
HUMAN_QUESTION_RE = re.compile(
    r"\b(are|r)\s+(you|u)\s+(a\s+|an\s+)?(real|actual|live)?\s*(human|person|bot|robot|ai|machine|chatbot|"
    r"computer|real)\b"
    r"|\b(am\s+i|are\s+we)\s+(talking|chatting|speaking|writing)\s+(to|with)\s+(a\s+|an\s+)?(real\s+|actual\s+)?"
    r"(human|person|bot|robot|ai|machine|computer|chatbot)\b"
    r"|\bis\s+this\s+(a\s+|an\s+)?(bot|robot|ai|chatbot|real\s+person|human|person|machine)\b"
    r"|\bwho\s+am\s+i\s+(talking|chatting|speaking)\s+(to|with)\b",
    re.I,
)
PERSON_REQUEST_RE = re.compile(
    r"\b(talk|speak|chat|write)\s+(to|with)\s+(a\s+|an\s+|the\s+|your\s+|some\s*one|some\s*body)?\s*"
    r"(real\s+|actual\s+|live\s+)?(human|person|someone|somebody|agent|representative|rep|staff|team|"
    r"manager|owner|people|support)\b"
    r"|\b(connect|transfer|put)\s+me\s+(to|through|with)\b"
    r"|\b(get|want|need)\s+(me\s+)?(a\s+|an\s+)?(real\s+|actual\s+|live\s+)?(human|person|agent|representative)\b"
    r"|^\s*(human|real person|live agent|agent|operator|customer (service|support))\s*(please|pls)?\s*[.!?]*\s*$"
    r"|\b(call|contact|email)\s+me\b",
    re.I,
)
# Asking for a deal is answered in code (the $1 Tahoe): the assistant never negotiates.
DISCOUNT_ASK_RE = re.compile(
    r"\b(discount|coupon|promo(tion(al)?)?\s*code|promo|voucher|deal|cheaper|lower\s+(the\s+)?price|"
    r"price\s+match|free\s+(bag|box|month|coffee|sample|trial)|\d+\s*%\s*off|percent\s+off|"
    r"special\s+(price|offer)|haggle|negotiat)",
    re.I,
)
# Topics a person must handle (the idea of 58's ESCALATE_WORDS, for chat). Whole words.
ESCALATION = {
    "complaint": re.compile(
        r"\b(complain|complaint|broken|damaged|leak(ed|ing)?|never\s+(arrived|came|got)|"
        r"(didn't|did\s+not|hasn't|has\s+not|haven't|still\s+not)\s+(arrive|arrived|come|came|received|get|got)|"
        r"missing|wrong\s+(order|item|bag|box|coffee)|charged\s+twice|double[-\s]charged|overcharged|"
        r"unacceptable|terrible|awful|furious|angry|worst|disgusting|mou?ldy?)\b", re.I),
    "refund": re.compile(
        r"\b(i\s+(want|need|demand|expect|would\s+like|'d\s+like)|give\s+me|send\s+me|where\s+is|"
        r"i('m|\s+am)\s+(still\s+)?waiting\s+(for|on))\b[^.?!\n]{0,40}\b(refund|money\s+back|reimburse)"
        r"|\brefund\s+me\b|\bmy\s+(money|refund)\b|\bmoney\s+back\b|\bchargeback\b", re.I),
    "account": re.compile(
        r"\b(cancel|close|delete)\s+my\b|\bchange\s+my\s+(address|card|payment|order|email)\b|"
        r"\bwhere\s+is\s+my\s+(order|box|delivery|parcel|package)\b|\bmy\s+(password|invoice)\b", re.I),
    "legal": re.compile(
        r"\b(lawyer|attorney|solicitor|lawsuit|sue|suing|legal\s+action|court|small\s+claims|"
        r"trading\s+standards|consumer\s+protection|ombudsman|fraud|scam|police|gdpr|"
        r"delete\s+my\s+data|data\s+protection|report\s+you)\b", re.I),
    "health": re.compile(
        r"\b(pregnan\w*|breast-?feed\w*|nursing|allerg\w*|sick|ill|illness|nause\w*|vomit\w*|doctor|"
        r"hospital|medical|medication|medicine|health\w*|heart|blood\s+pressure|diabet\w*|palpitations?|"
        r"headaches?|migraines?|anxiety|insomnia|caffeine\s+sensitiv\w*|safe\s+(for|during|to\s+drink|while))\b",
        re.I),
}
# Buying intent in code (the LLM's buying_intent flag is OR-ed with this).
BUY_RE = re.compile(
    r"\b(price|prices|pricing|cost|costs|how\s+much|plans?|quote|invoice|bulk|wholesale|buy|purchase|"
    r"subscribe|sign\s+up|order\s+for|team|teams|office|company|employees|colleagues|co-?workers|staff|"
    r"seats|trial|demo|sample|samples|sales)\b"
    r"|\b\d{1,5}\s*(people|persons|employees|staff|teammates|colleagues|co-?workers|seats|users|of\s+us)\b",
    re.I,
)
TEAM_SIZE_RE = re.compile(
    r"\b(\d{1,5})\s*(-?\s*person\s+team|people|persons|employees|staff|teammates|colleagues|co-?workers|"
    r"seats|users|of\s+us)\b|\bteam\s+of\s+(\d{1,5})\b",
    re.I,
)
QUESTION_WORD_RE = re.compile(
    r"\?|^\s*(what|how|when|where|why|who|which|can|could|do|does|is|are|will|would|should)\b", re.I)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")


def is_injection(text: str) -> bool:
    return bool(INJECTION_RE.search(text))


def asks_if_human(text: str) -> bool:
    return bool(HUMAN_QUESTION_RE.search(text))


def asks_for_person(text: str) -> bool:
    return bool(PERSON_REQUEST_RE.search(text))


def asks_for_discount(text: str) -> bool:
    return bool(DISCOUNT_ASK_RE.search(text))


def escalation_topics(text: str) -> list[str]:
    return [name for name, pattern in ESCALATION.items() if pattern.search(text)]


def buying_intent(text: str) -> bool:
    return bool(BUY_RE.search(text))


def team_size(text: str) -> str | None:
    m = TEAM_SIZE_RE.search(text)
    if not m:
        return None
    return m.group(1) or m.group(3)


def is_question(text: str) -> bool:
    return bool(QUESTION_WORD_RE.search(text))


def redact(text: str) -> str:
    """Stored transcripts never keep an email or phone number typed into the chat: an address
    is only kept through the consent form."""
    return PHONE_RE.sub("[phone removed]", EMAIL_RE.sub("[email removed]", text))


# ---------- model answer: sentences that never reach a visitor

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")  # the claim checker (44) splits the same way
NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
HUMAN_CLAIM_RE = re.compile(
    r"\b(i\s+am|i'm|im)\s+(a\s+|an\s+)?(real\s+|actual\s+)?(human|person|member\s+of\s+(the|our)\s+team|"
    r"employee|barista)\b|\b(i'm|i\s+am)\s+not\s+(a\s+|an\s+)?(ai|bot|robot|machine|chatbot|computer)\b",
    re.I,
)
# The assistant acting or committing on the company's behalf: never allowed, whatever the sources say.
PROMISE_RE = re.compile(
    r"\b(i('ll|\s+will|\s+can|\s+could|'ve|\s+have|\s+am\s+going\s+to)|we('ll|\s+will|'ve|\s+have|"
    r"\s+are\s+going\s+to))\s+(\w+\s+)?(give|offer|refund(ed)?|send|credit(ed)?|waive(d)?|appl(y|ied)|"
    r"upgrade(d)?|reserve(d)?|book(ed)?|schedule(d)?|cancel(l?ed)?|process(ed)?|replace(d)?|"
    r"compensate(d)?|guarantee(d)?|promise(d)?|discount(ed)?|match(ed)?|honou?r(ed)?|arrange(d)?|"
    r"make\s+sure|ensure)\b"
    r"|\b(i|we)\s+promise\b|\bguarantee(d|s)?\b",
    re.I,
)
# Offer words: allowed only when the sources use the same word ("Free shipping on subscriptions").
OFFER_WORDS = ("discount", "coupon", "promo", "voucher", "% off", "percent off", "special offer", "deal",
               "on sale", "sale price", "trial", "sample", "complimentary", "on the house", "price match",
               "cheaper", "lowest price", "free", "bonus", "gift", "credit")
# Health, medical and legal words: allowed only when the sources use the same word.
SENSITIVE_WORDS = ("pregnan", "breastfeed", "health", "medical", "medicine", "medication", "doctor",
                   "disease", "cure", "safe for", "safe during", "safe to", "allerg", "blood pressure",
                   "heart", "anxiety", "insomnia", "immune", "diet", "nutrition", "calorie", "weight loss",
                   "legal", "lawful", "liable", "liability", "lawyer", "warranty", "certified", "organic")
DATE_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|today|tonight|tomorrow|yesterday|next\s+(week|month|year|day)|this\s+(week|weekend|"
    r"month)|overnight|same[-\s]day|next[-\s]day|asap|right\s+away|immediately)\b",
    re.I,
)
PRICE_RE = re.compile(r"[$€£¥]\s*\d|\d\s*(usd|eur|gbp|dollars?|euros?|pounds?|cents?)\b", re.I)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_RE.split(text or "") if s.strip()]


def numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM_RE.findall(text)}


def blocked_reason(sentence: str, evidence: str) -> str | None:
    """Why this sentence may not be shown, or None. `evidence` = the exact sources the answer
    was written from (approved facts + knowledge-base excerpts)."""
    low, ev = sentence.lower(), evidence.lower()
    if HUMAN_CLAIM_RE.search(sentence):
        return "claims to be human"
    if PROMISE_RE.search(sentence):
        return "promise or action on the company's behalf"
    extra = sorted(numbers(sentence) - numbers(evidence))
    if extra:
        kind = "price" if PRICE_RE.search(sentence) else "number"
        return f"{kind} not in the sources: {', '.join(extra)}"
    for m in DATE_RE.finditer(sentence):
        if m.group(0).lower() not in ev:
            return f"date or time not in the sources: {m.group(0)}"
    for w in OFFER_WORDS:
        if re.search(r"(?<![a-z])" + re.escape(w), low) and w not in ev:
            return f"offer word not in the sources: {w}"
    for w in SENSITIVE_WORDS:
        if re.search(r"(?<![a-z])" + re.escape(w), low) and w not in ev:
            return f"health/legal word not in the sources: {w}"
    return None


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9$%./ ]+", " ", text.lower()).split()).strip(" .")
