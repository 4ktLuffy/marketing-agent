"""Evidence labels for a piece of copy, deterministic (no model).

Per sentence it pulls out money, percentages, quantities with a unit, dates and times, and claim
keywords, and compares them with the facts as they stand on the publish date for the task scope:

  match               the value is an in-scope valid fact's value (quoted)
  wrong_scope         the value is a fact's that is valid, but only for another site/plan/variant...
  conflict_or_expired the value is an expired / superseded / not-yet-valid fact's, or it differs
                      from an in-scope fact about the same subject
  no_source           a claim keyword (certified, guarantee, UL, "made in", ...) or a price or
                      percentage that no fact supports
  forbidden_phrase    a fact's forbidden phrasing, with no allowed-phrasing exemption for the
                      subject the sentence names
  missing_disclosure  a fact is used (slot, value or keyword) but none of its required
                      disclosures is in the piece
  review              worth a look, nothing checkable (never blocks)

Keeping false warnings low: a value is a match if it appears ANYWHERE in an in-scope fact (its
value, value text or sentence), but it is flagged only against a stale or out-of-scope fact's own
value. Bare numbers are ignored. Quantities no fact mentions are "review", not blocking. A
keyword is supported by any in-scope fact that says it (or is of that claim class and shares a
word with the sentence). Disclosures compare word sets, so "2 sharing" = "two sharing".

General rules (any business):
- claim identity: a certification / approval / partner claim needs a fact naming the SAME
  identifier (ISO 9001 vs ISO 14001, SOC 2 Type I vs II, NHS vs HCPC, UL Listed vs Recognized);
- ratings, awards, superlatives, rankings, customer counts need a result / claim / testimonial
  (or comparative / result class) fact saying the same number and thing; "our most popular X"
  (own range) is only "review"; "best way to", "best before", "number one priority" are not claims;
- non-numeric facts (inclusions, features, allergens, offers) are tied to a sentence by their
  subject + a distinctive word, a distinctive word pair of their value, or their attribute name,
  never by a shared number or a word that valid in-scope facts also use;
- codes (PN40, IP67, M12, 316L, 2 x 1.5 kW) are values: another number in the same family conflicts;
- a missing disclosure is reported on every sentence that uses the fact.

Round 6 (all STRONG, each with its own evidence):
- an expired offer named in its own words ("our autumn mouse deal" for "Autumn mouse-proofing
  offer"): two distinctive name words together, one no valid fact uses, then an offer word;
- the saving an offer's own text states ("save £20", "£90 instead of £110") said with a saving
  word is that offer's value, not a conflict with the regular price;
- how often ("eight return sailings a day", "twice a week") is a value like any other;
- identifiers with dashes or a word before "number" ("company ID 518-204-7731", "member workshop
  number 2291") compare digit for digit with the facts';
- a rating word on a scheme the facts name ("rated Excellent by the CQC"), another site's rating
  word said with this site's name, a grade in words ("Technical Three"), another tier of a named
  award ("Platinum" vs "Gold");
- "our in-house / on-site <professional>" needs a fact naming that role; "<Place>'s number one"
  needs a fact that says that ranking, not merely an award.

Round 8:
- disclosures are compared after a curated equivalence table (both sides): "£95pp" = "per person",
  "VAT extra" = "plus VAT", "once we've surveyed" = "after a survey", "T&Cs apply" = "terms apply",
  "min. 12 months" = "12-month minimum term"; opposites stay apart (see _DISC_EQUIV);
- a short question or speaker / caption line ("VO: Two A-boards?", <= 6 words) never blocks unless
  it has a slot, an identifier, an exact value or a fact's full name (its findings become review);
- idioms are not superlatives ("best-kept secret", "best-laid plans", "make the best of");
- STRONG: a quantity + its noun that only a stale / out-of-scope fact's sentence says ("55-minute
  treatment"); identifiers written in groups or with other dashes ("104·97·38", "G/77-92",
  "MPS–FR–2219", "establishment ID 731-04-24"); star numbers in words against a rating fact
  ("five-star hotel" vs "Four Star Hotel"); 24/7 against a support-hours fact with set times; "N + 1"
  deals said without "buy" ("your third month is on us", "three months for the price of two"); an
  expired offer / package named by part of its name ("Twilight spa", "sausage deal"; never a season
  word); a person's membership grade of a body ("a Fellow of the AAT" vs "full member"); a tier after
  a body's acronym ("NFRC Platinum"); "Class A" for a Euroclass fact; what only another plan's
  sentence includes ("includes payroll") said as included here;
- not blocking: 24/7, same-day / next-day and "N years of experience" with no fact, "our in-house
  <x> team" no fact names (no_source).

Round 10 (precision):
- "approved by" a time, day, date or deadline ("approved by 12 noon") is not an approving body;
- "free" followed by "right now" / "at the moment" / "currently" means available, not a price of nothing;
- disclosures in other words: "bought separately" / "an extra purchase" / "paid for on top" = not
  included; "upfront" / "prepaid" = paid in advance; a minimum term said as the customer's commitment
  ("stay at least 12 weeks", "12 weeks or longer", "sign up for a year at a time", "one-year sign-up";
  a year = 12 months); times of day ("12 noon" = "midday" = "12pm" = "12:00") and a deadline ("by" /
  "before"), where one other word of the deadline may be named otherwise ("sign off your proof");
- STRONG: a per-person price said as the price for a group ("£240 for you and a friend") conflicts;
  "tomorrow" = "the next day" for a valid fact's own value words (so its disclosure applies).

Round 11:
- STRONG: a price said on another basis than the fact's `basis`: a flat / per-unit / per-room price
  said per head ("£180 per child", "£95pp"), a per-seat / per-person / per-unit price said for a whole
  group ("£7 a month for your whole team", "£9 per company"); never when the fact names that basis;
- STRONG: another number in the same frame as a valid fact's own value, same unit ("up to 100 miles
  per charge" vs "up to 70 miles per charge"); a period said as never ending ("lifetime warranty",
  "cover for life") against a valid fact giving the same thing a set length;
- disclosures in other words: numbers past twelve in words, "as many as" / "no more than" = "up to",
  kids = children, each / every = per, "which we can't refund" = "non-refundable", "VAT added on top"
  = "plus VAT", "at least two hours long" = "minimum 2 hours", "in eco" = "in eco mode";
- a certification claim is backed by a valid fact whose own value names that very mark ("EU Ecolabel
  certified" for "EU Ecolabel cleaning products"), never by the business's name;
- WEAK: a past-tense sentence with no we / our / you and no fact's name ("when every office was full
  five days a week") sets the scene; a differing number in it is review only.

Round 12:
- amounts and percentages in words are read once, in extract(), into the same values as "£36" / "96%"
  ("twelve quid", "thirty-six pounds", "a hundred quid", "one thousand four hundred and fifty pounds",
  "ninety-six per cent", "half a percent"); the whole number is read, never its last word; "pounds of"
  and "a hundred per cent sure" are not values; a closing hour with no am / pm in a sentence about
  opening hours ("open till eight") is an evening time;
- a round ceiling ("under a hundred quid") stands for a fact's price just under it (within 10%, same
  currency); STRONG only when the sentence is about that fact, else review;
- free said otherwise ("on the house", "for nothing", "won't cost you a penny (more)", "thrown in") is
  a benefit; a valid benefit said by its own object ("thirty free boxes") uses its fact (disclosures
  apply); a stale / out-of-scope benefit is also found by one distinctive word of its own name, or
  (out of scope) by the head of the benefit its value names; "two for one" / "2-for-1" / BOGOF is the
  1 + 1 deal;
- disclosures in other words: minimum age ("18+", "aged eighteen or over"), eligibility ("if you
  qualify"), Monday to Friday ("weekdays", "Mon–Fri", "weekdays 8–6"), a survey with two adjectives
  and free said otherwise ("which costs nothing"), new customers ("if you haven't been to us before",
  "newcomers"), a confirmed booking ("once you've booked"), contents that vary, "<amount in words> each";
- a staff credential named by its body or school ("DBS-checked", "conservatoire-trained") needs a fact
  (adverbs like "well-" / "fully-trained" are not one); a track record count ("12,000 happy moves",
  "3,400 weddings since 2009") needs a fact.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from . import facts as F

BLOCKING = {"conflict_or_expired", "wrong_scope", "missing_disclosure", "forbidden_phrase", "slot_blocked"}

# ---------- values

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a": 1, "an": 1, "single": 1}
CURRENCY = {"£": "GBP", "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "$": "USD", "us$": "USD", "usd": "USD",
            "dollar": "USD", "dollars": "USD", "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR",
            "etb": "ETB", "birr": "ETB", "p": "GBP-p"}
MULT = {"k": 1000, "m": 1_000_000, "bn": 1_000_000_000, "thousand": 1000, "million": 1_000_000}
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
MONEY_PRE = re.compile(r"(?<![\w])(?P<cur>US\$|£|\$|€|GBP|USD|EUR|ETB)\s?(?P<num>" + _NUM + r")"
                       r"(?:\s?(?P<mult>k|K|m|M|bn|thousand|million)\b)?", re.I)
MONEY_POST = re.compile(r"(?<![\w.,])(?P<num>" + _NUM + r")\s?(?P<mult>k|K|m|M|bn|thousand|million)?\s?"
                        r"(?P<cur>GBP|USD|EUR|ETB|birr|pounds?|dollars?|euros?|p)\b", re.I)
PERCENT = re.compile(r"(?<![\w.,])(?P<num>\d+(?:\.\d+)?)\s?(?:%|percent\b|per\s?cent\b)", re.I)
UNITS = {
    "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour", "h": "hour",
    "minute": "minute", "minutes": "minute", "min": "minute", "mins": "minute",
    "day": "day", "days": "day", "working day": "working_day", "working days": "working_day",
    "business day": "working_day", "business days": "working_day",
    "week": "week", "weeks": "week", "wk": "week", "wks": "week",
    "month": "month", "months": "month", "year": "year", "years": "year", "yr": "year", "yrs": "year",
    "night": "night", "nights": "night", "person": "person", "people": "person", "guest": "person",
    "guests": "person", "adult": "person", "adults": "person", "seat": "seat", "seats": "seat",
    "user": "user", "users": "user", "room": "room", "rooms": "room", "session": "session",
    "sessions": "session", "course": "course", "courses": "course", "class": "class", "classes": "class",
    "kg": "kg", "g": "g", "mm": "mm", "cm": "cm", "km": "km", "ml": "ml", "litre": "l", "litres": "l",
    "liter": "l", "liters": "l", "bar": "bar", "psi": "psi", "°c": "degc", "°f": "degf", "mph": "mph",
    "gb": "gb", "tb": "tb", "mb": "mb", "miles": "mile", "mile": "mile", "metres": "metre", "meters": "metre",
    "integrations": "integration", "integration": "integration", "locations": "location",
    "branches": "branch", "clients": "client", "customers": "customer", "projects": "project",
    "kw": "kw", "kwh": "kwh", "kva": "kva", "watt": "w", "watts": "w", "hp": "hp", "rpm": "rpm", "hz": "hz",
    "khz": "khz", "volt": "volt", "volts": "volt", "amp": "amp", "amps": "amp",
}
_UNIT_ALT = "|".join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True))
QTY = re.compile(r"(?<![\w.,£$€])(?P<num>\d+(?:\.\d+)?|" + "|".join(k for k in NUMBER_WORDS if len(k) > 2) +
                 r")(?:\s?-\s?|\s)?(?P<unit>" + _UNIT_ALT + r")(?![\w])", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                          "nov", "dec"])}
_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_DM = re.compile(r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MON + r"\b(?:,?\s+(?P<y>\d{4}))?", re.I)
DATE_MD = re.compile(r"\b" + _MON + r"\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(?P<y>\d{4}))?", re.I)
DATE_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
DATE_SLASH = re.compile(r"\b(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4}|\d{2})\b")
TIME = re.compile(r"\b(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s?(?P<ap>am|pm)\b|\b(?P<h2>\d{1,2}):(?P<mi2>\d{2})\b", re.I)
# Alphanumeric values: PN40, IP67, M12, DN50 (letters then digits, no space or hyphen, so a model
# name like KV-50 or a standard like "ISO 9001" is not one) and grades like 316L. A same-family
# code with another number is a different value.
CODE = re.compile(r"(?<![\w\-/])(?P<p>[A-Z]{1,3})(?P<n>\d{1,4})(?P<s>[A-Z]?)(?![\w\-])")
GRADE = re.compile(r"(?<![\w\-/.])(?P<n>\d{3})(?P<s>[A-Z]{1,2})(?![\w\-])")
MULTI = re.compile(r"(?<![\w.,])(?P<a>\d{1,3})\s?[x×]\s?(?P<b>\d+(?:\.\d+)?)\s?(?P<unit>" + _UNIT_ALT + r")(?![\w])", re.I)
# Offers said in words: "a tenth off", "half-price", "knock half off", "half the usual £199",
# "a tenner", "a fiver", "20 quid", "ten per cent".
FRACTION = {"tenth": 10, "fifth": 20, "quarter": 25, "half": 50}
PCT_WORDS = re.compile(r"(?i:\b(?:(?:a|one)\s+(?P<fr>tenth|fifth|quarter)(?=\s+(?:off|discount)\b)|"
                       r"(?:save|saving|knock|take|takes|taking)\s+(?:a|one)\s+(?P<fr2>tenth|fifth|quarter)\b|"
                       r"(?P<half>half)(?:[- ]price\b|\s+off\b|\s+the\s+(?:usual|normal|regular|standard|full)"
                       r"(?:\s+(?:price|cost|fee|rate|amount)\b|(?=\s*[£$€])))))")
_WORD_NUM = {"ten": 10, "fifteen": 15, "twenty": 20, "twenty-five": 25, "thirty": 30, "forty": 40, "fifty": 50,
             "five": 5}
PCT_NUMWORD = re.compile(r"(?i:\b(?P<w>five|ten|fifteen|twenty(?:-five)?|thirty|forty|fifty)\s+(?:per\s?cent|percent)\b)")
MONEY_SLANG = re.compile(r"(?i:\b(?:a|one)\s+(?P<s>tenner|fiver)\b|(?<![\w.,£$€-])(?P<n>\d+|ten|five|twenty|fifty)\s+quid\b)")
# Round 12: amounts and percentages written in words, read into the same numeric values as "£36" /
# "96%": "twelve quid", "thirty-six pounds", "a hundred quid", "one hundred and twenty pounds", "fifty
# p", "ninety-six per cent", "half a percent". The whole number is read ("thirty-five quid" is 35,
# never "five quid"). A number word with no currency / percent word after it ("one of our", "a couple
# of", "first") is not a value, "pounds of" is a weight, and "a hundred per cent sure" (an idiom, or
# more than 100%) is not a percentage.
_W1 = r"one|two|three|four|five|six|seven|eight|nine"
_WTEEN = r"ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
_WTENS = r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_W99 = r"(?:(?:" + _WTENS + r")(?:[\s-](?:" + _W1 + r"))?|" + _WTEEN + r"|" + _W1 + r")"
_WH = r"(?:a|" + _W1 + r")\s+hundred(?:\s+(?:and\s+)?" + _W99 + r")?"
WORD_NUM = (r"(?:(?P<th>a|" + _W99 + r")\s+thousand(?:,?\s+(?:and\s+)?(?P<rest>" + _WH + r"|" + _W99 + r"))?"
            r"|(?P<h>" + _WH + r")|(?P<x>" + _W99 + r"))")
_WVAL = {w: i + 1 for i, w in enumerate(_W1.split("|"))}
_WVAL.update({w: i + 10 for i, w in enumerate(_WTEEN.split("|"))})
_WVAL.update({w: (i + 2) * 10 for i, w in enumerate(_WTENS.split("|"))})
_WVAL["a"] = 1
MONEY_WORDS = re.compile(r"(?i:(?<![\w-])" + WORD_NUM + r"\s+(?P<cur>pounds?(?!\s+of\b)|quid|pence|p(?![\w.])|"
                         r"dollars?|euros?)(?![\w-])"
                         # "nine pounds fifty" = £9.50 (pence in words after pounds; not "five pounds twenty minutes")
                         r"(?:\s+(?P<pence>(?:" + _WTENS + r")(?:[\s-](?:" + _W1 + r"))?|" + _WTEEN + r"|" + _W1 + r")(?![\w-])"
                         r"(?!\s+(?:minutes?|mins?|hours?|hrs?|people|guests|percent|per\s?cent|pounds?|quid|pence|p\b|"
                         r"years?|months?|weeks?|days?|nights?|miles?|km|times?|of\b)))?)")
PCT_WORDS_NUM = re.compile(r"(?i:(?<![\w-])(?:(?P<half>half)\s+a|" + WORD_NUM + r")\s+(?:per\s?cent|percent)\b"
                           r"(?!\s+(?:sure|certain|confident|committed|behind|right|honest|with|focused|dedicated|serious|"
                           r"true|yes|agree|on|that|about|happy|satisfied)\b))")


def word_number(m: re.Match) -> int | None:
    """The value of a WORD_NUM match: "thirty-six" = 36, "a hundred" = 100, "one hundred and twenty" = 120."""
    def small(s):
        if not s:
            return 0
        parts = [p for p in re.split(r"[\s-]+", s.lower()) if p != "and"]
        if "hundred" in parts:
            i = parts.index("hundred")
            return sum(_WVAL.get(p, 0) for p in parts[:i]) * 100 + sum(_WVAL.get(p, 0) for p in parts[i + 1:])
        return sum(_WVAL.get(p, 0) for p in parts)
    if m.group("th"):
        n = small(m.group("th")) * 1000 + small(m.group("rest"))
    else:
        n = small(m.group("h") or m.group("x"))
    return n or None


# "open till eight", "we pour until eleven", "open until 8 on weeknights": a closing hour said with no
# am / pm, in a sentence about opening hours, is an afternoon / evening hour (1 to 11 -> pm)
CLOSE_HOUR = re.compile(r"(?i:\b(?:until|till|til|'til)\s+(?P<n>1[01]|[1-9]|" + _W1 + r"|ten|eleven)"
                        r"(?:\s*o['’]clock)?(?=\s*(?:$|[,.;:!?)–—]|-(?!\d)|(?:on|every|each|at|and|daily|nightly|"
                        r"weekdays?|weeknights?)\b|(?:mon|tue|wed|thu|fri|sat|sun)(?:day|days|s)?\b)))")
HOURS_CONTEXT = re.compile(r"(?i:\b(?:open\w*|clos\w*|shut\w*|hours|weeknights?|evenings?|late|pour\w*|serv\w*)\b)")
# round the clock in other words: "day and night, weekends included", "night and day, seven days a
# week" (said exactly); "day and night" on its own is only a hint
ALL_HOURS_WORDS = re.compile(r"(?i:\b(?:day\s+and\s+night|night\s+and\s+day)\b,?\s+(?:(?:and\s+)?(?:weekends?\s+(?:included|too)|"
                             r"(?:including|incl\.?)\s+weekends|seven\s+days\s+a\s+week|7\s+days\s+a\s+week|"
                             r"every\s+day(?:\s+of\s+the\s+year)?|365\s+days\s+a\s+year)))")
ALL_HOURS_WORDS_HINT = re.compile(r"(?i:\b(?:day\s+and\s+night|night\s+and\s+day)\b)")
# round-the-clock, said exactly ("24/7", "round-the-clock", "24 hours a day") or described ("any
# hour", "even at 2am", "any time of the night": a hint only)
ALL_HOURS = re.compile(r"(?i:(?<![\w/])24\s?/\s?7(?![\w/])|\b(?:a)?round[- ]the[- ]clock\b|\b24\s+hours\s+a\s+day\b|"
                       r"\b24[- ]hours?\s+access\b)")
ALL_HOURS_HINT = re.compile(r"(?i:\bany\s+hour\b|\bany\s+time\s+of\s+(?:the\s+)?(?:day|night)\b|"
                            r"\b(?:even\s+)?at\s+[1-4]\s?am\b|\bmiddle\s+of\s+the\s+night\b)")
# How often: "eight return sailings a day", "4 trains an hour", "twice a week". A number with a
# countable run noun (not a unit already read as a quantity: "3 sessions" stays a quantity).
_FREQ_NUM = r"\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_FREQ_NOUN = (r"times|sailings?|departures?|trains?|buses|bus|flights?|crossings?|trips?|runs?|services?|deliveries|"
              r"collections?|visits?|shows?|screenings?|tours?|journeys?|boats?|ferries|ferry")
FREQ = re.compile(r"(?i:(?<![\w.,£$€])(?:(?P<num>" + _FREQ_NUM + r")\s+(?:[a-z-]+\s+){0,2}?(?:" + _FREQ_NOUN + r")|"
                  r"(?P<adv>once|twice|thrice))\s+(?:a|an|per|each|every)\s+(?P<per>day|week|fortnight|month|hour|year)\b)")
_ADV = {"once": 1, "twice": 2, "thrice": 3}
# "the barn seats 180", "sleeps 6", "accommodates up to 40": a number of people, with no unit after it
CAPACITY = re.compile(r"(?i:\b(?:seats|sleeps|accommodates|caters\s+for)\s+(?:up\s+to\s+)?(?P<num>\d{1,4})(?![\w%£$€.,]|\s+[a-z])"
                      r"|\b(?:seats|sleeps|accommodates|caters\s+for)\s+(?:up\s+to\s+)?(?P<n2>\d{1,4})(?=\s+(?:for|at|in|on|and|with)\b))")
DEADLINE = re.compile(r"\b(?:ends?|ending|until|till|til|by|before|deadline|last (?:day|chance)|expires?|closes?)\b", re.I)


@dataclass
class Val:
    kind: str            # money|percent|qty|date|time
    key: tuple
    text: str
    start: int
    end: int
    extra: dict = field(default_factory=dict)


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def _canon(d: Decimal) -> str:
    q = d.quantize(Decimal("0.01"))
    s = format(q.normalize(), "f")
    return s


def _overlaps(spans, a, b) -> bool:
    return any(a < y and x < b for x, y in spans)


def extract(text: str) -> list[Val]:
    out: list[Val] = []
    taken: list[tuple[int, int]] = []

    def add(v: Val):
        if not _overlaps(taken, v.start, v.end):
            out.append(v)
            taken.append((v.start, v.end))

    for rx in (MONEY_PRE, MONEY_POST):
        for m in rx.finditer(text):
            amt = _dec(m.group("num"))
            if amt is None:
                continue
            cur = CURRENCY.get(m.group("cur").lower())
            if cur is None:
                continue
            mult = (m.group("mult") or "").lower()
            if mult:
                amt *= MULT[mult]
            if cur == "GBP-p":
                if mult or "." in m.group("num"):
                    continue
                cur, amt = "GBP", amt / 100
            add(Val("money", ("money", _canon(amt), cur), m.group(0), m.start(), m.end()))
    for m in CODE.finditer(text):
        add(Val("code", ("code", m.group("p") + "#" + m.group("s"), m.group("n").lstrip("0") or "0"),
                m.group(0), m.start(), m.end()))
    for m in GRADE.finditer(text):
        add(Val("code", ("code", "#" + m.group("s"), m.group("n")), m.group(0), m.start(), m.end()))
    for m in MULTI.finditer(text):
        unit = UNITS.get(" ".join(m.group("unit").lower().split()))
        b = _dec(m.group("b"))
        if unit and b is not None:
            add(Val("qty", ("qty", f"{int(m.group('a'))}x{_canon(b)}", unit), m.group(0), m.start(), m.end()))
    for m in PERCENT.finditer(text):
        d = _dec(m.group("num"))
        if d is not None:
            add(Val("percent", ("percent", _canon(d)), m.group(0), m.start(), m.end()))
    for m in PCT_WORDS.finditer(text):
        fr = (m.group("fr") or m.group("fr2") or m.group("half") or "").lower()
        add(Val("percent", ("percent", _canon(Decimal(FRACTION[fr]))), m.group(0), m.start(), m.end(), {"words": True}))
    for m in PCT_WORDS_NUM.finditer(text):
        n = Decimal("0.5") if m.group("half") else word_number(m)
        if n is not None and n <= 100:
            add(Val("percent", ("percent", _canon(Decimal(n))), m.group(0), m.start(), m.end(), {"words": True}))
    for m in MONEY_WORDS.finditer(text):
        n = word_number(m)
        if n is None:
            continue
        cur = m.group("cur").lower()
        amt = Decimal(n) / 100 if cur in ("p", "pence") else Decimal(n)
        if m.group("pence") and cur.startswith(("pound", "quid")):
            parts = [x for x in re.split(r"[\s-]+", m.group("pence").lower()) if x]
            pn = sum(_WVAL.get(x, 0) for x in parts)
            if 0 < pn < 100:
                amt += Decimal(pn) / 100
        cur = "GBP" if cur in ("p", "pence", "quid") else CURRENCY.get(cur, "GBP")
        add(Val("money", ("money", _canon(amt), cur), m.group(0), m.start(), m.end(), {"words": True}))
    for m in PCT_NUMWORD.finditer(text):
        add(Val("percent", ("percent", _canon(Decimal(_WORD_NUM[m.group("w").lower()]))), m.group(0), m.start(), m.end(),
                {"words": True}))
    for m in MONEY_SLANG.finditer(text):
        raw = (m.group("s") or m.group("n") or "").lower()
        amt = {"tenner": 10, "fiver": 5}.get(raw) or _WORD_NUM.get(raw) or int(raw)
        add(Val("money", ("money", _canon(Decimal(amt)), "GBP"), m.group(0), m.start(), m.end(), {"words": True}))
    for m in ALL_HOURS.finditer(text):
        add(Val("hours", ("hours", "24/7"), m.group(0), m.start(), m.end()))
    for m in ALL_HOURS_WORDS.finditer(text):
        add(Val("hours", ("hours", "24/7"), m.group(0), m.start(), m.end()))
    for rx in (ALL_HOURS_HINT, ALL_HOURS_WORDS_HINT):
        for m in rx.finditer(text):
            add(Val("hours", ("hours", "24/7"), m.group(0), m.start(), m.end(), {"hint": True}))
    if HOURS_CONTEXT.search(text):
        for m in CLOSE_HOUR.finditer(text):
            raw = m.group("n").lower()
            h = int(raw) if raw.isdigit() else _WVAL.get(raw)
            if h and not TIME.match(text, m.start("n")):
                add(Val("time", ("time", f"{h + 12:02d}:00"), m.group(0), m.start("n"), m.end()))
    for rx in (DATE_ISO, DATE_SLASH):
        for m in rx.finditer(text):
            try:
                y = int(m.group("y"))
                y = y + 2000 if y < 100 else y
                d = date(y, int(m.group("m")), int(m.group("d")))
            except ValueError:
                continue
            add(Val("date", ("date", d.month, d.day), m.group(0), m.start(), m.end(), {"year": d.year}))
    for rx in (DATE_DM, DATE_MD):
        for m in rx.finditer(text):
            mon = MONTHS[m.group("mon")[:3].lower()]
            day = int(m.group("d"))
            if not 1 <= day <= 31:
                continue
            y = int(m.group("y")) if m.group("y") else None
            add(Val("date", ("date", mon, day), m.group(0), m.start(), m.end(), {"year": y}))
    for m in TIME.finditer(text):
        h = int(m.group("h") or m.group("h2"))
        mi = int(m.group("mi") or m.group("mi2") or 0)
        ap = (m.group("ap") or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h > 23 or mi > 59:
            continue
        add(Val("time", ("time", f"{h:02d}:{mi:02d}"), m.group(0), m.start(), m.end()))
    for m in QTY.finditer(text):
        raw = m.group("num").lower()
        num = Decimal(NUMBER_WORDS[raw]) if raw in NUMBER_WORDS else _dec(raw)
        unit = UNITS.get(" ".join(m.group("unit").lower().split()))
        if num is None or unit is None:
            continue
        add(Val("qty", ("qty", _canon(num), unit), m.group(0), m.start(), m.end()))
    for m in CAPACITY.finditer(text):
        add(Val("qty", ("qty", _canon(Decimal(m.group("num") or m.group("n2"))), "person"), m.group(0), m.start(), m.end()))
    for m in FREQ.finditer(text):
        raw = (m.group("num") or m.group("adv")).lower()
        n = _ADV.get(raw) or NUMBER_WORDS.get(raw) or int(raw)
        add(Val("freq", ("freq", _canon(Decimal(n)), m.group("per").lower()), m.group(0), m.start(), m.end()))
    return sorted(out, key=lambda v: v.start)


_NEG_BEFORE = re.compile(r"\b(?:not|no|never|non|nor)[\s-]+(?:(?:an?|rated|any|the)\s+)?$", re.I)


def _not_negated_vals(text: str) -> list[Val]:
    """Values of a fact's sentence, less the ones it says it is NOT ("classified A2 (not A1)")."""
    return [v for v in extract(text) if not _NEG_BEFORE.search(text[max(0, v.start - 20):v.start])]


# ---------- graded / levelled identifiers: "Level 2", "EPC B", "AEO-C", "Euroclass A2-s1,d0", "Type II"

SCHEME_WORDS = frozenset("level class grade type category band tier euroclass".split())
GENERIC_SCHEMES = frozenset("level class grade type category band tier star".split())  # need a tie to the fact
_GRADE_TOK = (r"(?P<g>(?:IV|VI{0,3}|I{1,3})(?![A-Za-z0-9])|[A-Z]{1,2}\d{0,2}(?![A-Za-z0-9])|\d{1,2}[A-Z]?(?![A-Za-z0-9]))"
              r"(?P<suf>-[a-z]\d(?:,\s?[a-z]\d)*)?(?![\w-]|[.,:/]\d)")
_SCHEME_SEP = r"(?:[ \t]?-[ \t]?|[ \t])"
FACT_SCHEME = re.compile(r"(?<![\w-])(?P<s>(?i:" + "|".join(sorted(SCHEME_WORDS)) + r")|[A-Z]{2,6})" + _SCHEME_SEP + _GRADE_TOK)
STAR_SCHEME = re.compile(r"(?<![\w.])(?P<g>[1-7])[- ]?star\b", re.I)
SCHEME_TYPES = {"certification", "credential", "spec"}
# a scheme's own name then a grade in words: "Cytech Technical Two", "Stage Three" (certifications
# and credentials only, so a product name like "Tempo 3" is never one)
NUM_GRADE = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
             "eight": "8", "nine": "9", "ten": "10"}
_NUM_GRADE_ALT = "|".join(NUM_GRADE)
NAMED_GRADE = re.compile(r"(?<![\w-])(?P<s>[A-Z][a-z]{3,})[ \t](?P<g>(?i:" + _NUM_GRADE_ALT + r")|\d{1,2})(?![\w-])")


@dataclass
class Grade:
    scheme: str      # "level", "EPC", "euroclass", "star"
    grade: str       # "2", "B", "A2"
    suffix: str      # "-s1,d0" or ""
    start: int = 0
    end: int = 0
    text: str = ""
    words: bool = False  # the grade was written in words ("Class Two", "grade six")


def _grade_norm(g: str) -> str:
    return ROMAN.get(g.lower(), g) if re.fullmatch(r"I{1,3}|IV|V", g) else g.upper()


def _scheme_norm(s: str) -> str:
    return s.lower() if s.lower() in SCHEME_WORDS else s


def fact_grades(f: dict) -> list[Grade]:
    """The scheme + grade a fact states, from its own value / value text (never its free sentence,
    which may say what it is NOT). Only facts whose value is a string, or that are certifications,
    credentials or specs, or whose attribute names a scheme, carry one."""
    val = f.get("value")
    attr = str(f.get("attribute") or "").replace("_", " ").lower()
    if not (isinstance(val, str) or f.get("fact_type") in SCHEME_TYPES or set(attr.split()) & SCHEME_WORDS):
        return []
    out = []
    for t in ([val] if isinstance(val, str) else []) + [f.get("value_text") or ""]:
        for m in FACT_SCHEME.finditer(t):
            out.append(Grade(_scheme_norm(m.group("s")), _grade_norm(m.group("g")), (m.group("suf") or "").replace(" ", "")))
        for m in STAR_SCHEME.finditer(t):
            out.append(Grade("star", m.group("g"), ""))
        if f.get("fact_type") in ("certification", "credential"):
            for m in NAMED_GRADE.finditer(t):
                if m.group("s").lower() not in SCHEME_WORDS and m.group("g").lower() in NUM_GRADE:
                    out.append(Grade(m.group("s"), NUM_GRADE[m.group("g").lower()], ""))
    return out


def named_grade_regex(schemes: set[str]):
    """"Technical Three" / "Technical 3" in copy for the named schemes the facts grade in words."""
    names = sorted((x for x in schemes if re.fullmatch(r"[A-Z][a-z]{3,}", x)), key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?<![\w-])(?P<s>" + "|".join(map(re.escape, names)) + r")[ \t](?P<g>(?i:" + _NUM_GRADE_ALT +
                      r")|\d{1,2})(?![\w-])")


def text_grades(f: dict) -> list[Grade]:
    """Scheme + grade named in a fact's own sentence ("Trade Tier 2 price: ..."), less the ones it
    says it is not. Used only to recognise an out-of-scope / expired fact, never to conflict."""
    t = " . ".join(x for x in (f.get("text") or "", F.subject_ref(f)) if x)
    out = []
    for m in FACT_SCHEME.finditer(t):
        if _NEG_BEFORE.search(t[max(0, m.start() - 20):m.start()]):
            continue
        if m.group("s").lower() in SCHEME_WORDS:
            out.append(Grade(_scheme_norm(m.group("s")), _grade_norm(m.group("g")), (m.group("suf") or "").replace(" ", "")))
    return out


def scheme_regex(schemes: set[str]):
    """Finds "<scheme> <grade>" in copy for the schemes the facts use (acronyms keep their case)."""
    words = sorted((s for s in schemes if s in SCHEME_WORDS), key=len, reverse=True)
    acr = sorted((s for s in schemes if s not in SCHEME_WORDS and s != "star"), key=len, reverse=True)
    alts = ([r"(?i:" + "|".join(map(re.escape, words)) + r")"] if words else []) + [re.escape(a) for a in acr]
    if not alts:
        return None
    return re.compile(r"(?<![\w-])(?P<s>" + "|".join(alts) + r")" + _SCHEME_SEP + _GRADE_TOK)


WORD_GRADE = re.compile(r"(?<![\w-])(?P<s>(?i:" + "|".join(sorted(SCHEME_WORDS)) + r"))[ \t-](?P<g>(?i:" + _NUM_GRADE_ALT +
                        r"))(?![\w-])")


def sentence_grades(s: str, rx, stars: bool, named=None, schemes=frozenset()) -> list[Grade]:
    out = []
    if named is not None:
        for m in named.finditer(s):
            g = m.group("g")
            out.append(Grade(m.group("s"), NUM_GRADE.get(g.lower(), g), "", m.start(), m.end(), m.group(0),
                             words=g.lower() in NUM_GRADE))
    # a generic scheme word the facts grade on, then a grade in words: "Class Two", "grade six"
    for m in WORD_GRADE.finditer(s):
        sc = m.group("s").lower()
        if sc in schemes and not any(g.start <= m.start() < g.end for g in out):
            out.append(Grade(sc, NUM_GRADE[m.group("g").lower()], "", m.start(), m.end(), m.group(0), words=True))
    if rx is not None:
        for m in rx.finditer(s):
            if any(g.start <= m.start() < g.end for g in out):
                continue
            out.append(Grade(_scheme_norm(m.group("s")), _grade_norm(m.group("g")),
                             (m.group("suf") or "").replace(" ", ""), m.start(), m.end(), m.group(0)))
    if stars:
        for m in STAR_SCHEME.finditer(s):
            out.append(Grade("star", m.group("g"), "", m.start(), m.end(), m.group(0)))
    return out


def fact_values(f: dict) -> tuple[set, set]:
    """(primary, secondary) value keys. Primary: the fact's own value and value text (a stale or
    out-of-scope fact is flagged only on these). Secondary: values its sentence mentions (enough
    for a match, never for a flag) unless it says it is not that value."""
    primary = {v.key for v in extract(f.get("value_text") or "")}
    val, unit, cur = f.get("value"), (f.get("unit") or "").strip().lower(), (f.get("currency") or "").strip()
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        d = Decimal(str(val))
        if cur and CURRENCY.get(cur.lower(), cur.upper()):
            primary.add(("money", _canon(d), CURRENCY.get(cur.lower(), cur.upper())))
        elif unit in ("%", "percent", "per cent"):
            primary.add(("percent", _canon(d)))
        elif UNITS.get(unit):
            primary.add(("qty", _canon(d), UNITS[unit]))
        elif not cur and not any(k[0] == "qty" and k[1] == _canon(d) for k in primary):
            # a unit written as a phrase ("seated_guests" -> guests), or a value text that puts one
            # word between the number and its unit ("180 seated guests")
            tail = re.split(r"[\s_-]+", unit)[-1] if unit else ""
            if UNITS.get(tail):
                primary.add(("qty", _canon(d), UNITS[tail]))
            primary |= _value_qty(d, f.get("value_text") or "")
    elif isinstance(val, str) and val.strip():
        primary |= {v.key for v in extract(val)}
    if F.subject_kind(f) in ("offer", "package", "event") or f.get("fact_type") in ("event", "availability"):
        end = F._day(f.get("valid_to"))
        if end:
            primary.add(("date", end.month, end.day))
    secondary = {v.key for v in _not_negated_vals(f.get("text") or "")}
    for c in f.get("conditions") or []:
        if isinstance(c, dict) and isinstance(c.get("value"), (int, float)) and not isinstance(c.get("value"), bool):
            key = str(c.get("key") or "").lower()
            if UNITS.get(key) or UNITS.get(key.rstrip("s")):
                secondary.add(("qty", _canon(Decimal(str(c["value"]))), UNITS.get(key) or UNITS[key.rstrip("s")]))
    return primary, secondary


def _value_qty(d: Decimal, vt: str) -> set:
    """A unitless value whose value text puts one word between it and a unit: value 180,
    "180 seated guests" -> 180 guests."""
    out = set()
    for m in re.finditer(r"(?<![\w.,£$€])(\d[\d,]*(?:\.\d+)?)\s+[a-z]+\s+(" + _UNIT_ALT + r")(?![\w])", vt, re.I):
        if _dec(m.group(1)) == d and UNITS.get(" ".join(m.group(2).lower().split())):
            out.add(("qty", _canon(d), UNITS[" ".join(m.group(2).lower().split())]))
    return out


def core_values(f: dict) -> set:
    """The keys of the fact's own value: its value field (with unit / currency) and its value text
    outside brackets. "£66 per full day (8am to 6pm)" with value 66 GBP -> only £66."""
    val, unit, cur = f.get("value"), (f.get("unit") or "").strip().lower(), (f.get("currency") or "").strip()
    out = set()
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        d = Decimal(str(val))
        if cur:
            out.add(("money", _canon(d), CURRENCY.get(cur.lower(), cur.upper())))
        elif unit in ("%", "percent", "per cent"):
            out.add(("percent", _canon(d)))
        elif UNITS.get(unit):
            out.add(("qty", _canon(d), UNITS[unit]))
    elif isinstance(val, str) and val.strip():
        out = {v.key for v in extract(val)}
    # the value text's own values, less what it adds in brackets ("£66 per full day (8am to 6pm)")
    out |= {v.key for v in extract(re.sub(r"\([^)]*\)", " ", f.get("value_text") or ""))}
    if not out:
        out = fact_values(f)[0]
    if F.subject_kind(f) in ("offer", "package", "event") or f.get("fact_type") in ("event", "availability"):
        end = F._day(f.get("valid_to"))
        if end:
            out.add(("date", end.month, end.day))
    return out


SAVE_BEFORE = re.compile(r"(?i:\b(?:save|saves|saving|savings\s+of|knock|knocks|knocking|take|takes|taking)\s+(?:(?:up\s+to|an?\s+extra|a\s+full)\s+)?$)")
SAVE_AFTER = re.compile(r"(?i:^\s*(?:off|discount|saving|cheaper|less)\b)")
WAS_RX = re.compile(r"(?i:\b(?:instead\s+of|was|usually|normally|down\s+from|rather\s+than|reduced\s+from|not)\s+)")
SAVE_CUE = re.compile(r"(?i:\b(?:save[sd]?|saving|savings|off|knock\w*|discount\w*|less|cheaper|reduced|slash\w*|cut)\b)")


def offer_savings(f: dict) -> set:
    """The saving an offer gives, in money or percent, as its own text states it: "(save £20)",
    "£15 off", or the difference in "£90 instead of £110". The offer's price itself is its value."""
    if F.subject_kind(f) != "offer":
        return set()
    t = " . ".join(str(x) for x in (f.get("value_text"), f.get("text")) if x)
    out = set()
    vals = [v for v in extract(t) if v.kind in ("money", "percent")]
    for v in vals:
        if SAVE_BEFORE.search(t[max(0, v.start - 30):v.start]) or SAVE_AFTER.search(t[v.end:v.end + 12]):
            out.add(v.key)
    for a, b in zip(vals, vals[1:]):        # "£90 instead of £110": the saving is £20
        if a.kind == b.kind == "money" and a.key[2] == b.key[2] and WAS_RX.fullmatch(t[a.end:b.start].strip() + " "):
            d = Decimal(b.key[1]) - Decimal(a.key[1])
            if d > 0:
                out.add(("money", _canon(d), a.key[2]))
    return out


# ---------- keywords

_KW = [
    ("certified", r"\bcertified\b", "cert"), ("certification", r"\bcertifications?\b", "cert"),
    ("accredited", r"\baccredit(?:ed|ation)\b", "cert"),
    # only claims when a named body/identifier sits next to them ("HCPC registered", "Google Partner")
    ("registered", r"\bregistered\b", "cert_named"), ("partner", r"\bpartner(?:ed)?\b", "cert_named"),
    ("guarantee", r"\bguarantee[ds]?\b", "policy"), ("cure", r"\bcure[sd]?\b", "health"),
    ("specialist", r"\bspecialists?\b", "credential"), ("uptime", r"\buptime\b", "security"),
    ("made in", r"\bmade in\b", "origin"), ("free delivery", r"\bfree (?:delivery|shipping)\b", "policy"),
    ("was £", r"\bwas\s+£", "price_reference"),
    ("ends", r"\bends?\b(?=\s+(?:on\b|at\b|soon\b|this\b|next\b|tonight\b|today\b|tomorrow\b|midnight\b|\d|"
             r"mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))", "offer_end"),
]
_KW_CASE = [("approved", r"\b[A-Z]{2,}[- ][Aa]pproved\b|\b[Aa]pproved\s+by\b|"
                         r"\b(?:[Gg]overnment|[Cc]linically|[Oo]fficially|[Cc]ouncil)[- ]approved\b", "cert"),
            ("SOC 2", r"\bSOC\s?2\b(?:\s+Type\s+(?:II|I|1|2)\b)?", "security"),
            ("ISO", r"\bISO(?:\s?\d{3,5}(?::\d{4})?)?\b", "cert"),
            ("UL", r"\bUL\b(?:[- ](?:[Ll]isted|[Rr]ecogni[sz]ed|[Cc]lassified|[Cc]ertified))?", "cert"),
            ("FDA", r"\bFDA\b", "cert"), ("NHS", r"\bNHS\b", "health"),
            # a conformity mark named by its acronym: "UKCA-marked", "CE marked", "UKCA and CE marks"
            ("marked", r"\b[A-Z]{2,6}(?:\s*(?:and|&|/|,)\s*[A-Z]{2,6})?[- ][Mm]ark(?:ed|s|ing)?\b", "cert")]
KEYWORDS = [(n, re.compile(p, re.I), c) for n, p, c in _KW] + [(n, re.compile(p), c) for n, p, c in _KW_CASE]
# "approved by 12 noon", "signed off by Friday", "approved by the end of the day": a deadline, not an
# authority. A time of day needs its am / pm / noon / o'clock or its minutes ("approved by 12
# councils" stays a claim), a date needs its month.
_MON_NC = _MON.replace("(?P<mon>", "(?:")
BY_DEADLINE = re.compile(
    r"(?i:^[ \t]+(?:(?:12[ \t]*)?noon|midday|midnight|(?:today|tonight|tomorrow)\b|"
    r"\d{1,2}(?:[:.]\d{2})?[ \t]*(?:a\.?m\.?|p\.?m\.?|noon|midday|o['’]?clock)(?![\w])|\d{1,2}[:.]\d{2}(?![\w.:])|"
    r"(?:(?:this|next|the)[ \t]+)?(?:mon|tues|wednes|thurs|fri|satur|sun)day\b|"
    r"(?:the[ \t]+)?(?:end|close|start)[ \t]+of[ \t]+(?:the[ \t]+)?(?:day|week|month|business|play|term)\b|"
    r"(?:the[ \t]+)?(?:deadline|cut[- ]?off(?:[ \t]+time)?)\b|(?:eod|cob)\b|"
    r"(?:the[ \t]+)?\d{1,2}(?:st|nd|rd|th)?[ \t]+(?:of[ \t]+)?" + _MON_NC + r"\b|" + _MON_NC + r"[ \t]+\d{1,2}(?:st|nd|rd|th)?\b))")
CLASS_SUPPORT = {  # a fact of these types/classes supports the keyword class when it shares a word
    "cert": ({"certification", "credential"}, {"safety_cert"}),
    "credential": ({"credential"}, set()),
    "security": ({"certification"}, {"security"}),
    "policy": ({"policy"}, set()),
    "origin": (set(), {"origin"}),
    "price_reference": (set(), {"price_reference"}),
    "offer_end": ({"price", "event", "availability"}, {"price_reference"}),
    "health": (set(), set()),
}
GENERIC = frozenset("our we us you your new best great get now today one".split())

# Ratings, awards, superlatives, rankings and customer counts: claims that need a fact of their own.
_COUNT_NOUNS = (r"(?:reviews|ratings|customers|clients|users|members|downloads|installs|businesses|companies|"
                r"students|patients|guests|subscribers|followers|households|families|diners|visitors)")
# words ending in -est that are not superlatives ("the latest", "Leeds's biggest" is one, "the hotel's guest" is not)
_NOT_EST = (r"(?i:latest|nearest|closest|earliest|interest|honest|forest|modest|harvest|contest|request|suggest|digest|"
            r"protest|guest|chest|quest|crest|nest|vest|pest|test|zest|rest|west|lest|arrest|invest|attest|detest|infest|"
            r"unrest|behest|inquest|bequest|conquest|tempest|manifest|earnest|midwest|southwest|northwest|longest\s+serving)")
_RATING = [
    ("voted", r"(?i:\b(?:voted|named|crowned|ranked)\s+(?:(?:as|the|a|an|our)\s+){0,2}"
              r"(?:best|top|favourite|favorite|number\s+one|no\.\s?1)\b)|(?i:\b(?:voted|named|ranked)\s+)#1\b"),
    ("award", r"(?i:\b(?:award|prize)[- ]winning\b|\bawards?\b)"),
    ("stars", r"(?i:(?<![\w.])(?:\d(?:\.\d)?|one|two|three|four|five)[- ]?stars?\b)"),
    ("rated", r"(?i:\b(?:top|highest|best)[- ]rated\b|\brated\s+(?:\d(?:\.\d)?(?:\s?(?:/|out\s+of)\s?\d+)?|"
              r"one|two|three|four|five)(?![\w.])(?!\s*(?:bar|psi|kw|kva|w|watts?|v|volts?|amps?|mm|cm|kg|°|degrees|hp|rpm|"
              r"hz|mph|l|litres?|liters?|tons?|tonnes?|hours?|minutes?)\b))"),
    ("first", r"(?<!\w)#1\b|(?i:\bnumber\s+one\b(?!\s+(?:priority|reason|question|tip|rule|mistake|thing|goal|"
              r"concern|job|choice\s+for\s+you))|\bno\.\s?1\b)"),
    ("best", r"(?i:\bbest[- ]in[- ]class\b)|(?i:\bthe\s+best\s+)(?!(?i:way|time|part|thing|bit|of\s+luck|wishes|"
             r"regards|value|before|of\s+both)\b)(?:[\w&'-]+\s+){0,3}?(?i:in|of|around|across)\s+(?:(?i:the)\s+)?"
             r"(?:(?i:town|city|country|world|region|area|county|market|industry|business|uk|us)\b|[A-Z][\w'-]+)"),
    ("popular", r"(?i:\bmost\s+popular\b)"),
    # "the friendliest vets in Ashby", "the fastest broadband in the county": an -est superlative
    # about a place or market (not "the latest news in town", "the nearest branch in Leeds")
    ("est", r"(?i:\bthe\s+)(?!(?i:latest|nearest|closest|earliest|interest|honest|forest|modest|harvest|contest|"
            r"request|suggest|digest|protest|longest\s+serving)\b)(?P<adj>(?i:[a-z]{3,}est))\s+(?:[\w&'-]+\s+){0,3}?"
            r"(?:(?i:in|of|around|across)\s+(?:(?i:the)\s+)?(?:(?i:whole|entire)\s+)?(?:(?i:town|city|country|world|region|area|"
            r"county|market|industry|business|uk|us|coast|coastline|island|nation|planet)\b|[A-Z][\w'-]+)|"
            r"(?i:on|along)\s+(?i:the)\s+(?:(?i:whole|entire)\s+)?(?i:coast|coastline|island|planet|high\s+street|market)\b)"),
    ("possessive", r"(?:\b[A-Z][\w-]*(?:\s+[A-Z][\w-]*){0,3}|(?i:\b(?:town|city|country|world|nation|region|county|"
                   r"area|village|island)))['’]s\s+(?i:favou?rite|best|most\s+popular|top|number\s+one|#1|best[- ]loved)\b"),
    # "Yorkshire's greenest printer", "the city's friendliest gym": an -est superlative owned by a place
    ("possest", r"(?:\b[A-Z][\w-]*(?:\s+[A-Z][\w-]*){0,3}|(?i:\b(?:town|city|country|world|nation|region|county|"
                r"area|village|island)))['’]s\s+(?!" + _NOT_EST + r"\b)(?P<adj>(?i:[a-z]{3,}est))\b"),
    ("leading", r"(?i:\b(?:industry|market|world|sector)[- ]leading\b|\b(?:the|a|an)\s+leading\s+"
                r"(?!(?:to|up|the|on|from|role|with|into|edge)\b)[a-z]\w+)|\b[A-Z]\w*'s\s+(?i:leading)\b"),
    ("count", r"(?i:(?<![\w.£$€])\d[\d,]*(?:\.\d+)?\s?k?\+\s+(?:[a-z]+\s+){0,2}?" + _COUNT_NOUNS + r"\b|"
              r"\b(?:over|more\s+than|trusted\s+by|join(?:ed\s+by)?)\s+\d[\d,]*(?:\.\d+)?\s?k?\+?\s+"
              r"(?:[a-z]+\s+){0,2}?" + _COUNT_NOUNS + r"\b|"
              # round 12: a count of jobs done said as a track record: "12,000 happy moves", "over 500
              # satisfied installs", "3,400 weddings since 2009" (100 or more, so "3 new courses since
              # 2024" is not one)
              r"(?<![\w.£$€])\d{2,3}(?:,\d{3})*\+?\s+(?:happy|satisfied|successful|completed|delighted)\s+[a-z]+s\b|"
              r"(?<![\w.£$€])(?:\d{1,3}(?:,\d{3})+|\d{3,})\+?\s+(?:[a-z]+\s+){0,2}?[a-z]+s\s+since\s+(?:19|20)\d{2}\b)"),
    # "most reliable", "one of the most energy-efficient": a quality superlative (an adjective,
    # not "most of", "at most", "most recent", "most people", "most likely")
    ("most", r"(?i:(?:\bone\s+of\s+)?(?:\bthe\s+)?(?<!\bat\s)\bmost\s+(?!(?:recent|recently|important|relevant|likely|"
             r"common|frequent|significant|interesting|exciting|popular|of)\b)(?P<adj>[a-z]+(?:-[a-z]+)*"
             r"(?:able|ible|ent|ant|ive|ous|ful|ic|ical|ced|ted|sted|ved|ed|ure|ate|afe)\b))"),
    # "the best-connected space", "the fastest-growing firm", "highest-reviewed": a hyphenated superlative
    ("bestx", r"(?i:\b(?!" + _NOT_EST + r"-)"
              r"(?:best|[a-z]{3,}est)-(?!in-class\b|rated\b|before\b)(?P<adj>[a-z]{3,})\b)"),
    # "4.9 out of 5", "4.8/5": a score (not "2 out of 5 homes", not a date 4/5/2026)
    ("score", r"(?i:(?<![\w./])\d(?:\.\d)?\s?(?:/|out\s+of)\s?(?:5|10)\b(?![/\d.,]\d)"
              r"(?=\s*(?:$|[^\w\s]|(?:stars?|on|from|by|in|for|across|with|rating|ratings|average|overall)\b)))"),
    # "every X is a qualified teacher", "all our therapists are qualified": a credential claim about staff
    ("staff", r"(?i:\b(?:every|each|all)\s+(?:(?:of\s+)?(?:our|the)\s+)?(?:[\w'-]+\s+){0,3}?(?:is|are)\s+"
              r"(?:an?\s+)?(?:fully\s+)?(?:qualified|licensed)\b(?:\s+(?P<prof>[a-z]+))?)"),
]
RATINGS = [(kind, re.compile(p)) for kind, p in _RATING]
# Set phrases that look like a superlative / ranking but claim nothing about the business: "the
# best-kept secret", "best-laid plans", "the best thing since sliced bread", "the best is yet to come",
# "make the best of it", "second-best", "at best", "the worst-kept secret". A rating match inside one
# is dropped (the rest of the sentence is still read).
IDIOMS = re.compile(r"(?i:\b(?:best|worst)[- ]kept(?:\s+secrets?)?\b|\bbest[- ]laid\b|\b(?:best|worst)[- ]case\b|"
                    r"\bbest\s+thing\s+since\s+sliced\s+bread\b|\bthe\s+best\s+is\s+yet\s+to\s+come\b|"
                    r"\b(?:make|making|makes|made)\s+the\s+(?:very\s+)?best\s+of\b|\bsecond[- ]best\b|\bat\s+best\b|"
                    r"\bbest\s+foot\s+forward\b|\bbest\s+of\s+(?:both\s+worlds|luck)\b|\bmay\s+the\s+best\b|"
                    r"\b(?:the\s+)?best\s+(?:bit|part)\s+(?:is|of)\b)")
RATING_WORDS = {  # what a supporting fact must talk about, per kind of claim (stemmed)
    "voted": {"voted", "vote", "award", "won", "winner"}, "award": {"award", "won", "winner", "prize", "medal"},
    "stars": {"star", "rated", "rating"}, "rated": {"rated", "rating", "star", "review"},
    "first": {"#1", "number", "first", "top", "1"}, "best": {"best"}, "popular": {"popular", "bestselling"},
    "leading": {"leading", "leader"}, "count": set(), "most": set(), "score": {"rated", "rating", "star", "review", "score"},
    "staff": set(), "est": set(), "possest": set(), "bestx": set(),
    "possessive": {"favourite", "favorite", "best", "popular", "top", "voted", "award", "loved"},
}
SUPERLATIVE = {"best", "top", "favourite", "favorite", "popular", "leading", "one", "1", "loved"}
OWN_RANGE = {"popular", "best", "first", "leading", "most", "est", "bestx"}   # "our most popular valve": about its own range
RATING_TYPES, RATING_CLASSES = {"result", "claim", "testimonial", "credential", "certification"}, {"comparative", "result"}

# Identity of a certification / approval / partner claim: the standard + number or named body.
_ID_TOK = r"(?:[A-Z][\w&+.']*|\d[\w:.]*)"
RUN_BEFORE = re.compile(r"(" + _ID_TOK + r"(?:[ \t-]+" + _ID_TOK + r"){0,5})[ \t-]*$")
_RUN = r"(" + _ID_TOK + r"(?:[ \t-]+" + _ID_TOK + r"){0,5})"
RUN_AFTER = re.compile(r"^[ \t]+(?:by|with|to|under|from)[ \t]+(?:the[ \t]+)?" + _RUN)
RUN_AFTER_PREP = re.compile(r"^[ \t]+(?:the[ \t]+)?" + _RUN)   # the keyword already ends in "by"
ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}
CLAIM_VERBS = frozenset("""certified certification certifications certificate accredited accreditation approved
approval registered registration partner partners partnered award awards winning rated""".split())
ID_STOP = F.STOP | GENERIC | frozenset("every each fully proudly officially other why how meet ask try book also "
                                      "the a an as".split()) | frozenset(
    # a pronoun that starts the sentence ("They're UKCA and CE marked") names no body
    "they he she it we i you these those both he's she's they're we're it's".split())
# "your team cloud-certified", "your whole staff certified": a claim word right after the reader's own
# noun (not "your certified installer", where it describes the business's people, and not "your
# charger is certified", a claim about what the business sells)
READER_OWNS = re.compile(r"(?i:\byour\s+(?:(?!(?:is|are|was|were|be|been|being|will|by|of|with|from|for|to|and|or|the|a|"
                         r"an|our|its|their|get|gets|got)\b)[a-z]+\s+){1,3}(?:[a-z]+-)?$)")
IDENTITY_CLASSES = {"cert", "cert_named", "security", "health", "credential"}
IDENTITY_TYPES, IDENTITY_CLASSES_F = {"certification", "credential"}, {"safety_cert", "security", "regulated_health"}
# named tiers of a partner / accreditation scheme ("Gold Partner", "Platinum accreditation")
TIER_WORDS = frozenset("bronze silver gold platinum diamond elite premier".split())
# conformity marks: "UKCA-marked", "CE marked", "carry the UKCA and CE marks"
MARK_WORDS = frozenset("mark marks marked marking".split())
SEG_PRICE = r"(?:price\s+list|prices?|pricing|rates?|discounts?|terms|tariffs?)\b"


def _norm_phrase(s: str) -> str:
    return " ".join(re.sub(r"[-‐–—]", " ", s.lower()).split())


def phrase_in(phrase: str, text: str) -> bool:
    p = _norm_phrase(phrase)
    if not p:
        return False
    return re.search(r"(?<![\w])" + re.escape(p) + r"(?![\w])", _norm_phrase(text)) is not None


def _tokens(text: str) -> set[str]:
    return {str(NUMBER_WORDS.get(w, w)) for w in F.words(text) if w not in {"a", "an", "the", "and", "of", "to", "is", "are"}}


def _blob(f: dict) -> str:
    parts = [f.get("text"), f.get("value_text"), f.get("attribute"), F.subject_ref(f),
             *F.phrases(f.get("allowed_phrasing")), *[str(d) for d in f.get("required_disclosures") or []]]
    return " ".join(str(p) for p in parts if p)


def _overlap(sentence: str, f: dict, exclude: set[str] = frozenset()) -> int:
    s = F.content_words(sentence) | {w for w in F.words(sentence) if w.isdigit() and len(w) >= 2}
    b = F.content_words(_blob(f)) | {w for w in F.words(_blob(f)) if w.isdigit() and len(w) >= 2}
    return len((s & b) - exclude - GENERIC)


def _subject_named(f: dict, sentence: str) -> bool:
    ref = F.subject_ref(f)
    if not ref:
        return False
    if phrase_in(ref, sentence):
        return True
    ref_words = F.content_words(ref) - GENERIC
    return bool(ref_words & F.content_words(sentence))


def id_tokens(text: str) -> set[str]:
    """Identifier tokens: "ISO 9001:2015" -> iso9001, "SOC 2 Type II" -> soc2 type2, "UL Listed" -> ul listed.
    Claim verbs and everyday words are dropped, so what is left names WHICH certificate / body."""
    t = re.sub(r"'\w*", "", text or "")
    t = re.sub(r"\b(type|tier|level|class|grade)\s+(iv|i{1,3}|v|\d)\b",
               lambda m: m.group(1) + ROMAN.get(m.group(2).lower(), m.group(2)), t, flags=re.I)
    t = re.sub(r"\b([A-Z]{2,6})[\s-]?(\d{1,5})(?::\d{2,4})?\b", r"\1\2", t)
    return {w for w in re.findall(r"[a-z0-9&+]+", t.lower())
            if (len(w) >= 2 or w.isdigit()) and w not in ID_STOP and w not in CLAIM_VERBS and w not in MARK_WORDS}


def claim_ids(sentence: str, start: int, end: int) -> set[str]:
    """The named body / standard of the claim at [start, end): the keyword itself ("ISO 14001",
    "NHS approved") plus the run of capitalised or numbered words right before it ("HubSpot Gold
    Solutions Partner", "HCPC registered") or after by/with/to ("approved by the NHS")."""
    before = RUN_BEFORE.search(sentence[:start])
    after = (RUN_AFTER_PREP if re.search(r"\b(?:by|with|to)$", sentence[start:end], re.I) else RUN_AFTER).match(sentence[end:])
    window = " ".join(x for x in ((before.group(1) if before else ""), sentence[start:end],
                                  (after.group(1) if after else "")) if x)
    # only capitalised / numbered words name something ("uptime", "certified" name nothing)
    return id_tokens(" ".join(re.findall(r"(?<![\w'])" + _ID_TOK + r"(?:[ \t]+(?:(?:I{1,3}|IV|V)\b|\d[\w:.]*))?", window)))


def fact_ids(f: dict) -> set[str]:
    parts = [f.get("value_text"), F.subject_ref(f), str(f.get("attribute") or "").replace("_", " "),
             *F.phrases(f.get("allowed_phrasing"))]
    if isinstance(f.get("value"), str):
        parts.append(f["value"])
    # the body behind an identifier the fact states ("LSQB-2417" -> "LSQB accreditation")
    pre = {w for p, _ in fact_identifiers(f) if p != "#" for w in p.lower().split("-") if len(w) >= 2}
    return id_tokens(" ".join(p for p in parts if p)) | (pre - ID_STOP)


# ---------- word profiles (non-numeric facts: inclusions, features, delivery options, allergens...)

UNIT_WORDS = frozenset(set(UNITS) | set(UNITS.values()) | {"working", "business", "per", "each", "every"})
WEAK = frozenset(GENERIC | {"free", "other", "include", "included", "includ", "including", "available", "offer",
                            "plan", "service", "product", "business", "every", "all", "any", "only", "day", "days", "month", "year", "week", "time"})
NEGATOR = frozenset("no not without excluding excludes never non".split())


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _seq(text: str) -> list[str]:
    """Stemmed words in order, stopwords out, numbers kept (a number never counts as distinctive)."""
    return [str(NUMBER_WORDS[w]) if w in NUMBER_WORDS and len(w) > 2 else _stem(w)
            for w in F.words(text) if w not in F.STOP and w not in ("a", "an", "the", "and", "of")]


def _pairs(seq: list[str]) -> set[tuple[str, str]]:
    return {(a, b) for a, b in zip(seq, seq[1:])}


@dataclass
class Profile:
    subj: set          # distinctive words of the subject ref
    attr_name: set     # distinctive words of the attribute name that the value text also uses ("breakfast")
    attr: set          # distinctive words of the value / attribute (not the subject's)
    pairs: set         # word pairs of the value text with at least one distinctive word
    terms: set         # every word of value text + ref + allowed phrasing (for "an in-scope fact says this too")
    all_pairs: set     # every pair of value text / ref / allowed phrasing


def profiles(all_facts: list[dict]) -> dict[str, Profile]:
    facts = [f for f in all_facts if f.get("status") != "draft"]
    raw = {}
    df: dict[str, int] = {}
    for f in facts:
        vt, ref = f.get("value_text") or (f["value"] if isinstance(f.get("value"), str) else ""), F.subject_ref(f)
        attr = str(f.get("attribute") or "").replace("_", " ")
        vseq, rseq, aseq = _seq(vt), _seq(ref), _seq(attr)
        allowed = [_seq(p) for p in F.phrases(f.get("allowed_phrasing"))]
        raw[f["key"]] = (vseq, rseq, aseq, allowed)
        for w in set(vseq) | set(rseq) | set(aseq):
            df[w] = df.get(w, 0) + 1
    n = len(facts)
    common = {w for w, c in df.items() if n >= 4 and c > n / 2}

    def distinct(w: str) -> bool:
        return (len(w) >= 3 and not any(ch.isdigit() for ch in w) and w not in UNIT_WORDS and w not in WEAK
                and w not in common and w not in NUMBER_WORDS)

    out = {}
    for key, (vseq, rseq, aseq, allowed) in raw.items():
        subj = {w for w in rseq if distinct(w)}
        attr = {w for w in vseq + aseq if distinct(w) and w not in CLAIM_VERBS} - subj
        pairs = {p for p in _pairs(vseq) if distinct(p[0]) or distinct(p[1])}
        all_pairs = _pairs(vseq) | _pairs(rseq) | set().union(*(_pairs(a) for a in allowed))
        terms = set(vseq) | set(rseq) | set(aseq) | set().union(*(set(a) for a in allowed))
        attr_name = {w for w in aseq if distinct(w)}
        attr_name = attr_name if attr_name and attr_name <= set(vseq) else set()
        out[key] = Profile(subj, attr_name, attr, pairs, terms, all_pairs)
    return out


def _subject_hit(f: dict, p: Profile, sentence: str, s_set: set) -> bool:
    ref = F.subject_ref(f)
    if ref and len(ref) >= 4 and phrase_in(ref, sentence):
        return True
    if not p.subj:
        return False
    return p.subj <= s_set if len(p.subj) <= 2 else len(p.subj & s_set) >= 2


# ---------- offers and benefits described without their name ("your first lesson is on us")

# "free X", "complimentary X", "20% off X", "half-price X": the benefit's object comes after it
_FREE_NOT_OFFER = r"(?:spaces?|places?|slots?|spots?|time|rein|hand|will|range|flowing|speech|trade|agent|kick)"
# "units free right now", "two rooms free at the moment": free meaning available (not a price of nothing)
_FREE_AVAILABLE = r"right\s+now|just\s+now|at\s+(?:the\s+)?(?:moment|minute|present)|currently|immediately|straight\s+away"
BENEFIT_AFTER = re.compile(r"(?i:(?<![\w-])(?<!feel\s)free(?![\w-])(?!\s+(?:to|of|from|and|or|for|when|if|with|as|at|now|today|this|"
                           r"until|next|" + _FREE_NOT_OFFER + r"|" + _FREE_AVAILABLE + r")\b)|"
                           r"\bcomplimentary\b|(?<![\w.])\d+(?:\.\d+)?\s?%\s+off\b|\bhalf[- ]price\b)")
# "X is free", "X on us", "X at no extra charge", "X free of charge", "X for free", "X waived"
BENEFIT_BEFORE = re.compile(r"(?i:\b(?:is|are|'s|’s|'re|be|comes?|goes?)\s+(?:completely\s+|totally\s+|absolutely\s+|also\s+)?free\b|"
                            r"\bon\s+us\b|\bat\s+no\s+(?:extra\s+|additional\s+)?(?:charge|cost)\b|\bfree\s+of\s+charge\b|"
                            r"\bfor\s+free\b|\bwaived\b|"
                            # round 12: "X on the house", "X for nothing", "X won't cost you a penny (more)",
                            # "with thirty boxes thrown in" (not "thanks for nothing": no object before it)
                            r"\bon\s+the\s+house\b|\bfor\s+nothing\b|(?<!\bnot\s)\bthrown\s+in\b|"
                            r"\b(?:won['’]t|will\s+not|doesn['’]t|does\s+not|don['’]t|do\s+not)\s+cost\s+(?:you\s+)?"
                            r"(?:a\s+(?:penny|penny\s+more|thing|single\s+penny)|anything(?:\s+(?:extra|more))?|a\s+bean)\b|"
                            r"(?<![\w-])(?<!feel\s)free(?=\s*(?:$|[.!?,;)]|(?:this|today|now|when|if|until|with|next)\b)))")
ON_US_IDIOM = frozenset("count counting rely relying depend depending call calling lean bet trust focus wait check spy "
                        "turn hang come keep eye".split())
_WIN_STOP = frozenset("for with when if on in to at by until till over every per and or from this that while "
                      "as so but once after before within across throughout via worth".split())
TIME_WORDS = frozenset("hour hours hr hrs minute minutes min mins day days week weeks wk wks month months year years "
                       "yr yrs working business".split())
OBJ_SKIP = frozenset(F.STOP | GENERIC | {"a", "an", "the", "my", "their", "his", "her", "its", "first", "try"})


def _obj_words(words: list[str]) -> list[str]:
    return [_stem(w) for w in words if len(w) >= 3 and not w.isdigit() and not any(c.isdigit() for c in w)
            and w not in OBJ_SKIP and w not in TIME_WORDS and w not in NEGATOR]


def _window_after(text: str, pos: int, n: int = 4) -> list[str]:
    seg = re.split(r"[.,;:!?()\n]", text[pos:], maxsplit=1)[0]
    out = []
    for w in F.words(seg):
        if w in _WIN_STOP:
            break
        out.append(w)
        if len(out) >= n:
            break
    return _obj_words(out)


def _window_before(text: str, pos: int, n: int = 5) -> list[str]:
    parts = re.split(r"[.,;:!?()\n]", text[:pos])
    seg = parts[-1]
    if not F.words(seg) and len(parts) > 1:
        seg = parts[-2]           # "Blueberry facial glow-up, free with every groom": the thing before the comma
    return _obj_words(F.words(seg)[-n:])


# "we'll waive the ceremony room fee": the benefit's object comes after it (never a no_source by itself)
WAIVE_AFTER = re.compile(r"(?i:\bwaiv(?:e|es|ing)\b)")


def _conj_after(text: str, pos: int, n: int = 4) -> list[str]:
    """The words of a second object joined to the first by "and" / "or" / "&": "free tweaks and
    adjustments" -> adjustments. Only used to find a fact that offers one of them."""
    seg = re.split(r"[.,;:!?()\n]", text[pos:], maxsplit=1)[0]
    ws = F.words(seg)
    for i, w in enumerate(ws[:n + 1]):
        if w in ("and", "or"):
            rest = []
            for x in ws[i + 1:i + 1 + n]:
                if x in _WIN_STOP:
                    break
                rest.append(x)
            return _obj_words(rest)
        if w in _WIN_STOP:
            break
    m = re.match(r"\s*(?:[\w'’-]+\s+){0,%d}?&\s+" % n, seg)
    return _obj_words(F.words(seg[m.end():])[:n]) if m else []


def benefit_objects(text: str) -> list[tuple[str, list[str]]]:
    """[(form, object words)] for each benefit in the text: form "after" for "free X", "before"
    for "X is on us". Idioms ("count on us", "feel free", "free to ask") carry no object."""
    return [(form, w) for form, w, _ in benefit_objects_full(text)]


def benefit_objects_full(text: str) -> list[tuple[str, list[str], list[str]]]:
    """benefit_objects with the words of a second object joined by "and" / "or" (form "after")."""
    out = []
    for m in BENEFIT_AFTER.finditer(text or ""):
        if _NEG_BEFORE.search(text[max(0, m.start() - 20):m.start()]):
            continue
        out.append(("after", _window_after(text, m.end()), _conj_after(text, m.end())))
    for m in WAIVE_AFTER.finditer(text or ""):
        if _NEG_BEFORE.search(text[max(0, m.start() - 20):m.start()]) or re.search(
                r"(?i)\b(?:not|never|n't|won't|can't|cannot)\s+(?:\w+\s+)?$", text[max(0, m.start() - 20):m.start()]):
            continue
        out.append(("waive", _window_after(text, m.end()), []))
    for m in BENEFIT_BEFORE.finditer(text or ""):
        prev = F.words(text[:m.start()])[-1:]
        if m.group(0).lower().endswith("us") and prev and prev[0] in ON_US_IDIOM:
            continue
        if _NEG_BEFORE.search(text[max(0, m.start() - 20):m.start()]):
            continue
        out.append(("before", _window_before(text, m.start()), []))
    return [(form, w, c) for form, w, c in out if w]


def _lemma(w: str) -> str:
    """A verb and its noun as one word for matching a benefit's object: deliver / delivery / delivered /
    delivering -> deliver (words of five letters or more only)."""
    if len(w) >= 6:
        for suf in ("ing", "ies", "ed", "y"):
            if w.endswith(suf) and len(w) - len(suf) >= 4:
                return w[:-len(suf)]
    return w


def offer_heads(f: dict) -> set[str]:
    """The object a benefit fact gives: "a free 30-minute trial lesson" -> lesson, "Saturday delivery at
    no extra charge" -> delivery, "20% off the Glow Facial" -> facial (the last word of the phrase)."""
    vt = f.get("value_text") or (f["value"] if isinstance(f.get("value"), str) else "")
    heads = {w[-1] for form, w in benefit_objects(vt) if form != "waive"}
    zero = _zero_price_ref(f)
    return heads | ({zero[-1]} if not heads and zero else set())


def _zero_price_ref(f: dict) -> list[str]:
    """The object words of a price fact whose price is nothing ("NHS sight test", value 0, "free"):
    what it gives free is its subject."""
    val = f.get("value")
    if f.get("fact_type") != "price" or isinstance(val, bool) or not isinstance(val, (int, float)) or val != 0:
        return []
    if F.subject_kind(f) in ("business", "offer"):
        return []
    return _obj_words(F.words(F.subject_ref(f)))


def offer_objects(f: dict) -> set[str]:
    """Every object word of what a benefit fact gives: "free ceremony room hire" -> ceremony, room, hire."""
    vt = f.get("value_text") or (f["value"] if isinstance(f.get("value"), str) else "")
    out = {x for form, w in benefit_objects(vt) if form != "waive" for x in w}
    return out or set(_zero_price_ref(f))


# ---------- "N + 1 free" deals said in words: "buy ten, drive eleven", "one free hour when you book ten"

_CARD = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_ORD = {w: i + 1 for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth eleventh "
                                        "twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth "
                                        "nineteenth twentieth".split())}
DEAL_BUY = frozenset("buy book books booking booked pay purchase order block".split())
DEAL_SKIP = frozenset("a an and pay for of up to in advance".split())
DEAL_FREE = frozenset("free bonus extra".split())
DEAL_GAIN = frozenset("get gets drive take have enjoy receive".split())


def _deal_num(t: str) -> int | None:
    m = re.fullmatch(r"(\d{1,3})(?:st|nd|rd|th)?", t)
    if m:
        return int(m.group(1))
    return _CARD.get(t, _ORD.get(t))


def deal_of(text: str) -> tuple[int, str | None] | None:
    """(N, thing) when the text offers N + 1 for the price of N: "book 10 hours, get the 11th free",
    "buy ten, drive eleven", "one free hour when you block-book ten". None otherwise. The thing is the
    noun after N (or after "free"), so "book 10 lessons, get a free theory pack" is no such deal."""
    toks = re.findall(r"[a-z]+|\d+(?:st|nd|rd|th)?|[,;:]", (text or "").lower().replace("’", "'"))
    words = [t for t in toks if t not in ",;:"]

    def noun(k: int) -> str | None:
        t = toks[k] if k < len(toks) else ""
        return _stem(t) if t.isalpha() and t not in F.STOP and t not in DEAL_SKIP and t not in (
            "when", "if", "with", "on", "every", "today", "now", "free", "block", "get", "and") else None
    gain = bool(re.search(r"\bfree\b|\bon us\b|\bprice of\b", " ".join(words)))
    for i, t in enumerate(toks):
        if t not in DEAL_BUY:
            continue
        j = i + 1
        while j < len(toks) and j <= i + 3 and toks[j] in DEAL_SKIP | DEAL_BUY:
            j += 1
        n = _deal_num(toks[j]) if j < len(toks) else None
        if not n or n < 2:
            continue
        if j + 1 < len(toks) and toks[j + 1] in ("to", "or", "and"):
            continue                                      # "2 to 3 weeks": a range
        unit = noun(j + 1)
        for k, x in enumerate(toks):
            if k == j or _deal_num(x) != n + 1:
                continue
            said_as = (gain or x in _ORD or re.fullmatch(r"\d+(?:st|nd|rd|th)", x)
                       or set(toks[max(0, k - 3):k]) & DEAL_GAIN
                       or (k == j + 3 and toks[j + 1] in ",;:"))          # "pay for five, ride six"
            if said_as:
                return n, unit or noun(k + 1)
        # "one free hour", "get one free", "a bonus lesson": one more of the same thing
        for k, x in enumerate(toks):
            if x in DEAL_FREE and k > 0 and toks[k - 1] in ("one", "1", "a", "an"):
                fu = noun(k + 1)
                if unit and fu and unit != fu:
                    continue                              # "a free theory pack": something else
                return n, unit or fu
            if x in ("one", "1") and toks[k + 1:k + 3] in (["on", "us"], ["free"]):
                return n, unit
    return _deal_said_otherwise(text)


_ORD_ALT = "|".join(w for w in _ORD if _ORD[w] >= 2) + r"|\d{1,2}(?:nd|rd|th)"
_CARD_ALT = "|".join(w for w in _CARD if 1 <= _CARD[w] <= 12) + r"|\d{1,2}"
# "your third month is on us", "take a third one on us": the (N+1)th free, with no "buy N" said
ORD_GAIN = re.compile(r"(?i:\b(?:the|a|an|your|every|each)\s+(?P<o>" + _ORD_ALT + r")\s+(?P<mid>(?:[a-z'’-]+\s+){0,2}?)"
                      r"(?:(?:of|at)\s+(?:equal|same|similar|lower|lesser|up\s+to)\b(?:\s+[a-z'’-]+){0,4}?\s+)?"
                      r"(?:is\s+|are\s+|goes\s+|comes\s+)?(?:on\s+us|free|on\s+the\s+house)\b)")
# "three months for the price of two", "your first three months cost the price of two"
PRICE_OF = re.compile(r"(?i:\b(?P<k>" + _CARD_ALT + r")\s+(?:(?P<noun>[a-z'’-]+)\s+(?:of\s+[a-z'’-]+\s+|[a-z'’-]+\s+)?)?(?:for|cost|costs|at)\s+"
                      r"(?:just\s+|only\s+)?(?:the\s+)?price\s+of\s+(?P<n>" + _CARD_ALT + r")\b)")


# "2-for-1", "two for one", "buy one get one free", "BOGOF": the 1 + 1 deal (the thing after it, if any)
TWO_FOR_ONE = re.compile(r"(?i:(?<![\w£$€.-])(?:two|2)[\s-]+for[\s-]+(?:one|1)(?![\w.%-])(?:\s+(?:on\s+)?(?P<noun>[a-z'’-]+))?|"
                         r"\bbuy\s+one,?\s+get\s+one(?:\s+free)?\b|\bbogof\b)")


def _deal_said_otherwise(text: str) -> tuple[int, str | None] | None:
    t = (text or "").replace("’", "'")
    m = TWO_FOR_ONE.search(t)
    if m and not ((m.group("noun") or "").lower() in UNITS or (m.group("noun") or "").lower() in TIME_WORDS):
        w = (m.group("noun") or "").lower() if "noun" in m.groupdict() else ""
        return 1, (_stem(w) if w and w not in F.STOP and w not in DEAL_SKIP and w not in ("one", "ones", "deal", "offer")
                   else None)
    m = PRICE_OF.search(t)
    if m:
        k, n = _deal_num(m.group("k").lower()), _deal_num(m.group("n").lower())
        if k and n and k == n + 1:
            w = (m.group("noun") or "").lower()
            return n, (_stem(w) if w and w not in F.STOP and w not in ("one", "ones") else None)
    m = ORD_GAIN.search(t)
    if m:
        k = _deal_num(m.group("o").lower())
        if k and k >= 2:
            words = [w for w in m.group("mid").lower().split() if w not in F.STOP and w not in ("one", "ones", "is", "are")]
            return k - 1, (_stem(words[0]) if words else None)
    return None


def _deal_same(a, b) -> bool:
    return bool(a and b) and a[0] == b[0] and (a[1] is None or b[1] is None or a[1] == b[1])


# ---------- identifier numbers: certificate / licence / registration / member numbers

# "ABC-12345", "QA 20417", "WCL-7781", "FM30922", "VM-G-20417": capitals (up to three hyphenated
# groups) then 3+ digits. "company number 01234567",
# "licence no. 44821": a number right after an identifier word. Same shape (prefix + digit count)
# with other digits is another identifier (a typo or a transposition is still wrong).
ID_RX = re.compile(r"(?<![\w/-])(?P<p>[A-Z]{1,5}(?:[-/][A-Z]{1,5}){0,2})[-\s/]?(?P<n>\d{3,10})(?![\w/-]|[.,]\d)")
# "SRA number 804517", "the SRA, ID 8045/71", "ADI number is 417-358": a body's acronym, then
# no. / number / ID and the digits (any separators); the digits are compared as one number
ACR_NUM = re.compile(r"(?<![\w-])(?P<p>[A-Z]{2,6})(?:[,:]?[ \t]+(?:[a-z]+[ \t]+)?)(?i:no\.?|num(?:ber)?\.?|id|ref(?:erence)?\.?|#)"
                     r"[ \t]*(?:(?i:is)[ \t]+|:[ \t]*|#[ \t]*)?(?P<n>\d{2,8}(?:[-/. ]\d{1,8}){0,3})(?![\w/-]|[.,]\d)")
ID_CTX = re.compile(r"(?i:\b(?:certificate|cert|licen[cs]e|registration|registered|reg|member(?:ship)?|company|charity|"
                    r"vat|firm|practice|accreditation|permit)\s+(?:(?:no|num|number|ref|reference|id)\.?\s*)?[:#]?\s*)"
                    r"(?P<n>\d{4,12}|\d{1,6}(?:[·/]\d{1,6}){1,4})(?![\w-]|[.,]\d)")
# "company ID 518-204-7713", "CQC location ID 1-4471029358", "member workshop number 2219": an
# identifier word, at most one more word, then no. / number / ID and digits (dash-segmented or not)
ID_CTX_SEG = re.compile(r"(?i:\b(?:certificate|licen[cs]e|registration|member(?:ship)?|company|charity|firm|practice|"
                        r"accreditation|permit|location|partner|provider|workshop|scheme|establishment|report)\s+"
                        r"(?:[a-z]+\s+)?(?:no\.?|num\.?|number|id|ref\.?|reference)\s*[:#]?\s*)"
                        r"(?P<n>\d{1,6}(?:[-/·]\d{1,10}){0,4})(?![\w-]|[.,]\d)")
# "licence G/77-92", "certificate AB 12-345": an identifier word, then capitals and digits written in
# groups; compared as prefix + digits ("G7729")
ID_CTX_PFX = re.compile(r"(?i:\b(?:certificate|licen[cs]e|registration|member(?:ship)?|permit|accreditation)\s+"
                        r"(?:(?:no|num|number|id)\.?\s*)?[:#]?\s*)(?P<p>[A-Z]{1,3})[-/ ]?"
                        r"(?P<n>\d{1,6}(?:[-/·]\d{1,6}){1,4})(?![\w-]|[.,]\d)")
# "CGQB/3/55210", "NESR:207.713", "FHRS 11.88.24", "number 01/34871": digits written in two or more
# groups with any separator, after an acronym or (without one) shortly after an identifier word;
# compared as prefix + all digits. Dates, times, prices and phone numbers are not identifiers.
SEG_ID = re.compile(r"(?<![\w/.:£$€%-])(?:(?P<p>[A-Z]{2,6})(?:[-/:]|[ \t])?)?(?P<n>\d{1,6}(?:[-/.:·]\d{1,8}){1,4})"
                    r"(?![\w/-]|[.,:]\d)")
SEG_ID_CTX = re.compile(r"(?i:\b(?:number|no\.?|num|registration|registered|reg|certificate|cert|reference|ref|"
                        r"licen[cs]e|licensed|id|permit|membership)\b[^.\n]{0,25}$)")
SEG_NOT_CTX = re.compile(r"(?i:\b(?:tel|phone|call|mobile|ring|text|whatsapp|fax|dated?|on|valid|expires?|from|until)\b"
                         r"[^.\n]{0,12}$)")
NOT_ID_PREFIX = frozenset("GBP USD EUR ETB AUD CAD NZD CHF JPY UK US EU AM PM ID NO REF NUM".split())
ID_TYPES = {"certification", "credential"}
ID_CLASSES = {"safety_cert", "security", "regulated_health", "regulated_food"}


@dataclass
class Ident:
    prefix: str
    num: str
    start: int
    end: int
    text: str
    ctx: str = ""        # the identifier word before a bare number ("establishment", "licence")

    @property
    def shape(self):
        return (self.prefix, len(self.num))


def _date_like(a: int, b: int, c: int) -> bool:
    """d/m/y, m/d/y or y-m-d with a real day and month."""
    if a > 31:
        return 1 <= b <= 12 and 1 <= c <= 31
    return c >= 0 and ((1 <= b <= 12 and 1 <= a <= 31) or (1 <= a <= 12 and 1 <= b <= 31))


def identifiers(text: str, ctx_only: bool = False) -> list[Ident]:
    out: list[Ident] = []
    text = re.sub(r"[‐‑–—]", "-", text or "")        # "MPS–FR–2219": dashes of any width (same length)
    for m in ID_CTX_PFX.finditer(text):
        if m.group("p") in NOT_ID_PREFIX:
            continue
        out.append(Ident(m.group("p"), re.sub(r"\D", "", m.group("n")), m.start("p"), m.end("n"), m.group(0)))
    if not ctx_only:
        for m in ID_RX.finditer(text or ""):
            if m.group("p") in NOT_ID_PREFIX or any(i.start <= m.start() < i.end for i in out):
                continue
            out.append(Ident(m.group("p").replace("/", "-"), m.group("n"), m.start(), m.end(), m.group(0)))
    for m in ACR_NUM.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group("n"))
        if m.group("p") in NOT_ID_PREFIX or not 4 <= len(digits) <= 12 or any(
                i.start <= m.start("n") < i.end or m.start("n") <= i.start < m.end("n") for i in out):
            continue
        out.append(Ident(m.group("p"), digits, m.start("n"), m.end("n"), m.group(0)))
    for m in ID_CTX.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group("n"))
        if len(digits) < 4 or any(i.start <= m.start("n") < i.end for i in out):
            continue
        out.append(Ident("#", digits, m.start("n"), m.end(), m.group("n"), m.group(0).split()[0].lower()))
    for m in ID_CTX_SEG.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group("n"))
        if len(digits) < 4 or any(i.start <= m.start("n") < i.end or m.start("n") <= i.start < m.end("n") for i in out):
            continue
        out.append(Ident("#", digits, m.start("n"), m.end("n"), m.group("n"), m.group(0).split()[0].lower()))
    for m in SEG_ID.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group("n"))
        p = m.group("p")
        if len(digits) < 5 or any(i.start <= m.start("n") < i.end or m.start("n") <= i.start < m.end("n") for i in out):
            continue
        dm = re.fullmatch(r"(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})", m.group("n"))
        if dm and _date_like(*(int(x) for x in dm.groups())):
            continue
        if p and p in NOT_ID_PREFIX:
            continue
        if not p and (not SEG_ID_CTX.search(text[:m.start()]) or SEG_NOT_CTX.search(text[:m.start()])):
            continue
        out.append(Ident(p or "#", digits, m.start("n"), m.end("n"), m.group(0)))
    return out


def fact_identifiers(f: dict) -> set[tuple[str, str]]:
    """(prefix, digits) of the identifiers a fact states. Certification / credential facts (or a
    certification-like claim class): any capitals+digits identifier; other facts: only a number
    that follows an identifier word ("licence no. 44821"), so a model name is never one."""
    src = f.get("source") if isinstance(f.get("source"), dict) else {}
    text = " ".join(str(x) for x in (f.get("value_text"), f.get("value") if isinstance(f.get("value"), str) else None,
                                     f.get("text"), f.get("evidence_ref"), src.get("ref")) if x)
    idy = f.get("fact_type") in ID_TYPES or (f.get("claim_class") or "none") in ID_CLASSES
    return {(i.prefix, i.num) for i in identifiers(text, ctx_only=not idy)}


def _ident_findings(fs: "FactSets", ids: dict, s: str, veto: set, use) -> tuple[list[dict], list[tuple[int, int]]]:
    """An identifier in copy against the facts' identifiers: the same one matches; an expired /
    out-of-scope fact's conflicts; another number of the same shape as a valid fact's conflicts
    (and that fact may not "support" the claim word in this sentence)."""
    out, spans = [], []
    caps = set(re.findall(r"[A-Z]{2,6}", s))

    def fam(p: str, q: str) -> bool:
        """The same identifier family: equal prefixes, or one is the last group of the other and the
        sentence names the other groups ("FSC licence C-152107" for FSC-C151207)."""
        if p == q:
            return True
        long, short = (p, q) if len(p) > len(q) else (q, p)
        return long.endswith("-" + short) and all(g in caps for g in long.split("-")[:-1])

    def has(f, i, same_num=True):
        return any(fam(p, i.prefix) and (n == i.num if same_num else len(n) == len(i.num))
                   for p, n in ids.get(f["key"], ()))
    for i in identifiers(s):
        ok = [f for f in fs.ok if has(f, i)]
        if not ok and i.prefix == "#":       # "establishment ID 7310442" for "AA establishment ID 7310442"
            ok = [f for f in fs.ok if any(n == i.num for _, n in ids.get(f["key"], ()))]
        if ok:
            f = _best(ok, s)
            out.append(_finding(s, "match", f, _quote(f), f"{i.text} = {f['key']}"))
            spans.append((i.start, i.end))
            continue
        sc = [f for f in fs.scope if has(f, i)]
        st = [f for f in fs.stale if has(f, i)]
        if sc or st:
            f = _best(sc or st, s)
            veto.add(f["key"])
            out.append(_finding(s, "wrong_scope" if sc else "conflict_or_expired", f, _quote(f),
                                f"{i.text} is from a fact that is {_why(fs, f)}"))
            spans.append((i.start, i.end))
            continue
        same = [f for f in fs.ok if has(f, i, same_num=False)]
        if i.prefix == "#" and i.ctx:
            # "establishment ID 731-04-24" against "AA establishment ID 7310442": a valid fact with the
            # same identifier word and a number of the same length is the one it differs from
            by_ctx = [f for f in fs.ok if any(len(n) == len(i.num) for _, n in ids.get(f["key"], ()))
                      and re.search(r"(?i)\b" + re.escape(i.ctx) + r"\b", _blob(f))]
            same = by_ctx or same
        if same:
            f = _best(same, s)
            veto.add(f["key"])
            held = ", ".join(sorted(f"{p}-{n}" if p != "#" else n for p, n in ids[f["key"]]
                                    if fam(p, i.prefix) and len(n) == len(i.num)))
            out.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                f"{i.text} differs from the identifier in the fact: {held}"))
            spans.append((i.start, i.end))
    # "ISO 27001 ... certificate IS 781240": a fact with a conflicting identifier is not matched here
    out = [x for x in out if not (x["label"] == "match" and x["fact_key"] in veto)]
    for x in out:
        if x["label"] == "match":
            use(x["fact_key"], s)
    return out, spans


# ---------- a rating / grade number on a scheme a fact rates: "graded us 1 (Outstanding)" against
#            "inspection grade 2 (Good)", "5-star food hygiene rating" against "food hygiene rating 4"

SCHEME_RATING = re.compile(
    r"(?i:\bgraded\s+(?:(?:us|it|them|as|the\s+\w+)\s+)?(?P<n1>\d{1,2})(?![\w,/%]|\.\d)|"
    r"(?<![\w.])(?P<n2>\d{1,2}|one|two|three|four|five|six|seven)[- ]?stars?\s+(?P<w2>(?:[a-z'-]+\s+){1,3}?)(?:rating|grade|score)\b|"
    r"\b(?P<w3>(?:[a-z'-]+\s+){1,3}?)(?:rating|grade|score)\s+(?:of\s+)?(?P<n3>\d{1,2})(?![\w,/%]|\.\d))")
RATING_ATTR = frozenset({"grade", "graded", "rating", "rated", "score"})


def rating_facts(f: dict) -> bool:
    """A fact whose value is a whole-number rating / grade on some scheme."""
    val = f.get("value")
    if not isinstance(val, (int, float)) or isinstance(val, bool) or val != int(val):
        return False
    words = set(F.words(" ".join(str(x) for x in (f.get("attribute"), f.get("value_text")) if x).replace("_", " ")))
    return bool(words & RATING_ATTR)


def _scheme_rating_findings(fs: "FactSets", s: str, use) -> tuple[list[dict], list[tuple[int, int]]]:
    out, spans = [], []
    for m in SCHEME_RATING.finditer(s):
        n = m.group("n1") or m.group("n2") or m.group("n3")
        n = int(NUM_GRADE.get(n.lower(), n))
        scheme = {_stem(w) for w in F.content_words(m.group("w2") or m.group("w3") or "")} - RATING_ATTR - GENERIC

        def about(f):
            if not rating_facts(f):
                return False
            blob = {_stem(w) for w in F.words(_blob(f).replace("_", " "))}
            if scheme:
                return scheme <= blob          # the same named scheme ("food hygiene")
            return _overlap(s, f, RATING_ATTR | {"graded", "us"}) >= 1   # "our inspection graded us 1"
        for pool in (fs.ok, fs.scope, fs.stale):
            cands = [f for f in pool if about(f)]
            if not cands:
                continue
            f = _best(cands, s)
            held = int(f["value"])
            if held == n:
                if pool is fs.ok:
                    use(f["key"], s)
                    out.append(_finding(s, "match", f, _quote(f), f"{m.group(0).strip()} = {f['key']}"))
                else:
                    out.append(_finding(s, "wrong_scope" if pool is fs.scope else "conflict_or_expired", f, _quote(f),
                                        f"{m.group(0).strip()} is from a fact that is {_why(fs, f)}"))
            elif pool is fs.ok:
                out.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                    f"{m.group(0).strip()}: the fact says {held} ({_quote(f)})"))
            else:
                continue
            spans.append((m.start(), m.end()))
            break
    # "our five-star hotel" against "AA Four Star Hotel", "rated five stars by the AA" against it: a
    # whole star number said of the same thing (the noun after "star") or with the scheme's acronym
    for m in STAR_WORD.finditer(s):
        if _overlaps(spans, m.start(), m.end()):
            continue
        n = int(NUM_GRADE.get(m.group("n").lower(), m.group("n")))
        noun = _stem(m.group("noun").lower()) if m.group("noun") else None
        acr = set(re.findall(r"\b[A-Z]{2,6}\b", s)) - NOT_ID_PREFIX
        for f in fs.ok:
            if not rating_facts(f):
                continue
            t = " ".join(str(x) for x in (f.get("value_text"), f.get("text")) if x)
            held = [(int(NUM_GRADE.get(x.group("n").lower(), x.group("n"))),
                     _stem(x.group("noun").lower()) if x.group("noun") else None) for x in STAR_WORD.finditer(t)]
            same_thing = [h for h, fn in held if noun and fn == noun]
            by_acr = [h for h, _ in held if acr & set(re.findall(r"\b[A-Z]{2,6}\b", t))]
            nums = same_thing or by_acr
            if not nums:
                continue
            if n in nums:
                use(f["key"], s)
                out.append(_finding(s, "match", f, _quote(f), f"{m.group(0).strip()} = {f['key']}"))
            else:
                out.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                    f"{m.group(0).strip()}: the fact says {nums[0]} star ({_quote(f)})"))
            spans.append((m.start(), m.end()))
            break
    return out, spans


STAR_WORD = re.compile(r"(?i:(?<![\w.])(?P<n>[1-7]|one|two|three|four|five|six|seven)[- ]stars?\b(?!\s+(?:on|from)\b)"
                       r"(?:\s+(?P<noun>[a-z]{3,}))?)")


# ---------- a rating in words on a named scheme: "rated Excellent by the CQC" when the fact says
#            "rated Good by the CQC"; "Outstanding care at <this site>" when Outstanding is another site's

RATING_VALUE = re.compile(r"[A-Z][a-z]{2,}(?: [A-Za-z][a-z]{2,})?")
RATED_WORD = re.compile(r"\brated\s+(?:as\s+)?(?P<w>[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)|"
                        r"\brating\s*(?:of|:|is|was)?\s+(?P<w2>[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)")
RATING_WORD_TYPES = {"certification", "credential", "result"}
RATING_WORD_STOP = frozenset("the an our your their its latest last recent current new full official previous "
                             "first next this that".split())


def rating_schemes(f: dict) -> set[str]:
    """The named scheme of a fact whose value is a rating word ("Good", "Outstanding"): the
    acronyms its value text (else its sentence) uses, and their spelled-out forms in its sentence
    ("CQC", "Care Quality Commission")."""
    val = f.get("value")
    if not (f.get("fact_type") in RATING_WORD_TYPES and isinstance(val, str) and RATING_VALUE.fullmatch(val.strip())):
        return set()
    ref = F.subject_ref(f)
    acr = [a for a in re.findall(r"(?<![\w-])[A-Z]{2,6}(?![\w-])", f.get("value_text") or "") if a not in ref]
    if not acr:
        acr = [a for a in re.findall(r"(?<![\w-])[A-Z]{2,6}(?![\w-])", f.get("text") or "") if a not in ref]
    out = set(acr)
    # the body that gave the rating: "rated Good by Ofsted", "rated Excellent by the Care Inspectorate"
    for t in (f.get("value_text") or "", f.get("text") or ""):
        for m in re.finditer(r"\brated\s+" + re.escape(str(val).strip()) + r"\s+by\s+(?:the\s+)?"
                             r"(?P<b>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})", t):
            out.add(m.group("b"))
    for m in re.finditer(r"(?:[A-Z][a-z]+\s+){1,5}[A-Z][a-z]+", f.get("text") or ""):
        ws = m.group(0).split()
        for i in range(len(ws)):
            if "".join(w[0] for w in ws[i:]) in acr:
                out.add(" ".join(ws[i:]))
                break
    return out


def _scheme_in(alias: str, s: str) -> bool:
    if alias.isupper():
        return re.search(r"(?<![\w-])" + re.escape(alias) + r"(?![\w])", s) is not None
    return phrase_in(alias, s)


def _word_rating_findings(fs: "FactSets", schemes: dict, s: str, use) -> list[dict]:
    out = []
    live = [(f, pool) for pool in (fs.ok, fs.scope, fs.stale) for f in pool if schemes.get(f["key"])]
    if not live:
        return out
    said_at = [(m.start(), m.group("w") or m.group("w2")) for m in RATED_WORD.finditer(s)]
    # "An Excellent Ofsted rating": a rating word right before the scheme's name and rating / grade
    aliases = sorted({a for f, _ in live for a in schemes[f["key"]]}, key=len, reverse=True)
    for m in re.finditer(r"(?<![\w-])(?P<w>[A-Z][a-z]{2,})\s+(?:" + "|".join(map(re.escape, aliases)) +
                         r")\s+(?:rating|grade|inspection\s+rating|inspection\s+grade)\b", s):
        if m.group("w").lower() not in RATING_WORD_STOP and not any(a <= m.start() < a + 1 for a, _ in said_at):
            said_at.append((m.start(), m.group("w")))
    for at, said in said_at:
        if _NEG_BEFORE.search(s[max(0, at - 20):at]):
            continue
        same = [(f, pool) for f, pool in live if any(_scheme_in(a, s) for a in schemes[f["key"]])]
        if not same:
            continue
        words = [said, said.split()[0]]
        hit = [(f, pool) for f, pool in same if str(f["value"]).strip().lower() in (w.lower() for w in words)]
        if hit:
            for pool in (fs.ok, fs.scope, fs.stale):
                c = [f for f, p in hit if p is pool]
                if not c:
                    continue
                f = _best(c, s)
                if pool is fs.ok:
                    use(f["key"], s)
                    out.append(_finding(s, "match", f, _quote(f), f"rated {f['value']} = {f['key']}"))
                else:
                    out.append(_finding(s, "wrong_scope" if pool is fs.scope else "conflict_or_expired", f, _quote(f),
                                        f"rated {f['value']} is {_why(fs, f)}"))
                break
            continue
        ok = [f for f, p in same if p is fs.ok]
        if ok:
            f = _best(ok, s)
            scale = sorted({str(g["value"]) for g, _ in same})
            out.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                f"rated {words[0]}: the fact says {f['value']} (ratings on file: {', '.join(scale)})"))
    if out:
        return out
    # "Outstanding care at Alder Court": another site's rating word said with this site's name
    for f, pool in live:
        if pool is fs.ok or F.subject_kind(f) not in ("site", "business") or phrase_in(F.subject_ref(f), s):
            continue
        w = str(f["value"]).strip()
        m = re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", s)
        if not m or _NEG_BEFORE.search(s[max(0, m.start() - 20):m.start()]):
            continue
        mine = [g for g in fs.ok if schemes.get(g["key"]) & schemes[f["key"]] and g.get("attribute") == f.get("attribute")
                and str(g["value"]).strip().lower() != w.lower() and len(F.subject_ref(g)) >= 4
                and phrase_in(F.subject_ref(g), s)]
        if mine:
            out.append(_finding(s, "wrong_scope" if pool is fs.scope else "conflict_or_expired", f, _quote(f),
                                f'"{w}" said of {F.subject_ref(mine[0])} is the rating of {F.subject_ref(f)}, '
                                f"{_why(fs, f)}; {F.subject_ref(mine[0])} is rated {mine[0]['value']}"))
            break
    return out


# ---------- staff credentials: "our instructors are registered dietitians"

# round 12: a credential named by its body or school: "DBS-checked", "conservatoire-trained",
# "Montessori-trained" (not an adverb: "well-trained", "fully-trained", "highly-trained" are puffery)
CRED = (r"(?:board[- ]certified|state[- ]registered|registered|chartered|certified|licensed|accredited|qualified|"
        r"(?!(?:well|self|fully|highly|properly|specially|expertly|professionally|in|un|over|re)-)"
        r"[a-z]{2,}(?<!ly)-(?:trained|checked|vetted))")
_PROF = """physiotherapist physio therapist nutritionist dietitian dietician engineer electrician plumber surveyor
architect accountant solicitor lawyer barrister nurse vet veterinarian surgeon doctor gp dentist hygienist optician
optometrist pharmacist osteopath chiropractor podiatrist psychologist psychotherapist counsellor counselor coach
trainer instructor teacher tutor midwife paramedic technician mechanic expert specialist consultant adviser advisor
planner designer developer administrator professional practitioner chef beautician aesthetician hairdresser stylist
groomer inspector assessor auditor analyst installer fitter gasfitter carer caregiver
scientist clinician psychiatrist radiographer sonographer anaesthetist orthodontist audiologist
herbalist acupuncturist masseur masseuse sommelier pilot broker examiner horticulturist gardener arborist
landscaper translator interpreter""".split()
PROF_WORDS = frozenset(_PROF + [w + "s" for w in _PROF] + ["coaches", "midwives", "staff", "people"])
_MOD = r"(?:(?!(?:by|with|to|of|in|for|and|or|the|a|an|under|from|at|on|is|are)\b)[\w&'-]+\s+)"
CRED_RX = re.compile(r"(?i:\b" + CRED + r"\b)")
MOD_STOP = frozenset("by with to of in for and or the a an under from at on is are as who that which".split())
STAFF_B = re.compile(r"(?i:\b(?:our|every|each|all|in-house)\s+(?:(?:of\s+)?(?:our|the)\s+)?" + _MOD + r"{0,2}?"
                     r"(?P<prof>[a-z]+)\s+(?:(?:on|in|at)\s+(?:our|the)\s+\w+\s+)?(?:is|are)\s+(?:(?:all|also|fully|now|"
                     r"properly)\s+)*(?:an?\s+)?(?:[\w&-]+\s+){0,2}?(?P<cred>" + CRED + r"))\b")
STAFF_CONTEXT = frozenset("our every each all in-house inhouse team staff by are 're".split())
CONDITION_RX = re.compile(r"(?i:\b(?:when|whenever|if|once|provided|providing|unless|until|must|should|needs?|needed|"
                          r"requires?|required|only|can|could|may|might|recommended|any|ought|(?:has|have|had)\s+to)\b)")


def staff_claims(s: str) -> list[tuple[int, int, str, str, str]]:
    """[(start, end, text, credential word, profession)] for credential claims about the people who
    do the work. "registered office", "registered users", an "MCS certified installer" that the
    business itself is (no our / every / by / are before it) are not."""
    out = []
    for m in CRED_RX.finditer(s):
        prof, end = None, m.end()
        for w in list(re.finditer(r"[\w&'-]+", s[m.end():]))[:4]:
            lw = w.group(0).lower()
            if lw in MOD_STOP or re.search(r"[.,;:!?()]", s[m.end():m.end() + w.start()]):
                break
            if lw in PROF_WORDS:
                prof, end = lw, m.end() + w.end()
                break
        if prof is None:
            continue
        before = re.findall(r"[a-z'-]+", s[:m.start()].lower())[-8:]
        if not (set(before) & STAFF_CONTEXT or any(w.endswith("'re") for w in before)):
            continue
        if before[-1:] and before[-1] in ("a", "an", "any") and not (len(before) > 1 and before[-2] in ("is", "'s")):
            # "every return is prepared by a Chartered Tax Adviser": who does the work, a claim;
            # "covered when installed by a qualified engineer", "must be signed off by an accredited
            # engineer": a condition, not a claim about staff
            by_claim = (before[-1] in ("a", "an") and len(before) > 1 and before[-2] == "by"
                        and not CONDITION_RX.search(re.split(r"[.;:!?]", s[:m.start()])[-1]))
            if not by_claim:
                continue
        out.append((m.start(), end, s[m.start():end], m.group(0).lower(), prof))
    for m in STAFF_B.finditer(s):
        prof = m.group("prof").lower()
        if prof not in PROF_WORDS or any(a <= m.start("cred") < b for a, b, *_ in out):
            continue
        out.append((m.start(), m.end(), m.group(0), m.group("cred").lower(), prof))
    return out


# "our in-house entomologist", "an on-site GP": a claim that the business employs that professional
ROLE_RX = re.compile(r"(?i:\b(?:our|an?|the|their|its)\s+(?:own\s+)?(?:in[- ]?house|on[- ]?site)\s+(?P<prof>[a-z]+)\b)")


TEAM_RX = re.compile(r"(?i:\b(?:our|an?|the)\s+(?:own\s+)?(?:in[- ]?house|on[- ]?site)\s+(?P<w>[a-z]{3,})\s+"
                     r"(?:team|department|studio|crew|unit)\b)")
FACILITY_RX = re.compile(r"(?i:\b(?:our|an?|the|its)\s+(?:own\s+)?(?:in[- ]?house|on[- ]?site)\s+"
                         r"(?P<w>lab|labs|laboratory|workshop|kitchen|bakery|studio|factory|pharmacy|garage|surgery|"
                         r"clinic|salon|dispensary|warehouse)\b)")
DEDICATED_RX = re.compile(r"(?i:\b(?:a|an|your|one|their|its)\s+(?:own\s+)?(?:dedicated|personal)\s+(?:[a-z]+\s+)?"
                          r"(?P<w>co-?ordinator|manager|planner|advis[eo]r|consultant|contact|host|tutor|trainer|"
                          r"engineer|stylist|concierge|nurse|coach|chef|butler|chauffeur|assistant|shopper)\b)")
UNSOURCED_RX = re.compile(r"(?i:(?<![\w/])24\s?/\s?7(?![\w/])|\b(?:a)?round[- ]the[- ]clock\b|\b24\s+hours\s+a\s+day\b|"
                          r"\b(?:same|next)[- ]day\s+(?:install\w*|deliver\w*|dispatch\w*|service|repairs?|fitting|"
                          r"turnaround|collection|printing|appointments?|call[- ]?outs?)\b|"
                          r"\b\d{1,3}\+?\s+years?(?:['’]|\s+of)?\s+(?:experience|expertise)\b)")


def role_claims(s: str) -> list[tuple[int, int, str, str]]:
    out = []
    for m in ROLE_RX.finditer(s):
        prof = m.group("prof").lower()
        if prof in PROF_WORDS - {"staff", "people"} or re.fullmatch(r"[a-z]{3,}(?:ists?|icians?)", prof):
            out.append((m.start(), m.end(), m.group(0), prof))
    return out


# Who does the work, said of every job: "A qualified dental nurse answers every call", "Every file is
# handled personally by a partner", "each table is built start to finish by a single master
# craftsman", "every one of our instructors is a grade A ADI". A promise about the people behind every
# job needs a fact that names that role and is not about one named person only.
_SERVICE_PROF = sorted(set(_PROF) | {"partner", "craftsman", "craftsperson", "carpenter", "joiner", "cabinetmaker",
                                     "artisan", "maker", "solicitor", "director"}, key=len, reverse=True)
_SPROF = r"(?P<prof>" + "|".join(_SERVICE_PROF) + r")(?:s|es|men)?"
_SMOD = r"(?:[\w&'-]+[ \t]+){0,3}?"
_WHO = r"(?:a|an|one|our|the\s+same|a\s+single|one\s+single|the)"
_SVERB = (r"(?:answers|handles|checks|reviews|builds|makes|signs(?:\s+off)?|oversees|looks\s+after|takes|picks\s+up|"
          r"deals\s+with|manages|inspects|fits|installs|treats|sees|runs|handcrafts|crafts|prepares|writes|leads)")
SERVICE_RX = re.compile(
    r"(?i:\b" + _WHO + r"[ \t]+" + _SMOD + _SPROF + r"[ \t]+" + _SVERB + r"[ \t]+(?:(?:every|each|all)\b|(?:all\s+)?(?:of\s+)?your\b))|"
    r"(?i:\b(?:every|each|all)[ \t]+(?:[\w'-]+[ \t]+){0,3}?(?:is|are|gets?)[ \t]+(?:[\w-]+ly[ \t]+)?"
    r"(?:[a-z]+ed|built|made|done|seen|taken|written|signed|run|cut|sewn|drawn|taught)[ \t]+(?:[\w'-]+[ \t]+){0,4}?"
    r"by[ \t]+" + _WHO + r"[ \t]+" + _SMOD + _SPROF.replace("?P<prof>", "?P<prof2>") + r"\b)|"
    r"(?i:\b(?:every|each|all)[ \t]+(?:one[ \t]+)?of[ \t]+(?:our|the)[ \t]+" + _SPROF.replace("?P<prof>", "?P<prof3>") +
    r"[ \t]+(?:is|are)\b)")


def service_claims(s: str) -> list[tuple[int, int, str, str]]:
    return [(m.start(), m.end(), m.group(0), (m.group("prof") or m.group("prof2") or m.group("prof3")).lower())
            for m in SERVICE_RX.finditer(s)]


def _service_supports(f: dict, prof: str) -> bool:
    """A fact naming that role, about the business's people (a fact about one named person does not
    say every job is done by such a person, unless it says every / all)."""
    if f.get("fact_type") in ("price", "hours", "availability", "event", "contact") or not _role_supports(f, prof):
        return False
    return F.subject_kind(f) != "person" or bool(set(F.words(f.get("text") or "")) & UNIVERSAL)


def _role_supports(f: dict, prof: str) -> bool:
    return _stem(prof) in {_stem(w) for w in F.words(" ".join((_blob(f), f.get("text") or "")))}


UNIVERSAL = frozenset("every each all".split())


def _others_differ(f: dict, s: str, start: int, claim: str, cred: str, prof: str) -> str | None:
    """"Every technician is an X Master Technician" when the fact is about one person and says the
    OTHER technicians hold something else ("Our other technicians are X Accredited at Level 3"):
    the sentence of the fact about the others, else None."""
    if F.subject_kind(f) != "person":
        return None
    before = set(re.findall(r"[a-z]+", s[:start].lower())[-10:])
    if not before & UNIVERSAL:
        return None
    qual = {_stem(w) for w in F.words(claim)} - {_stem(w) for w in F.words(cred)} - {_stem(prof)} - F.STOP
    for sent in re.split(r"(?<=[.!?])\s+", f.get("text") or ""):
        ws = {_stem(w) for w in F.words(sent)}
        if not ({"other", "rest", "remaining", "others"} & ws and _stem(prof) in ws):
            continue
        if (qual and not qual <= ws) or ws & NEGATOR:
            return sent.strip()
    return None


def _cred_stem(w: str) -> str:
    parts = w.lower().replace("-", " ").split()
    if len(parts) > 1 and parts[-1] in ("trained", "checked", "vetted"):
        return parts[0][:6]           # "DBS-checked": the check's body is what a fact must name
    return parts[-1][:6]


def _staff_supports(f: dict, cred: str, prof: str) -> bool:
    """A fact that says these staff hold this credential: a person / credential fact (never a
    business-level certification or partner status) naming the profession and the credential."""
    if not (F.subject_kind(f) == "person" or f.get("fact_type") == "credential"):
        return False
    blob_words = [w for w in F.words(_blob(f))]
    stems = {_stem(w) for w in blob_words}
    if _stem(prof) not in stems and prof not in ("staff", "people"):
        return False
    return any(w.startswith(_cred_stem(cred)) for w in blob_words)


# ---------- allergen / ingredient lists

ALLERGEN_FACT = re.compile(r"(?i)\b(?:allergens?|ingredients?)\b")
LIST_CUE = re.compile(r"(?i)\b(?:contains?|containing|may\s+contain|traces?\s+of|allergens?|ingredients?|"
                      r"made\s+(?:with|from))\b")
LIST_CUE_NEG = re.compile(r"(?i)\b(?:not|no|never|doesn't|does\s+not|free[- ]from|without)\s+(?:\w+\s+)?$")
LIST_SKIP = frozenset("contain contains containing may trace traces made with from allergen allergens ingredient "
                      "ingredients also facility handle handles".split())


def allergen_items(f: dict) -> set[str]:
    attr = str(f.get("attribute") or "").replace("_", " ")
    if not (ALLERGEN_FACT.search(attr) or (ALLERGEN_FACT.search(f.get("text") or "")
                                           and LIST_CUE.search(f.get("value_text") or ""))):
        return set()
    vt = f.get("value_text") or (f["value"] if isinstance(f.get("value"), str) else "")
    return {_stem(w) for w in F.words(vt) if len(w) >= 3 and w not in F.STOP and w not in LIST_SKIP
            and w not in WEAK and not w.isdigit()}


def lists_allergens(s: str, items: set[str]) -> bool:
    """The sentence lists (after contains / ingredients / made with) the fact's items."""
    if not items:
        return False
    for m in LIST_CUE.finditer(s):
        if LIST_CUE_NEG.search(s[max(0, m.start() - 20):m.start()]):
            continue
        after = {_stem(w) for w in F.words(s[m.end():])}
        if len(after & items) >= min(2, len(items)):
            return True
    return False


# ---------- offer names mentioned in part ("the Green Fairways bursary" for "County Green Fairways junior bursary")

def name_part_named(ref: str, s: str, common: set) -> bool:
    """Two or more adjacent capitalised words of the sentence that are adjacent words of the
    name (at least one of them distinctive)."""
    rseq = _seq(ref)
    if len(rseq) < 2:
        return False
    rpairs = _pairs(rseq)
    toks = re.findall(r"[A-Za-z][\w'’&-]*", s)
    for a, b in zip(toks, toks[1:]):
        if not (a[0].isupper() and b[0].isupper()):
            continue
        pa, pb = _seq(a), _seq(b)
        if len(pa) != 1 or len(pb) != 1:
            continue
        pr = (pa[0], pb[0])
        if pr in rpairs and any(len(w) >= 3 and w not in WEAK and w not in common for w in pr):
            if re.search(re.escape(a) + r"\s+" + re.escape(b), s):
                return True
    return False


OFFER_NOUNS = frozenset("offer deal sale special discount promo promotion bundle voucher saving".split())


def offer_named_loosely(ref: str, s_seq: list[str], common: set, ok_terms: set) -> bool:
    """An offer named by its own words in another order or form ("our autumn mouse deal" for the
    "Autumn mouse-proofing offer"): two or more distinctive words of its name close together, at
    least one that no valid in-scope fact uses, and an offer word (deal, offer, sale...) right after."""
    name = {w for w in _seq(ref) if len(w) >= 3 and w not in WEAK and w not in common and w not in OFFER_NOUNS
            and w not in UNIT_WORDS and not any(c.isdigit() for c in w)}
    if len(name) < 2:
        return False
    pos = [i for i, w in enumerate(s_seq) if w in name]
    hit = {s_seq[i] for i in pos}
    if len(hit) < 2 or not hit - ok_terms:
        return False
    for i in pos:          # a run of name words (at most one other word between) ending in an offer noun
        run, j = {s_seq[i]}, i
        while j + 1 < len(s_seq) and (s_seq[j + 1] in name or (j + 2 < len(s_seq) and s_seq[j + 2] in name
                                                                  and s_seq[j + 1] not in OFFER_NOUNS)):
            j = j + 1 if s_seq[j + 1] in name else j + 2
            run.add(s_seq[j])
        if len(run) >= 2 and run - ok_terms and any(w in OFFER_NOUNS for w in s_seq[j + 1:j + 3]):
            return True
    return False


TIME_NAME_WORDS = frozenset("""spring summer autumn fall winter christmas xmas easter halloween bonfire valentine
valentines new year weekday weekend midweek monday tuesday wednesday thursday friday saturday sunday january
february march april june july august september october november december morning evening night day week
month season seasonal holiday holidays early late last first launch opening anniversary""".split())


def name_said_in_part(ref: str, s_seq: list[str], common: set, ok_terms: set, offer: bool) -> bool:
    """An expired offer / package named by part of its name, in any case: two adjacent words of the
    name ("Twilight spa" for "Summer Twilight Spa"), or for an offer one name word then an offer word
    ("sausage deal", "gutter cleaning deals" for "Sausage Stack", "Gutter Trio"). The name word must
    be the fact's own: four letters or more, no valid in-scope fact uses it anywhere, and not a
    season / time word ("your autumn Glow Facial" names the valid facial, not the autumn offer)."""
    rseq = _seq(ref)

    def own(w):
        return (len(w) >= 4 and w not in TIME_NAME_WORDS and w not in ok_terms and w not in common and w not in WEAK and w not in OFFER_NOUNS
                and w not in UNIT_WORDS and not any(c.isdigit() for c in w))
    rpairs = _pairs(rseq)
    for a, b in zip(s_seq, s_seq[1:]):
        if (a, b) in rpairs and (own(a) or own(b)):
            return True
    if offer:
        names = {w for w in rseq if own(w)}
        for i, w in enumerate(s_seq):
            if w in names and any(x in OFFER_NOUNS for x in s_seq[i + 1:i + 3]):
                return True
    return False


MEMBER_GRADE = re.compile(r"(?i:\b(?P<g>fellow|(?:full|chartered|honorary|associate|student|affiliate)\s+member|member|"
                          r"associate|affiliate|student)\s+of\s+(?:the\s+)?)(?P<body>[A-Z][A-Za-z]{1,7})\b")
_GRADE_NORM = {"full member": "member", "chartered member": "member", "honorary member": "honorary",
               "associate member": "associate", "student member": "student", "affiliate member": "affiliate"}
PERSON = re.compile(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}(?:-[A-Z][a-z]+)?\b")


def _member_grades(text: str) -> list[tuple[str, str]]:
    out = []
    for m in MEMBER_GRADE.finditer(text or ""):
        g = " ".join(m.group("g").lower().split())
        out.append((_GRADE_NORM.get(g, g), m.group("body")))
    return out


def _member_grade_findings(fs: "FactSets", s: str, use) -> list[dict]:
    said = _member_grades(s)
    if not said:
        return []
    people = set(PERSON.findall(s))
    out = []
    for g, body in said:
        for f in fs.ok:
            t = " ".join(str(x) for x in (f.get("value_text"), f.get("text")) if x)
            held = {hg for hg, hb in _member_grades(t) if hb == body}
            if not held or not (people & set(PERSON.findall(t))):
                continue
            if g in held:
                use(f["key"], s)
                out.append(_finding(s, "match", f, _quote(f), f"{g} of the {body} = {f['key']}"))
            else:
                out.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                    f"{g} of the {body}: the fact says {', '.join(sorted(held))} ({_quote(f)})"))
            break
    return out


INCL_BEFORE = re.compile(r"(?i:\b(?:includes?|included|including|comes?\s+with|with)\s+)")
INCL_CUE = re.compile(r"(?i:\b(?:includ\w*|comes?\s+with|free\s+with)\b)")


def inclusion_words(fs: "FactSets", ok_terms: set) -> dict[str, tuple[dict, str]]:
    """Stemmed word -> (fact, word) for what a stale / out-of-scope plan, package or variant says it
    includes ("... and includes payroll"), when no valid in-scope fact uses the word and no other fact
    includes it."""
    seen: dict[str, list] = {}
    for f in fs.stale + fs.scope:
        if F.subject_kind(f) not in ("plan", "package", "variant", "product", "service"):
            continue
        t = " ".join(str(x) for x in (f.get("text"), f.get("value_text")) if x)
        for m in INCL_BEFORE.finditer(t):
            words = re.split(r"[.,;:()]", t[m.end():])[0].split()[:3]
            for w in words:
                w0 = w.lower().strip("'’")
                st = _stem(w0)
                if (len(st) < 5 or st in ok_terms or w0 in F.STOP or w0 in GENERIC or st in WEAK or w0 in UNIT_WORDS
                        or not w0.isalpha() or w0 in NEGATOR):
                    continue
                seen.setdefault(st, []).append((f, w0))
    return {w: hits[0] for w, hits in seen.items() if len({f["key"] for f, _ in hits}) == 1}


def _foreign_inclusions(s: str, s_set: set, incl_words: dict, fs: "FactSets", ok_terms: set):
    out = []
    for st, (g, w) in incl_words.items():
        if st not in s_set or _negated_in(s, st):
            continue
        here = bool(INCL_CUE.search(s)) or any(
            F.subject_kind(h) == F.subject_kind(g) and len(F.subject_ref(h)) >= 4 and phrase_in(F.subject_ref(h), s)
            for h in fs.ok)
        if here:
            out.append((g, w))
    return out


def _neg_window(text: str) -> set[str]:
    """Stemmed words a text puts right after a negator ("no joining fee" -> joining, fee)."""
    ws = [_stem(w) for w in F.words(text)]
    out = set()
    for i, w in enumerate(ws):
        if w in NEGATOR:
            out |= set(ws[i + 1:i + 4])
    return out


# ---------- the check


@dataclass
class FactSets:
    ok: list          # usable: valid on the day, in scope, not restricted
    scope: list       # valid on the day but for another scope
    stale: list       # expired, superseded, retired, not yet valid
    rules: list       # carry forbidden phrasing (active or expired, any scope)
    reason: dict      # key -> reason (None for ok)


def sort_facts(all_facts: list[dict], day: date, scope: dict) -> FactSets:
    ok, sc, stale, rules, reason = [], [], [], [], {}
    for f in all_facts:
        status = f.get("status") or "active"
        if status == "draft":
            continue
        if status in ("active", "expired"):
            rules.append(f)
        if (f.get("sensitivity") or "public") == "restricted":
            reason[f["key"]] = "restricted"
            continue
        r = F.classify(f, day, scope)
        reason[f["key"]] = r
        if r is None:
            ok.append(f)
        elif r in ("out_of_scope", "scope_unspecified"):
            sc.append(f)
        else:
            stale.append(f)
    return FactSets(ok, sc, stale, rules, reason)


def _finding(sentence, label, fact=None, quote=None, detail="", blocking=None, weak=False):
    out = {"sentence": sentence, "label": label, "fact_key": fact["key"] if fact else None, "quote": quote,
           "blocking": (label in BLOCKING) if blocking is None else blocking, "detail": detail}
    if weak:
        out["_weak"] = True
    return out


def _settle(items: list[dict], fs: "FactSets", by_key: dict) -> list[dict]:
    """Evidence tiers. A blocking label needs a STRONG basis (an exact value / date / percentage,
    a slot, an identifier, a named offer / product, a grade on a named scheme, a forbidden phrase,
    a claim word no fact supports). A WEAK basis (shared common words, an attribute term, a
    fragment of a name) is only "review": the reviewer decides."""
    out = []
    for x in items:
        weak = x.pop("_weak", False)
        if weak and x["blocking"]:
            f = by_key.get(x["fact_key"]) or {}
            why = (_why(fs, f) or "valid here") if f else "not in scope"
            x = dict(x, label="review", blocking=False,
                     detail=(f"needs your judgement: this may refer to {F.subject_ref(f) or x['fact_key']}, "
                             f"which is {why} ({x['detail']})")[:400])
        out.append(x)
    return out


def _quote(f: dict) -> str:
    return (f.get("value_text") or f.get("text") or "")[:300]


def _best(cands: list[dict], sentence: str) -> dict:
    return max(cands, key=lambda f: (bool(F.subject_ref(f)) and phrase_in(F.subject_ref(f), sentence),
                                     _subject_named(f, sentence), _overlap(sentence, f)))


def _why(fs: FactSets, f: dict) -> str:
    r = fs.reason.get(f["key"])
    if r in ("out_of_scope", "scope_unspecified"):
        return f"only for {F.scope_text(f) or 'another scope'}"
    return {"expired": f"expired {f.get('valid_to') or ''}".strip(), "not_yet_valid": f"valid from {f.get('valid_from')}",
            "superseded": "superseded by a newer fact", "retired": "retired"}.get(r, r or "")


def _code_family(keys, fam: str) -> list[Decimal]:
    return [Decimal(k[2]) for k in keys if k[0] == "code" and k[1] == fam]


def _code_in_range(v: Val, keys) -> bool:
    """DN25 is inside a fact that says "DN15 to DN100"."""
    if v.kind != "code":
        return False
    fam = _code_family(keys, v.key[1])
    return len(fam) >= 2 and min(fam) <= Decimal(v.key[2]) <= max(fam)


def _same_attribute(v: Val, f: dict, sentence: str) -> bool:
    prim, sec = fact_values(f)
    if v.kind == "code":       # PN63 against a fact that says PN40: same family, another value
        return bool(_code_family(prim, v.key[1]))
    if v.kind == "hours":
        # "support is available 24/7" against a support-hours fact that states set hours (a time
        # range, days): said exactly (not a hint), about the fact's subject or attribute
        if v.extra.get("hint") or f.get("fact_type") != "hours" or v.key in prim | sec:
            return False
        if not any(k[0] == "time" for k in prim | sec):
            return False
        ref_words = {_stem(w) for w in F.content_words(F.subject_ref(f)) - GENERIC}
        attr_words = {_stem(w) for w in F.content_words(str(f.get("attribute") or "").replace("_", " ")) - GENERIC
                      - {"hour", "hours", "time", "times"}}
        return bool((ref_words | attr_words) & {_stem(w) for w in F.content_words(sentence)})
    kinds = [k for k in prim if k[0] == v.kind and (v.kind != "money" or k[2] == v.key[2])
             and (v.kind not in ("qty", "freq") or k[2] == v.key[2])]
    if not kinds:
        return False
    ref_words = F.content_words(F.subject_ref(f)) - GENERIC
    attr_words = F.content_words(str(f.get("attribute") or "").replace("_", " ")) - GENERIC
    sw = F.content_words(sentence)
    return bool(ref_words & sw) or bool(attr_words & sw) or (v.kind == "qty" and _same_frame(v, f, sentence))


def _frame(text: str, start: int, end: int) -> set[str]:
    """The words a quantity is said with: two before it, four after it ("up to 70 miles per charge"
    -> charge), less units, numbers and everyday words."""
    before = F.words(text[max(0, start - 40):start])[-2:]
    after = F.words(text[end:end + 40])[:4]
    return {_stem(w) for w in before + after if w not in F.STOP and w not in GENERIC and w not in UNIT_WORDS
            and w not in WEAK and len(w) >= 4 and not any(c.isdigit() for c in w)}


def _same_frame(v: Val, f: dict, sentence: str) -> bool:
    """Another number in the same frame as the fact's own value, with the same unit: "up to 100 miles
    per charge" against "up to 70 miles per charge". The frame word is what the number measures."""
    vt = f.get("value_text") or ""
    mine = _frame(sentence, v.start, v.end)
    return bool(mine) and any(w.kind == v.kind and w.key[2] == v.key[2] and _frame(vt, w.start, w.end) & mine
                              for w in extract(vt))


def _tied(f: dict, prof: dict, sentence: str, s_set: set) -> bool:
    """A quantity ("six weeks") is only another fact's when the sentence is about that fact too:
    its subject, or a distinctive word of its value."""
    p = prof.get(f["key"])
    return p is not None and bool((p.subj | p.attr) & s_set)


def _name_part(v: Val, f: dict, sentence: str) -> bool:
    """A code that is part of the fact's NAME ("A2" of "A2 Summer Intensive", "B2" of "Cambridge B2
    First"), not its value, with the name itself not in the sentence: it only hints at the fact."""
    if v.kind != "code":
        return False
    ref = F.subject_ref(f)
    if not ref or phrase_in(ref, sentence) or phrase_in(re.sub(r"\s*\([^)]*\)", "", ref), sentence):
        return False
    return re.search(r"(?<![\w-])" + re.escape(v.text) + r"(?![\w-])", ref) is not None


def _pct_value(f: dict) -> Decimal | None:
    val, unit = f.get("value"), str(f.get("unit") or "").strip().lower()
    if isinstance(val, (int, float)) and not isinstance(val, bool) and 0 < val < 100 and (
            unit in ("%", "percent", "per cent") or re.search(r"\d\s?%", f.get("value_text") or "")):
        return Decimal(str(val))
    return None


def _discounted_by(v: Val, fs: "FactSets", offers: list[dict]):
    """(offer, base price fact) when the money value is a valid price less a stale / out-of-scope
    offer's percentage."""
    try:
        amount = Decimal(v.key[1])
    except (InvalidOperation, IndexError):
        return None
    for g in offers:
        pct = _pct_value(g)
        off = [Decimal(k[1]) for k in offer_savings(g) | core_values(g) if k[0] == "money" and k[2] == v.key[2]] \
            if pct is None else []
        for b in fs.ok:
            bv = b.get("value")
            if (b.get("fact_type") != "price" or not isinstance(bv, (int, float)) or isinstance(bv, bool)
                    or CURRENCY.get(str(b.get("currency") or "").lower(), str(b.get("currency") or "").upper()) != v.key[2]):
                continue
            if pct is not None and abs(Decimal(str(bv)) * (100 - pct) / 100 - amount) < Decimal("0.01"):
                return g, b
            if any(o > 0 and abs(Decimal(str(bv)) - o - amount) < Decimal("0.01") for o in off):
                return g, b
    return None


# "for under a hundred quid", "less than £50": a round ceiling said instead of the price. When no fact
# has that very amount, it stands for the highest price of a fact just under it (at most 10% less, same
# currency): "under £100" for a £99 offer. Only STRONG when the sentence is about that fact too.
UNDER_BEFORE = re.compile(r"(?i:\b(?:just\s+|only\s+)?(?:under|less\s+than|below|not\s+even)\s+$)")


def _under_value(v: Val, s: str, live: list[dict], vals_cache: dict):
    """(fact, value key) a price ceiling stands for, or None."""
    if v.kind != "money" or not UNDER_BEFORE.search(s[max(0, v.start - 20):v.start]):
        return None
    if any(v.key in vals_cache[f["key"]][0] or v.key in vals_cache[f["key"]][1] for f in live if f["key"] in vals_cache):
        return None
    top = Decimal(v.key[1])
    best = None
    for f in live:
        for k in vals_cache.get(f["key"], (set(), set()))[0]:
            if k[0] == "money" and k[2] == v.key[2] and top * Decimal("0.9") <= Decimal(k[1]) < top:
                if best is None or Decimal(k[1]) > Decimal(best[1][1]):
                    best = (f, k)
    return best


def _deadline_passed(v: Val, sentence: str, day: date) -> bool:
    if v.kind != "date" or not DEADLINE.search(sentence):
        return False
    year = v.extra.get("year") or day.year
    try:
        d = date(year, v.key[1], v.key[2])
    except ValueError:
        return False
    if not v.extra.get("year") and (day - d).days > 180:
        return False           # "ends 3 January" written in December means next year
    return d < day


TOMORROW = re.compile(r"(?i)\btomorrow\b")
# a price said as covering more than one person: "£240 for you and a friend", "£90 for two", "£60 for
# the pair of you" (not "£240 each for you and a friend", not "£90 for two hours")
GROUP_AFTER = re.compile(r"(?i:^[ \t]*(?:in\s+total\s+)?for\s+(?:(?:you\s+and\s+(?:a|an|one|your)\s+(?:[a-z]+\s+)?"
                         r"(?:friend|partner|mate|colleague|plus[- ]one|other\s+half|husband|wife|mum|dad|child|kid|"
                         r"son|daughter|sister|brother|buddy|pal|guest))|(?:the\s+)?(?:two|both|pair|three|four|2|3|4)"
                         r"\s+of\s+you|(?:a\s+)?couples?|(?:two|three|four|five|six|2|3|4|5|6)"
                         r"(?:\s+(?:people|persons|adults|guests|learners|students|diners|friends|of\s+you))?"
                         r"(?![\w-]|\s+(?:hours?|nights?|days?|weeks?|months?|years?|sessions?|lessons?|classes|"
                         r"items?|units?|bags?|boxes|tickets?|mins?|minutes?|courses?|visits?|rooms?|[a-z]+s)\b))\b)")


# a price said as covering a whole group ("£7 a month for your whole team", "£7 per company", "£55 for
# all of you"), after an optional period ("a month"). Not "for everyone (who books)": that is each.
_PERIOD = r"(?:(?:a|an|per|each|every)\s+(?:month|year|week|night|day|hour|term)\b[ \t,]*)?"
_GROUP_NOUN = (r"(?:team|company|business|firm|family|group|office|class|party|household|staff|workforce|"
               r"organi[sz]ation|school|crew|department|practice|account|payroll)")
WHOLE_GROUP_AFTER = re.compile(
    r"(?i:^[ \t]*" + _PERIOD + r"(?:(?:in\s+total\s+)?for\s+(?:(?:your|the|our|their)\s+(?:whole|entire)\s+" + _GROUP_NOUN +
    r"|all\s+of\s+you|the\s+(?:whole|entire)\s+lot)|(?:per|a)\s+(?:company|business|firm|team|household|"
    r"organi[sz]ation|account)|(?:per|a)\s+party)\b)")
# a price said per head ("£180 per child", "£95pp", "£40 a head", "£12 per person per month")
_HEAD_NOUN = (r"(?:person|people|head|child|children|kid|kids|guest|adult|attendee|participant|learner|student|pupil|"
              r"delegate|diner|visitor|player|rider|passenger|employee|user|seat|staff\s+member|member\s+of\s+staff)")
PER_HEAD_AFTER = re.compile(
    r"(?i:^[ \t]*" + _PERIOD + r"(?:(?:per|a|for\s+(?:each|every)|each)\s+" + _HEAD_NOUN + r"\b|"
    r"(?:pp|p/p|p\.p\.)(?![\w]))[ \t]*(?:(?:a|an|per|each|every)\s+(?:month|year|week|night|day|hour)\b)?)")
PER_HEAD_FACT = re.compile(r"(?i)(?<!not\s)(?<!no\s)\b(?:per|each|a|every)\s+" + _HEAD_NOUN + r"\b|\bpp\b|"
                           r"\bper[\s_-]?(?:person|head|seat|user|employee)\b")
HEAD_BASES = {"per_person", "per_seat"}
NOT_HEAD_BASES = {"flat", "per_unit", "per_room"}


def _per_head_fact(f: dict) -> bool:
    return f.get("basis") in HEAD_BASES or bool(PER_HEAD_FACT.search(f.get("value_text") or ""))


def _group_clash(v: Val, f: dict, s: str) -> str | None:
    """A price said on another basis than the fact's (contract basis): a per-person / per-seat /
    per-unit price said as the price for a group ("£240 for you and a friend", "£7 a month for your
    whole team"), or a flat / per-unit / per-room price said per head ("£180 per child" when it is per
    party). Only when the fact itself does not name that basis."""
    blob = " ".join(str(x) for x in (f.get("text"), f.get("value_text")) if x)
    after = s[v.end:]
    if _per_head_fact(f) or f.get("basis") == "per_unit":
        m = GROUP_AFTER.match(after) if _per_head_fact(f) else None
        if m and not re.search(r"(?i)\b(?:two|2)\s+(?:sharing|people|persons|adults|guests)\b|\bcouples?\b|"
                               r"\bfor\s+two\b|\bpair\b", blob):
            return m.group(0).strip()
        m = WHOLE_GROUP_AFTER.match(after)
        if m:
            noun = F.words(m.group(0))[-1]
            if not re.search(r"(?i)\b(?:per|a|each|whole|entire|flat)\s+" + re.escape(noun) + r"\b|\bflat\b", blob):
                return m.group(0).strip()
    if f.get("basis") in NOT_HEAD_BASES and not _per_head_fact(f):
        m = PER_HEAD_AFTER.match(after)
        if m:
            return m.group(0).strip()
    return None


def _basis_word(f: dict) -> str:
    b = f.get("basis")
    if _per_head_fact(f):
        return "per person" if b != "per_seat" else "per seat"
    return {"flat": "a flat price", "per_unit": "per unit", "per_room": "per room"}.get(b, str(b or "")).replace("_", " ")


UNBOUNDED = re.compile(r"(?i:\b(?:lifetime|life[- ]?long|forever|permanent|never[- ]ending|everlasting)[ \t-]+"
                       r"(?:(?!of\b|in\b)[a-z&'-]+[ \t]+){0,2}?(?P<n>[a-z]{4,})\b|"
                       r"\b(?P<n2>[a-z]{4,})[ \t]+(?:(?:is|are|lasts?|runs?|valid|good)[ \t]+)?(?:for[ \t]+life|for[ \t]*ever|"
                       r"that[ \t]+never[ \t]+(?:expires?|ends?|runs[ \t]+out))\b)")
PERIOD_UNITS = frozenset("day working_day week month year".split())


def _period_nouns(f: dict) -> set[str]:
    """What a fact gives a length to: its attribute words and the last word of its value
    ("12-month parts and labour warranty" -> warranty)."""
    attr = F.content_words(str(f.get("attribute") or "").replace("_", " ")) - GENERIC
    vt = [w for w in F.words(f.get("value_text") or "") if not w.isdigit()]
    return {_stem(w) for w in attr | set(vt[-1:]) if len(w) >= 4}


def _finite_period(f: dict) -> bool:
    """The fact states a set length (a number of days / months / years) and never says it is unending."""
    blob = " ".join(str(x) for x in (f.get("text"), f.get("value_text")) if x)
    if re.search(r"(?i)\blife\s?(?:time|long)\b|\bfor\s+life\b|\bforever\b|\bpermanent\b|\bnever\s+expires?\b", blob):
        return False
    return any(k[0] == "qty" and k[2] in PERIOD_UNITS for k in core_values(f))


PAST_RX = re.compile(r"(?i)\b(?:was|were|used\s+to|had\s+been|back\s+(?:when|then|in))\b")
PERSON_RX = re.compile(r"(?i)\b(?:we|we're|we’re|we've|we’ve|our|ours|us|you|you're|you’re|your|yours)\b")


def _scene_setting(s: str, facts: list[dict]) -> bool:
    """A general sentence about the past ("contracts made sense when every office was full five days a
    week"): past tense, no first or second person, no fact's full name."""
    return (PAST_RX.search(s) is not None and PERSON_RX.search(s) is None
            and not any(len(F.subject_ref(f)) >= 4 and phrase_in(F.subject_ref(f), s) for f in facts))


def _numbers(text: str) -> set[str]:
    out = set()
    for m in re.finditer(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s?(k\b)?", text or "", re.I):
        d = _dec(m.group(1))
        if d is not None:
            out.add(_canon(d * 1000 if m.group(2) else d))
    return out


HEAD_SKIP = frozenset(F.STOP | GENERIC | {"of", "long", "full", "whole", "extra", "free", "each", "every"})


def _head_after(text: str, end: int) -> str | None:
    """The first content word after a quantity: "a 55-minute treatment of your choice" -> treatment."""
    for w in F.words(text[end:])[:3]:
        if w not in HEAD_SKIP and not w.isdigit() and len(w) >= 3:
            return _stem(w)
    return None


def _copied_detail(v: Val, s: str, fs: "FactSets", vals_cache: dict):
    """A quantity no valid in-scope fact mentions, said with the same thing after it ("55-minute
    treatment") as in exactly one stale / out-of-scope fact's text: that fact's detail, carried over.
    STRONG: the number, the unit and the noun it is about must all be that fact's."""
    head = _head_after(s, v.end)
    if not head:
        return None
    hits = []
    for f in fs.stale + fs.scope:
        if v.key not in vals_cache[f["key"]][0] | vals_cache[f["key"]][1]:
            continue
        t = " ".join(str(x) for x in (f.get("text"), f.get("value_text")) if x)
        if any(w.key == v.key and _head_after(t, w.end) == head for w in extract(t)):
            hits.append(f)
    if len({f["key"] for f in hits}) != 1:
        return None
    return hits[0], head


def _thing_after(sentence: str, start: int) -> str | None:
    """The noun a superlative is about: "the best café in town" -> caf (stemmed)."""
    words = F.words(sentence[start:])
    for i, w in enumerate(words):
        if w in SUPERLATIVE:
            for x in words[i + 1:]:
                if x not in F.STOP and x not in ID_STOP and x not in ("in", "of", "rated", "loved"):
                    return _stem(x)
            return None
    return None


_RATING_NUM_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                     "eight": "8", "nine": "9", "ten": "10"}


def _rating_numbers(text: str) -> set[str]:
    """The numbers of a rating, said in digits or words: "rated Four" -> 4."""
    return _numbers(text) | {_RATING_NUM_WORDS[w] for w in F.words(text) if w in _RATING_NUM_WORDS}


# who gave a rating: "5 stars by over 300 couples", "4.9 from 800+ Google reviews", "rated 5/5 by our
# customers". A score from people is a review score: only a fact about reviews / those people has it.
RATED_BY = re.compile(r"(?i:^[^.;!?]{0,25}?\b(?:by|from)\s+(?:(?:over|more\s+than|nearly|almost|around|some)\s+)?"
                      r"(?:[\d,.]+\s?k?\+?\s+)?(?:(?:our|happy|local|real|verified|satisfied|google|trustpilot|"
                      r"facebook|[A-Z][a-z]+)\s+){0,2}(?P<who>[a-z]{4,}s)\b)")
RATING_KINDS_NUM = ("rated", "stars", "score")


def _rating_supports(f: dict, kind: str, kw: str, sentence: str, start: int, any_number: bool = False) -> bool:
    if not (f.get("fact_type") in RATING_TYPES or (f.get("claim_class") or "none") in RATING_CLASSES
            or phrase_in(kw, _blob(f))):
        return False
    blob = _blob(f) + " " + (str(f.get("value")) if f.get("value") is not None else "")
    if phrase_in(kw, blob):
        return True
    words = {_stem(w) for w in F.words(blob)}
    need = RATING_WORDS.get(kind) or set()
    if kind == "count":
        need = {_stem(w) for w in F.words(kw) if not w[0].isdigit()} & {_stem(w) for w in re.findall(r"[a-z]+", _COUNT_NOUNS)}
    if kind == "possest":      # "Yorkshire's greenest": a fact that says "greenest"
        need = {_stem(F.words(kw)[-1])}
    if kind == "est":          # "the friendliest": a fact that says "friendliest"
        need = {_stem(F.words(kw)[1])}
    if kind == "bestx":        # "best-connected": a fact that says "connected" (and is a claim / result)
        need = {_stem(F.words(kw)[-1])}
    if kind == "most":         # "most reliable": a fact that says "reliable" (the adjective itself)
        need = {_stem(F.words(kw)[-1])}
    if kind == "staff":        # "every tutor is a qualified teacher": a fact that says who, and both words
        ws = F.words(kw)
        i = next((j for j, w in enumerate(ws) if w in ("qualified", "licensed")), len(ws))
        who = [w for w in ws[:i] if w not in ("is", "are", "a", "an", "fully")][-1:]
        if not {_stem(w) for w in ws[i:i + 2] + who if w not in F.STOP} <= words:
            return False
    if need and not need & words:
        return False
    if kind == "possessive":
        # "Ashcombe's number one": the fact must say that ranking itself, not just any award
        tail = re.split(r"['’]s\s+", kw, maxsplit=1)[-1].lower()
        alts = (["number one", "no. 1", "no 1", "#1", "top"] if re.fullmatch(r"number\s+one|#1", tail) else [tail])
        low = " " + blob.lower() + " "
        if not any(phrase_in(a, blob) or (" " + a + " ") in low for a in alts):
            return False
    if not any_number and not _rating_numbers(kw) <= _rating_numbers(blob):
        return False
    if kind in RATING_KINDS_NUM:
        by = RATED_BY.search(sentence[start:])
        if by and not {_stem(by.group("who")), "review"} & words:
            return False           # "5 stars by 300 couples" is not a food hygiene rating 5
    thing = _thing_after(sentence, start) if kind in ("voted", "best", "first", "popular", "leading", "possessive") else None
    if kind == "est":
        ws = F.words(kw)
        thing = _stem(ws[2]) if len(ws) > 2 and ws[2] not in ("in", "of", "around", "across") else None
    if thing and thing not in words:
        return False
    before = RUN_BEFORE.search(sentence[:start]) if kind == "award" else None
    ids = id_tokens(before.group(1)) if before else set()
    return ids <= (words | id_tokens(blob))


def check_text(text: str, all_facts: list[dict], day: date, scope: dict,
               slot_used: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """(findings, used fact keys) for one piece. `slot_used`: fact keys filled from slots."""
    fs = sort_facts(all_facts, day, scope)
    by_key = {f["key"]: f for f in all_facts}
    vals_cache = {f["key"]: fact_values(f) for f in all_facts if f.get("status") != "draft"}
    core_cache = {f["key"]: core_values(f) for f in all_facts if f.get("status") != "draft"}
    prof = profiles(all_facts)
    ok_pairs = set().union(*(prof[f["key"]].all_pairs for f in fs.ok if f["key"] in prof))
    ok_terms = set().union(*(prof[f["key"]].terms for f in fs.ok if f["key"] in prof))
    findings: list[dict] = []
    used: dict[str, list[str]] = {}       # fact key -> every sentence using it (in order)
    strong_used: dict[str, list[str]] = {}   # the same, only STRONG uses (they carry disclosures)

    def use(key: str, sentence: str, strong: bool = True):
        for d in ((used, strong_used) if strong else (used,)):
            lst = d.setdefault(key, [])
            if sentence not in lst:
                lst.append(sentence)

    for k in slot_used or []:
        f = by_key.get(k)
        if f:
            vt = f.get("value_text") or ""
            pos = text.find(vt) if vt else -1
            while pos >= 0:
                use(k, F.sentence_at(text, pos))
                pos = text.find(vt, pos + 1)
            used.setdefault(k, [])
            strong_used.setdefault(k, [])

    kw_facts = [(p, f) for f in all_facts if f.get("status") != "draft" and (f.get("claim_class") or "none") != "none"
                for p in F.phrases(f.get("allowed_phrasing"))]
    live = [f for f in all_facts if f.get("status") != "draft"]
    grades = {f["key"]: fact_grades(f) for f in live}
    t_grades = {f["key"]: text_grades(f) for f in live}
    schemes = {g.scheme for gl in list(grades.values()) + list(t_grades.values()) for g in gl}
    g_rx, g_stars, g_named = scheme_regex(schemes), "star" in schemes, named_grade_regex(schemes)
    heads = {f["key"]: offer_heads(f) for f in live}
    objects = {f["key"]: offer_objects(f) for f in live}

    def gives(g, w):     # the benefit's object is this fact's: its head word, or two of its object words
        lw = {_lemma(x) for x in w}     # "deliver prescriptions for free" gives "free prescription delivery"
        return bool({_lemma(x) for x in heads.get(g["key"], set())} & lw) or len(
            {_lemma(x) for x in objects.get(g["key"], set())} & lw) >= 2
    savings = {f["key"]: offer_savings(f) for f in live}
    r_schemes = {f["key"]: rating_schemes(f) for f in live}
    idents = {f["key"]: fact_identifiers(f) for f in live}
    s_names = {f["key"]: scheme_names(f) for f in live}
    sale_offers = [f for f in fs.stale + fs.scope if F.subject_kind(f) == "offer" and (
        _pct_value(f) is not None or (isinstance(f.get("value"), (int, float)) and f.get("currency")
                                      and re.search(r"(?i)\boff\b|\bsave", f.get("value_text") or "")))]
    deals = {f["key"]: deal_of(" . ".join(str(x) for x in (f.get("value_text"), f.get("text")) if x))
             for f in live if F.subject_kind(f) == "offer"}
    allergens = {f["key"]: allergen_items(f) for f in fs.ok}
    incl_words = inclusion_words(fs, ok_terms)
    neg_words = {f["key"]: _neg_window(" ".join(x for x in (f.get("value_text"), f.get("text")) if x)) for f in live}
    df: dict[str, int] = {}
    for f in live:
        for w in set(_seq(F.subject_ref(f))):
            df[w] = df.get(w, 0) + 1
    common_ref = {w for w, c in df.items() if len(live) >= 4 and c > len(live) / 2}
    own_words = {}          # per valid fact: its value / attribute words no other valid fact uses
    for g in fs.ok:
        if g["key"] not in prof:
            continue
        others = set().union(*(prof[h["key"]].terms for h in fs.ok if h["key"] != g["key"] and h["key"] in prof))
        mine = set(_seq(g.get("value_text") or "")) | set(_seq(str(g.get("attribute") or "").replace("_", " ")))
        own_words[g["key"]] = {w for w in mine - others if len(w) >= 4 and w not in WEAK and w not in UNIT_WORDS
                               and not any(c.isdigit() for c in w)}

    def distinct_generic(pr):
        return all(w in WEAK or w in F.STOP or len(w) < 3 for w in pr)
    fact_words = set()
    for f in live:
        if (f.get("sensitivity") or "public") == "restricted":
            continue          # never in copy: what only a restricted fact says is not offered
        fact_words |= {_stem(w) for w in F.words(" ".join(str(x) for x in (
            f.get("text"), f.get("value_text"), F.subject_ref(f), f.get("attribute"),
            *F.phrases(f.get("allowed_phrasing"))) if x).replace("_", " "))}

    for a, b in F.sentence_spans(text):
        s = text[a:b]
        s_seq = _seq(s)
        s_set, s_pairs = set(s_seq), _pairs(s_seq)
        s_find: list[dict] = []
        # 0. graded identifiers: "Level 3" against a fact that says Level 2, "EPC A" against EPC B
        s_grades = sentence_grades(s, g_rx, g_stars, g_named, schemes)
        s_grades += short_class_grades(s, s_grades, schemes, grades, fs)
        grade_spans = [(g.start, g.end) for g in s_grades]
        veto: set[str] = set()          # facts a grade conflicts with: no keyword may "match" them here
        g_find = [x for sg in s_grades for x in _grade_finding(fs, grades, s, sg, lambda *_: None, veto, t_grades)]
        for x in g_find:       # "SOC 2 Type II" against "SOC 2 Type I": the conflicting grade wins
            if x["label"] != "match" or x["fact_key"] not in veto:
                s_find.append(x)
                if x["label"] == "match":
                    use(x["fact_key"], s, strong=not x.get("_weak"))
        # 0b. identifier numbers: "certificate QA 20471" against a fact that says QA 20417
        id_find, id_spans = _ident_findings(fs, idents, s, veto, use)
        s_find += id_find
        # 0c. a rating / grade number on a scheme a fact rates ("5-star food hygiene rating")
        sr_find, sr_spans = ([], []) if grade_spans else _scheme_rating_findings(fs, s, use)
        s_find += sr_find
        # 0d. a rating word on a named scheme ("rated Excellent by the CQC")
        wr_find = _word_rating_findings(fs, r_schemes, s, use)
        s_find += wr_find
        # 0e. a tier said with a scheme's name ("Lexcel Gold") that the scheme's fact does not name
        s_find += _tier_name_findings(fs, s_names, s, veto)
        # 0e2. a register's grade by a name the facts never give it ("NESR Master Contractor")
        s_find += _grade_name_findings(fs, s, veto)
        # 0f. a person's membership grade of a body ("Priya Doshi, a Fellow of the AAT") against the
        #     grade a valid fact gives the same person ("a full member of the AAT")
        mg_find = _member_grade_findings(fs, s, use)
        s_find += mg_find
        veto |= {x["fact_key"] for x in mg_find if x["blocking"]}
        veto |= {x["fact_key"] for x in wr_find if x["blocking"]}
        # 1. values
        for v in extract(s):
            under = _under_value(v, s, live, vals_cache)
            if under:
                uf, ukey = under
                v = Val(v.kind, ukey, v.text, v.start, v.end,
                        dict(v.extra, under=True, **({} if _tied(uf, prof, s, s_set) or _subject_named(uf, s)
                                                      else {"hint": True})))
            if v.kind == "code" and (_overlaps(grade_spans, v.start, v.end) or _overlaps(id_spans, v.start, v.end)):
                continue
            save_cue = bool(SAVE_CUE.search(s))
            ok = [f for f in fs.ok if v.key in vals_cache[f["key"]][0] or v.key in vals_cache[f["key"]][1]
                  or _code_in_range(v, vals_cache[f["key"]][0]) or (save_cue and v.key in savings[f["key"]])]
            loose = ""
            if not ok and v.kind == "qty" and v.key[2] == "day":
                # "5-day crowns" for a fact that says 5 working days: the same promise, shortened
                wd = ("qty", v.key[1], "working_day")
                ok = [f for f in fs.ok if wd in vals_cache[f["key"]][0]
                      and (_tied(f, prof, s, s_set) or _subject_named(f, s))]
                loose = " working days" if ok else ""
            if ok:
                f = _best(ok, s)
                clash = _group_clash(v, f, s) if v.kind == "money" else None
                if clash:
                    use(f["key"], s)
                    s_find.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                           f'{v.text} "{clash}": the fact\'s price is {_basis_word(f)} ({_quote(f)})'))
                    continue
                # a value that is not the fact's own ("up to £250,000" of a fee band, "8am to 6pm" of a
                # day rate) ties the sentence to the fact, but does not carry its disclosures
                use(f["key"], s, strong=not (_name_part(v, f, s) or v.extra.get("hint"))
                    and (v.key in core_cache[f["key"]] or v.key in savings[f["key"]]
                         or (loose and ("qty", v.key[1], "working_day") in core_cache[f["key"]])))
                s_find.append(_finding(s, "match", f, _quote(f), f"{v.text} = {f['key']}"
                                       + (f" (read as {v.key[1]}{loose})" if loose else "")))
                continue

            def tie(f):     # a quantity / "24/7" is a fact's only with a word of that fact nearby
                if v.kind == "freq":     # "eight times a day": the business has a schedule of that period
                    return _tied(f, prof, s, s_set) or any(k[0] == "freq" and k[2] == v.key[2]
                                                           for g in fs.ok for k in vals_cache[g["key"]][0])
                return v.kind not in ("qty", "hours") or _tied(f, prof, s, s_set)

            def weak_basis(f):
                # a code that is only part of the fact's name, a described (not stated) value, or a
                # bare quantity in a sentence about nothing else of the fact
                return _name_part(v, f, s) or bool(v.extra.get("hint")) or not tie(f)
            sc = [f for f in fs.scope if v.key in vals_cache[f["key"]][0]]
            sc = [f for f in sc if tie(f)] or sc      # untied quantities stay, as WEAK
            if sc:
                f = _best(sc, s)
                s_find.append(_finding(s, "wrong_scope", f, _quote(f), f"{v.text} is {_why(fs, f)}",
                                       weak=weak_basis(f)))
                continue
            st = [f for f in fs.stale if v.key in vals_cache[f["key"]][0]]
            st = [f for f in st if tie(f)] or st
            if st:
                f = _best(st, s)
                s_find.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                       f"{v.text} is from a fact that is {_why(fs, f)} at {day.isoformat()}",
                                       weak=weak_basis(f)))
                continue
            # the saving of an offer that is stale / out of scope ("Save £20 on your autumn service"),
            # said with a saving word: that offer's, not another price's
            sv = [f for f in fs.stale + fs.scope if v.key in savings[f["key"]]] if save_cue else []
            if sv:
                f = _best(sv, s)
                s_find.append(_finding(s, "conflict_or_expired" if f in fs.stale else "wrong_scope", f, _quote(f),
                                       f"{v.text} is the saving of {F.subject_ref(f) or f['key']}, which is {_why(fs, f)}"))
                continue
            # a price an offer's discount makes of a valid price ("down to £1,232.50" = £1,450 less
            # 15%): that offer's, when it is stale / out of scope
            disc_of = _discounted_by(v, fs, sale_offers) if v.kind == "money" else None
            if disc_of:
                g, base = disc_of
                s_find.append(_finding(s, "conflict_or_expired" if g in fs.stale else "wrong_scope", g, _quote(g),
                                       f"{v.text} is {base['value_text'] or base['value']} less "
                                       f"{str(g['value']) + '%' if _pct_value(g) is not None else g.get('value_text')} "
                                       f"({F.subject_ref(g) or g['key']}), which is {_why(fs, g)}"))
                continue
            if _deadline_passed(v, s, day):
                s_find.append(_finding(s, "conflict_or_expired", None, None,
                                       f"{v.text} is before the publish date {day.isoformat()}"))
                continue
            same = [f for f in fs.ok if _same_attribute(v, f, s) and (v.kind != "freq" or _tied(f, prof, s, s_set))]
            if same:
                f = _best(same, s)
                # "... when every office was full five days a week": a sentence about the past that
                # speaks of no one here (no we / our / you, no fact's name) sets the scene; WEAK
                s_find.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                       f"{v.text} differs from the fact: {_quote(f)}",
                                       weak=v.kind in ("qty", "freq") and _scene_setting(s, live)))
            elif v.kind in ("money", "percent"):
                s_find.append(_finding(s, "no_source", None, None,
                                       f"{v.text}: no fact has this {'price' if v.kind == 'money' else 'percentage'}",
                                       blocking=True))
            elif v.kind == "qty" and (cp := _copied_detail(v, s, fs, vals_cache)):
                f, head = cp
                s_find.append(_finding(s, "conflict_or_expired" if f in fs.stale else "wrong_scope", f, _quote(f),
                                       f"{v.text} {head}: only {F.subject_ref(f) or f['key']} says it, which is "
                                       f"{_why(fs, f)}"))
            elif v.kind == "qty" and not any(_overlaps([(m.start(), m.end())], v.start, v.end)
                                             for m in UNSOURCED_RX.finditer(s)):
                s_find.append(_finding(s, "review", None, None, f"{v.text}: no fact mentions it"))
        # 1b. a period said as never ending ("lifetime warranty", "cover for life") against a valid
        #     fact that gives the same thing a set length ("12-month warranty")
        for m in UNBOUNDED.finditer(s):
            if re.search(r"(?i)\b(?:in\s+a|not\s+(?:a\s+)?|no)[\s-]*$", s[:m.start()]):
                continue          # "once in a lifetime", "not a lifetime warranty"
            noun = _stem((m.group("n") or m.group("n2")).lower())
            hit = [f for f in fs.ok if f["key"] not in veto and noun in _period_nouns(f) and _finite_period(f)]
            if hit:
                f = _best(hit, s)
                use(f["key"], s)
                s_find.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                       f'"{m.group(0)}" differs from the fact: {_quote(f)}'))
        # 2. forbidden phrasing
        forbidden_spans, forbidden_raw = [], []
        for f in fs.rules:
            for p in F.phrases(f.get("forbidden_phrasing")):
                if not phrase_in(p, s):
                    continue
                exempt = any(p.lower() in (x.lower() for x in F.phrases(g.get("allowed_phrasing")))
                             and (_subject_named(g, s) or F.subject_kind(g) == "business") for g in fs.ok)
                if exempt:
                    continue
                why = next((x.get("why") for x in f.get("forbidden_phrasing") or []
                            if isinstance(x, dict) and x.get("phrase") == p and x.get("why")), None)
                forbidden_spans.append(_norm_phrase(p))
                forbidden_raw.append(p)
                s_find.append(_finding(s, "forbidden_phrase", f, p, why or f'"{p}" is not allowed here'))
        # 3a. credential claims about staff ("our instructors are registered dietitians"): only a
        #     fact that says those people hold that credential supports one
        staff_spans = []
        for st_a, st_b, st_text, cred, st_prof in staff_claims(s):
            staff_spans.append((st_a, st_b))
            sup = [f for f in fs.ok if _staff_supports(f, cred, st_prof)]
            if sup:
                f = _best(sup, s)
                others = _others_differ(f, s, st_a, st_text, cred, st_prof)
                if others:
                    s_find.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                           f'"{st_text}" is said of everyone; the fact says: {others}'[:400]))
                    continue
                # "every one of our mechanics is qualified" when the only fact is about one person
                if set(re.findall(r"[a-z]+", s[:st_a].lower())[-6:]) & UNIVERSAL and not [
                        g for g in sup if F.subject_kind(g) != "person" or set(F.words(g.get("text") or "")) & UNIVERSAL]:
                    s_find.append(_finding(s, "no_source", None, None,
                                           f'"{st_text}" is said of everyone; {f["key"]} is about one person',
                                           blocking=True))
                    continue
                use(f["key"], s)
                s_find.append(_finding(s, "match", f, _quote(f), f'"{st_text}" supported by {f["key"]}'))
                continue
            stale_sup = [f for f in fs.stale + fs.scope if _staff_supports(f, cred, st_prof)]
            if stale_sup:
                f = _best(stale_sup, s)
                s_find.append(_finding(s, "conflict_or_expired" if f in fs.stale else "wrong_scope", f, _quote(f),
                                       f'"{st_text}" is from a fact that is {_why(fs, f)}'))
                continue
            s_find.append(_finding(s, "no_source", None, None,
                                   f'"{st_text}": no fact says these staff hold this credential', blocking=True))
        # 3a2. who does every job ("a partner handles every file"): a fact must name that role
        for sv_a, sv_b, sv_text, sv_prof in service_claims(s):
            if _overlaps(staff_spans, sv_a, sv_b):
                continue
            staff_spans.append((sv_a, sv_b))
            sup = [f for f in fs.ok if _service_supports(f, sv_prof)]
            if sup:
                f = _best(sup, s)
                use(f["key"], s)
                s_find.append(_finding(s, "match", f, _quote(f), f'"{sv_text}" supported by {f["key"]}'))
                continue
            one = [f for f in fs.ok if _role_supports(f, sv_prof) and F.subject_kind(f) == "person"]
            veto |= {f["key"] for f in one}
            s_find.append(_finding(s, "no_source", None, None,
                                   f'"{sv_text}": no fact says this of every job' +
                                   (f" ({one[0]['key']} is about one person)" if one else ""), blocking=True))
        # 3b. a professional the business says it has in house / on site: a fact must name that role
        for r_a, r_b, r_text, r_prof in role_claims(s):
            if _overlaps(staff_spans, r_a, r_b):
                continue
            staff_spans.append((r_a, r_b))
            sup = [f for f in fs.ok if _role_supports(f, r_prof)]
            if sup:
                f = _best(sup, s)
                use(f["key"], s)
                s_find.append(_finding(s, "match", f, _quote(f), f'"{r_text}" supported by {f["key"]}'))
                continue
            other = [f for f in fs.scope + fs.stale if _role_supports(f, r_prof)]
            if other:
                f = _best(other, s)
                s_find.append(_finding(s, "wrong_scope" if f in fs.scope else "conflict_or_expired", f, _quote(f),
                                       f'"{r_text}" is from a fact that is {_why(fs, f)}'))
                continue
            s_find.append(_finding(s, "no_source", None, None, f'"{r_text}": no fact says you have this professional',
                                   blocking=True))
        # 2c. "our in-house design team": a team the business says it has, that no fact names (not
        #     blocking: a team is a softer claim than a named professional)
        for m in TEAM_RX.finditer(s):
            if _overlaps(staff_spans, m.start(), m.end()):
                continue
            word = m.group("w").lower()
            pat = r"(?i)\b(?:in[- ]?house|on[- ]?site)\s+" + re.escape(word) + r"\b|\b" + re.escape(word) + r"\s+team\b"
            sup = [f for f in fs.ok if re.search(pat, " ".join((_blob(f), f.get("text") or "")))]
            if sup:
                f = _best(sup, s)
                s_find.append(_finding(s, "match", f, _quote(f), f'"{m.group(0)}" named by {f["key"]}'))
            else:
                s_find.append(_finding(s, "no_source", None, None, f'"{m.group(0)}": no fact says you have this team'))
        # 2c2. "our in-house lab", "a dedicated wedding coordinator": a facility or a personal role the
        #      business says it has that no fact names (not blocking)
        for m in list(FACILITY_RX.finditer(s)) + list(DEDICATED_RX.finditer(s)):
            if _overlaps(staff_spans, m.start(), m.end()):
                continue
            word = m.group("w").lower()
            stem = word[:-1] if word.endswith("s") and not word.endswith("ss") else word
            if any(re.search(r"(?i)\b" + re.escape(stem) + r"(?:s|es)?\b", " ".join((_blob(f), f.get("text") or "")))
                   for f in fs.ok):
                continue
            s_find.append(_finding(s, "no_source", None, None, f'"{m.group(0)}": no fact says you have this'))
        # 2d. said without a fact: round-the-clock hours, a same-day / next-day promise, years of experience
        for m in UNSOURCED_RX.finditer(s):
            key = m.group(0).lower()
            if ALL_HOURS.fullmatch(m.group(0)):
                if any(("hours", "24/7") in vals_cache[f["key"]][0] | vals_cache[f["key"]][1] for f in fs.ok) or any(
                        f.get("fact_type") == "hours" for f in fs.ok + fs.scope + fs.stale):
                    continue      # an hours fact decides it (match / conflict above)
            elif re.match(r"(?i)(?:same|next)", key):
                word = re.match(r"(?i)(same|next)", key).group(1).lower()
                if any(re.search(r"(?i)\b" + word + r"[- ]day\b", " ".join((_blob(f), f.get("text") or "")))
                       for f in fs.ok + fs.scope + fs.stale):
                    continue
            else:
                n = re.search(r"\d+", key).group(0)
                if any(re.search(r"\b" + n + r"\+?\s*(?:-\s*)?years?\b", " ".join((_blob(f), f.get("text") or "")))
                       for f in fs.ok + fs.scope + fs.stale):
                    continue
            s_find.append(_finding(s, "no_source", None, None, f'"{m.group(0)}": no fact says this'))
        # 3. claim keywords (built-in + claim-bearing allowed phrasing of the facts) and ratings/awards
        hits: list[tuple[int, int, str, str]] = []
        for name, rx, cls in KEYWORDS:
            for m in rx.finditer(s):
                hits.append((m.start(), m.end(), m.group(0), cls))
        idiom_spans = [(m.start(), m.end()) for m in IDIOMS.finditer(s)]
        for kind, rx in RATINGS:
            for m in rx.finditer(s):
                if not _overlaps(idiom_spans, m.start(), m.end()):
                    hits.append((m.start(), m.end(), m.group(0), "rating:" + kind))
        for p, f in kw_facts:
            m = re.search(r"(?<![\w])" + re.escape(p) + r"(?![\w])", s, re.I)
            if m:
                hits.append((m.start(), m.end(), m.group(0), "phrase"))
        hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
        kept, end = [], -1
        for h in hits:
            if h[0] >= end:
                kept.append(h)
                end = h[1]
            elif h[1] > end and kept and h[1] - h[0] > kept[-1][1] - kept[-1][0]:
                kept[-1] = h
                end = h[1]
        seen_ids: set[frozenset] = set()

        def use_kw(k, sentence):
            if k not in veto:
                use(k, sentence)
        n_before_kw = len(s_find)
        for start, stop, kw, cls in kept:
            if any(fp in _norm_phrase(kw) or _norm_phrase(kw) in fp for fp in forbidden_spans):
                continue
            if _overlaps(staff_spans, start, stop) and (cls in IDENTITY_CLASSES or cls == "rating:staff"):
                continue
            if re.search(r"(?i)\bby$", kw) and BY_DEADLINE.match(s[stop:]):
                continue                  # "artwork approved by 12 noon": a deadline, not an approving body
            if cls.startswith("rating:"):
                if _overlaps(sr_spans, start, stop):
                    continue
                s_find += _rating_finding(fs, s, start, kw, cls[7:], use_kw)
                continue
            if cls == "cert" and READER_OWNS.search(s[:start]) and not claim_ids(s, start, stop):
                # "get your team cloud-certified": what the reader gets, not a claim about the business
                s_find.append(_finding(s, "review", None, None, f'"{kw}": said of the reader\'s own people or things'))
                continue
            ids = claim_ids(s, start, stop) if cls in IDENTITY_CLASSES else set()
            if ids:
                if frozenset(ids) in seen_ids or any(ids <= id_tokens(fp) for fp in forbidden_raw):
                    continue
                seen_ids.add(frozenset(ids))
                idf = _identity_finding(fs, s, kw, ids, use_kw)
                if idf and idf[-1] == "VETO":
                    idf = idf[:-1]
                    veto.add(idf[0]["fact_key"])
                s_find += idf
                continue
            if cls == "cert_named":
                continue                  # "registered users", "our delivery partner": not a claim
            kw_words = F.content_words(kw)

            def supports(f, kw=kw, cls=cls, kw_words=kw_words):
                if phrase_in(kw, _blob(f)):
                    return True
                types, classes = CLASS_SUPPORT.get(cls, (set(), set()))
                typed = f.get("fact_type") in types or (f.get("claim_class") or "none") in classes
                if cls == "offer_end":
                    typed = typed and bool(f.get("valid_to"))
                return typed and _overlap(s, f, kw_words) >= 1
            ok = [f for f in fs.ok if supports(f)]
            if ok and not any(phrase_in(kw, _blob(f)) for f in ok) and any(
                    phrase_in(kw, _blob(f)) for f in fs.scope + fs.stale):
                ok = []      # "free delivery" backed only by a shared word, while another scope's fact says it
            if ok:
                f = _best(ok, s)
                # a claim word backed only by a shared word is a generic mention: no disclosure duty
                if phrase_in(kw, _blob(f)) or _subject_named(f, s):
                    use_kw(f["key"], s)
                elif f["key"] not in veto:
                    use(f["key"], s, False)
                s_find.append(_finding(s, "match", f, _quote(f), f'"{kw}" supported by {f["key"]}'))
                continue
            sc = [f for f in fs.scope if phrase_in(kw, _blob(f))]
            if sc:
                f = _best(sc, s)
                s_find.append(_finding(s, "wrong_scope", f, _quote(f), f'"{kw}" is {_why(fs, f)}'))
                continue
            st = [f for f in fs.stale if phrase_in(kw, _blob(f))]
            if st:
                f = _best(st, s)
                s_find.append(_finding(s, "conflict_or_expired", f, _quote(f), f'"{kw}" is from a fact that is {_why(fs, f)}'))
                continue
            s_find.append(_finding(s, "no_source", None, None, f'"{kw}": no fact supports this claim', blocking=True))
        s_find = s_find[:n_before_kw] + [x for x in s_find[n_before_kw:]
                                         if not (x["label"] == "match" and x["fact_key"] in veto)]
        flagged = {x["fact_key"] for x in s_find if x["fact_key"] and x["blocking"] and not x.get("_weak")}
        # valid facts this sentence is about (a value of theirs, or their subject): words and pairs
        # their own sentence uses are theirs here, not an expired offer's ("a 10 kWh battery for
        # £9,450" is the package, not the expired battery upgrade)
        tied_ok = {x["fact_key"] for x in s_find if x["label"] == "match" and x["fact_key"]}
        tied_ok |= {g["key"] for g in fs.ok if g["key"] in prof and F.subject_kind(g) != "business"
                    and _subject_hit(g, prof[g["key"]], s, s_set)}
        tied_seqs = [_seq(" ".join(str(x) for x in (by_key[k].get("text"), by_key[k].get("value_text")) if x))
                     for k in tied_ok]
        tied_pairs = set().union(*(_pairs(q) for q in tied_seqs)) if tied_seqs else set()
        tied_terms = set().union(*(set(q) for q in tied_seqs)) if tied_seqs else set()
        # 4. a non-numeric stale / out-of-scope fact used by its words (subject + attribute, or a
        #    distinctive word pair of its value) that no valid in-scope fact also says
        for f in fs.stale + fs.scope:
            p = prof.get(f["key"])
            val = f.get("value")
            numeric = isinstance(val, (int, float)) and not isinstance(val, bool)
            if p is None or f["key"] in flagged or (numeric and F.subject_kind(f) not in ("offer", "package", "event")):
                continue
            pair_hit = (p.pairs & s_pairs) - ok_pairs
            if numeric:
                # an offer with a value ("no joining fee (normally £30)") is used by its benefit
                # phrase: a word pair of what it waives, said without the number
                fneg0 = neg_words.get(f["key"], set())
                pair_hit = {pr for pr in pair_hit if pr[0] in fneg0 and pr[1] in fneg0}
            if not phrase_in(F.subject_ref(f), s):
                pair_hit -= tied_pairs
            attr_hit = set()
            if F.subject_kind(f) != "business" and _subject_hit(f, p, s, s_set):   # the business is named everywhere
                also = set().union(*(prof[g["key"]].terms for g in fs.ok if g["key"] in prof and (
                    _subject_hit(g, prof[g["key"]], s, s_set) or g.get("attribute") == f.get("attribute"))))
                attr_hit = (p.attr & s_set) - also
            # the attribute itself ("breakfast"), when only stale / out-of-scope facts have it
            name_hit = p.attr_name if p.attr_name and p.attr_name <= s_set and not p.attr_name & ok_terms else set()
            # a negated word is not a use, unless the fact itself says it negated ("no joining fee")
            fneg = neg_words.get(f["key"], set())

            def negated(w):
                return _negated_in(s, w) and w not in fneg
            pair_hit = {pr for pr in pair_hit if not any(negated(w) for w in pr)}
            attr_hit = {w for w in attr_hit if not negated(w)}
            name_hit = name_hit if not any(negated(w) for w in name_hit) else set()
            if numeric:
                attr_hit, name_hit = set(), set()
            if not (pair_hit or attr_hit or name_hit):
                continue
            words = sorted({w for pr in pair_hit for w in pr} | attr_hit | name_hit)
            label = "conflict_or_expired" if f in fs.stale else "wrong_scope"
            # STRONG: the subject is named (with an attribute word), or a word pair of the value that
            # has a word no valid in-scope fact uses. Pairs of shared everyday words ("home alarm")
            # and a bare attribute term are WEAK: review only.
            strong = (bool(attr_hit) or phrase_in(F.subject_ref(f), s)
                      or any(w not in ok_terms for pr in pair_hit for w in pr))
            if not strong and set(words) <= tied_terms:
                continue          # a valid fact this sentence is about already says these words
            s_find.append(_finding(s, label, f, _quote(f), f"uses {F.subject_ref(f) or f['key']} "
                                   f"({', '.join(words)}), which is {_why(fs, f)}", weak=not strong))
            if strong:
                flagged.add(f["key"])
        # 4a. a price kept for one customer segment, offered by that segment's name ("trade pricing
        #     is open to everyone") in a task for another segment
        for f in fs.scope:
            if f["key"] in flagged or not (f.get("fact_type") == "price" or F.subject_kind(f) == "offer"
                                           or (f.get("claim_class") or "none") == "price_reference"):
                continue
            for seg in (f.get("scope") or {}).get("segments") or []:
                phrase = _norm_phrase(re.sub(r"[_/]+", " ", str(seg)))
                if len(phrase) < 3 or any(seg in ((g.get("scope") or {}).get("segments") or []) for g in fs.ok):
                    continue
                ns = _norm_phrase(s)
                base = phrase[:-1] if phrase.endswith("s") and not phrase.endswith("ss") and len(phrase) > 4 else phrase
                m = re.search(r"(?<![\w])" + re.escape(base) + r"(?:s|'s|’s|s'|s’)?\s+" + SEG_PRICE, ns)
                if not m or _NEG_BEFORE.search(ns[max(0, m.start() - 20):m.start()]):
                    continue
                s_find.append(_finding(s, "wrong_scope", f, _quote(f),
                                       f'"{m.group(0)}": {F.subject_ref(f) or f["key"]} is {_why(fs, f)}'))
                flagged.add(f["key"])
                break
        # 4b. a benefit described without its name ("your first lesson is on us"): the object of the
        #     benefit is a stale offer's and no valid in-scope fact gives that object as a benefit
        has_no_source = any(x["label"] == "no_source" for x in s_find)
        for form, objs, conj in benefit_objects_full(s):
            w = set(objs)
            giving = [g for g in fs.ok if gives(g, w)]
            if giving:
                # a valid benefit said in its own object words ("thirty free boxes", "we deliver
                # prescriptions for free") uses that fact: its required disclosures apply (only when
                # one valid fact gives that object: two would make the use ambiguous)
                if len(giving) == 1 and form != "waive" and giving[0]["key"] not in veto:
                    use(giving[0]["key"], s)
                continue
            def gives_own(g):
                # round 12: one object word that is the stale / out-of-scope offer's own (in its name, and
                # no valid in-scope fact uses it): "a fake phishing attack ... for nothing" is the
                # "Free phishing simulation"
                own = objects.get(g["key"], set()) & {_stem(x) for x in F.words(F.subject_ref(g))}
                return gives(g, w) or any(x in own and x not in ok_terms and len(x) >= 5 for x in w)
            st = [g for g in fs.stale if gives_own(g) and g["key"] not in flagged]
            # a free thing only an out-of-scope fact gives free ("free eye tests" when only the NHS
            # sight test is free, and only for NHS-eligible patients); round 12: or a benefit the
            # out-of-scope fact's own value names ("weekend office moves at no extra charge")
            sc = [g for g in fs.scope if (_zero_price_ref(g) or heads.get(g["key"])) and gives_own(g)
                  and g["key"] not in flagged]
            if st or sc:
                g = _best(st or sc, s)
                said = sorted((heads[g["key"]] | objects[g["key"]]) & w)
                s_find.append(_finding(s, "conflict_or_expired" if st else "wrong_scope", g, _quote(g),
                                       f"describes {F.subject_ref(g) or g['key']} ({', '.join(said)}), "
                                       f"which is {_why(fs, g)}"))
                flagged.add(g["key"])
            elif (form == "after" and not has_no_source and not any(g["key"] in flagged for g in fs.stale + fs.scope)
                  and not (w | set(conj)) & fact_words):
                s_find.append(_finding(s, "no_source", None, None,
                                       f"free {' '.join(objs)}: no fact offers this", blocking=True))
                has_no_source = True
        # 4c. an "N + 1 free" deal said in words ("buy ten, drive eleven") is that offer's
        s_deal = deal_of(s)
        if s_deal:
            for pool in (fs.ok, fs.stale, fs.scope):
                hit = [g for g in pool if F.subject_kind(g) == "offer" and _deal_same(s_deal, deals.get(g["key"]))
                       and g["key"] not in flagged]
                if not hit:
                    continue
                g = _best(hit, s)
                if not any(x["fact_key"] == g["key"] for x in s_find):
                    if pool is fs.ok:
                        use(g["key"], s)
                        s_find.append(_finding(s, "match", g, _quote(g), f"the {s_deal[0]} + 1 deal of {g['key']}"))
                    else:
                        s_find.append(_finding(s, "conflict_or_expired" if pool is fs.stale else "wrong_scope", g,
                                               _quote(g), f"the {s_deal[0]} + 1 deal of {F.subject_ref(g) or g['key']}, "
                                               f"which is {_why(fs, g)}"))
                        flagged.add(g["key"])
                break
        # 4d. what only another plan / package includes ("includes payroll" of the out-of-scope Small
        #     Business plan) said as included here, or with this task's plan named
        for g, w in _foreign_inclusions(s, s_set, incl_words, fs, ok_terms):
            if g["key"] in flagged or any(x["fact_key"] == g["key"] for x in s_find):
                continue
            s_find.append(_finding(s, "wrong_scope" if g in fs.scope else "conflict_or_expired", g, _quote(g),
                                   f'"{w}" is included only in {F.subject_ref(g) or g["key"]}, which is {_why(fs, g)}'))
            flagged.add(g["key"])
        # 5. a valid fact about the subject the sentence names says "no X", the sentence says X
        for g in fs.ok:
            ref = F.subject_ref(g)
            if g["key"] in flagged or len(ref) < 4 or not phrase_in(ref, s):
                continue
            neg = _negated(" ".join(x for x in (g.get("text"), g.get("value_text")) if x))
            hit = sorted(w for w in neg if w in s_set and not _negated_in(s, w))
            if hit:
                s_find.append(_finding(s, "conflict_or_expired", g, _quote(g) or g.get("text"),
                                       f'"{hit[0]}": the fact about {ref} says it is not included ({g.get("text") or ""})'[:400]))
                flagged.add(g["key"])
        # 5a. a named award / certificate a valid fact says is not held ("not the Higher Certificate")
        dn = _denied_name_findings(fs, s, flagged)
        s_find += dn
        flagged |= {x["fact_key"] for x in dn}
        # 5b. a list of allergens / ingredients that is a valid fact's list uses that fact (so its
        #     required disclosure applies here too)
        for g in fs.ok:
            if allergens.get(g["key"]) and lists_allergens(s, allergens[g["key"]]):
                use(g["key"], s)
                if not any(x["fact_key"] == g["key"] for x in s_find):
                    s_find.append(_finding(s, "match", g, _quote(g), f"lists the items of {g['key']}"))
        # "dispatched tomorrow" = "dispatched the next day" (only for a valid fact's own value words)
        s_pairs_next = _pairs(_seq(TOMORROW.sub("next day", s))) if TOMORROW.search(s) else set()
        # 5c. a valid non-numeric fact with a required disclosure is used when its value text is
        #     written out ("call <partner> in <town>"), a word pair of its value that is its own
        #     ("manufacturer warranty") is said, or its service is named with its own attribute word
        #     ("Warranty-Safe Servicing"). Generic words other valid facts share never count.
        for g in fs.ok:
            vt = g.get("value_text") or ""
            if (not g.get("required_disclosures") or isinstance(g.get("value"), (int, float)) or extract(vt)
                    or g["key"] in flagged):     # the sentence conflicts with it: not a use
                continue
            own = own_words.get(g["key"], set())
            ref = F.subject_ref(g)
            how = None
            if len(F.content_words(vt)) >= 2 and len(vt) >= 12 and phrase_in(vt, s):
                how = "value"
            elif any((pr in s_pairs or pr in s_pairs_next) and (pr[0] in own or pr[1] in own) and not (distinct_generic(pr))
                     and not any(_negated_in(s, w) for w in pr) for pr in _pairs(_seq(vt))):
                how = "value words"
            elif len(ref) >= 4 and phrase_in(ref, s):
                attr = {w for w in _seq(str(g.get("attribute") or "").replace("_", " ")) if w in own}
                if attr & s_set and not any(_negated_in(s, w) for w in attr & s_set):
                    how = "name + " + ", ".join(sorted(attr & s_set))
            if how:
                use(g["key"], s)
                if not any(x["fact_key"] == g["key"] for x in s_find):
                    s_find.append(_finding(s, "match", g, _quote(g), f"names {g['key']} ({how})"))
        # 6. a stale offer / package named with nothing checkable
        ok_refs = {F.subject_ref(f).lower() for f in fs.ok}
        # a valid fact's required disclosure that names it ("excl. VAT and fuel surcharge") is not a use
        disc_here = [str(d) for g in fs.ok for d in g.get("required_disclosures") or [] if phrase_in(str(d), s)]
        for f in fs.stale + fs.scope:
            ref = F.subject_ref(f)
            if len(ref) < 4 or f["key"] in flagged or ref.lower() in ok_refs:
                continue
            named = phrase_in(ref, s)
            if not named and F.subject_kind(f) in ("offer", "package", "event") and f in fs.stale:
                # "the Green Fairways bursary" for the "County Green Fairways junior bursary"
                named = name_part_named(ref, s, common_ref) and not any(
                    name_part_named(F.subject_ref(g), s, common_ref) for g in fs.ok if len(F.subject_ref(g)) >= 4)
            if not named and F.subject_kind(f) == "offer" and f in fs.stale:
                named = offer_named_loosely(ref, s_seq, common_ref, ok_terms)
            if not named and F.subject_kind(f) in ("offer", "package", "event") and f in fs.stale:
                named = name_said_in_part(ref, s_seq, common_ref, ok_terms, F.subject_kind(f) == "offer")
            if not named:
                continue
            if any(phrase_in(ref, d) for d in disc_here):
                continue
            clash = _scope_clash(f, scope, s_set) if f in fs.scope and phrase_in(ref, s) else set()
            if f in fs.stale and F.subject_kind(f) in ("offer", "package", "event"):
                s_find.append(_finding(s, "conflict_or_expired", f, _quote(f), f"{ref} is {_why(fs, f)}"))
            elif clash:
                s_find.append(_finding(s, "wrong_scope", f, _quote(f),
                                       f"{ref} with {', '.join(sorted(clash))}: {ref} is {_why(fs, f)}"))
            else:
                s_find.append(_finding(s, "review", f, _quote(f), f"mentions {ref}, which is {_why(fs, f)}"))
            flagged.add(f["key"])
        findings += _settle(_dedupe(s_find), fs, by_key)

    # 7. disclosures of every fact used: reported on every sentence that uses it (one piece, one
    #    blocking decision), so the reviewer sees each place that needs the words
    piece_tokens = _tokens(_disc_canon(text))
    for k, sentences in strong_used.items():
        f = by_key.get(k)
        disc = [str(d) for d in (f or {}).get("required_disclosures") or [] if str(d).strip()]
        if not disc:
            continue
        if any(_tokens(_disc_canon(d)) <= piece_tokens or disclosure_said(d, text) for d in disc):
            continue
        for sentence in sentences or [""]:
            findings.append(_finding(sentence or text.strip()[:200], "missing_disclosure", f, disc[0],
                                     "add: " + " / ".join(f'"{d}"' for d in disc)))
    return _light_lines(findings, all_facts), list(used)


# A short question or a short speaker / caption line ("VO: Two A-boards?", "Caption: Autumn boxes",
# "Still on the fence?") names things but states nothing checkable. It never blocks unless it has a
# slot, an identifier, an exact value (money, percentage, quantity with a unit, date, time, code) or
# a fact's full name ("Is the Sausage Stack back?"): its blocking findings become "review".
SPEAKER_LABEL = re.compile(r"(?i)^\s*(?:vo|v/o|voice[- ]?over|narrator|caption|on[- ]screen(?:\s+text)?|"
                           r"text(?:\s+overlay)?|overlay|super|title\s+card|host|presenter|speaker\s*\d*)\s*:\s*")
ANY_LABEL = re.compile(r"^\s*[A-Za-z][\w /-]{0,20}:\s+")
SLOT_MARK = re.compile(r"\[\[|\{\{")
LIGHT_MAX_WORDS = 6


def light_line(s: str) -> bool:
    m = SPEAKER_LABEL.match(s)
    rest = s[m.end():] if m else s[ANY_LABEL.match(s).end():] if ANY_LABEL.match(s) else s
    rest = rest.strip()
    if not rest or len(re.findall(r"[\w'’-]+", rest)) > LIGHT_MAX_WORDS:
        return False
    return bool(m) or rest.endswith("?")


def _anchored(s: str, all_facts: list[dict]) -> bool:
    if SLOT_MARK.search(s) or identifiers(s) or extract(s):
        return True
    return any(len(F.subject_ref(f)) >= 4 and phrase_in(F.subject_ref(f), s) for f in all_facts)


def _light_lines(findings: list[dict], all_facts: list[dict]) -> list[dict]:
    memo: dict[str, bool] = {}
    for x in findings:
        if not x.get("blocking"):
            continue
        s = x["sentence"]
        if s not in memo:
            memo[s] = light_line(s) and not _anchored(s, all_facts)
        if memo[s]:
            x["detail"] = f"{x['label']} not blocking on a short question / label: {x['detail']}"
            x["label"], x["blocking"] = "review", False
    return findings


# Equivalent ways to write the same disclosure concept. Each concept is rewritten to one token (on
# the fact's disclosure AND on the copy) before the word comparison, so "plus VAT" = "excl. VAT",
# "£95pp" = "per person", "once we've surveyed" = "after a survey". Curated for precision: every
# pattern names the concept itself; the short forms that mean something else on their own ("pm" is
# also a time, "each" / "a night" / "monthly" are ordinary words) only count straight after an amount
# (a currency sign and a number). Opposites stay apart: incl. VAT and excl. VAT are different tokens,
# and a negated form ("no survey", "without a survey", "no minimum term") is never rewritten. A
# "survey of 120 clients" (a result) and "£49 each way" are not rewritten either. A group named
# `keep` is kept in front of the token (the amount before "pm"), groups `n` / `u` / `adj` after it.
#
# concept                 written as (either side)
# excl. VAT               excl. / excluding / exclusive of / ex / plus / + / before VAT, VAT extra /
#                         on top / not included / excluded
# incl. VAT               incl. / including / inclusive of / inc VAT, VAT included / inclusive
# per person              per person / guest / head / adult / pax, each guest / person / adult,
#                         p.p.; after an amount: pp, p/p, a head, each (not "each way")
# per night/month/year    per night / month / calendar month / year / annum; after an amount:
#                         /night, a night, nightly; /month, /mo, pm, pcm, a month, monthly; /yr,
#                         p.a., a year, annually, yearly
# from (a price)          from / starting at / prices start at / as little as, before a currency sign
# subject to survey       subject to / after / once / following / upon (we've) (a) (free|home|site)
#                         survey(ed), with an optional "(final) price/quote/figure confirmed/fixed"
# subject to status       subject to (credit) status, depending on (your) status, status permitting
# subject to availability subject to / depending on availability, availability permitting
# while stocks last       while stock(s) / supplies last(s)
# terms apply             terms (and conditions) / T&Cs / Ts&Cs / T+Cs / conditions apply
# minimum term            12-month minimum (term), minimum term of 12 months, min. 12 months
_AMT = (r"(?P<keep>[£$€]\s?\d[\d,]*(?:\.\d+)?(?:k\b)?"          # the amount a short form follows
        r"|\b(?:pounds|quid|dollars|euros))")                     # ... or one in words ("ninety-five pounds")
_NUMW = r"\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|eighteen|twenty-four|thirty-six"
_TERM_UNIT = r"month|year|week|night|day|hour"
_TERM_NOUN = (r"(?:term|contract|commitment|stay|rental|hire|let|tenancy|membership|subscription|agreement|"
              r"sign[\s-]?up|booking|period)")
_TERM_VERB = (r"(?:stay(?:s|ing)?|keep(?:s|ing)?|kept|rent(?:s|ing)?|hire[sd]?|hiring|remain(?:s|ing)?|"
              r"sign(?:s|ed|ing)?[\s-]?up|commit(?:s|ted|ting)?|subscribe[sd]?|lock(?:s|ed)?\s+in|tie[sd]?\s+in)")
_DISC_EQUIV = [
    # VAT exclusive / inclusive ("VAT not included" before "VAT included")
    (r"(?:\bexcl\.?|\bexcluding|\bexclusive\s+of|\bex\.?|\bplus|\+|\bbefore)\s*vat\b|"
     r"\bvat\s+(?:is\s+)?(?:extra|on\s+top|not\s+included|excluded|payable\s+on\s+top)\b|"
     # "with VAT added on top", "VAT is charged on top", "we add VAT", "VAT to be added"
     r"(?<!\bno\s)\bvat\s+(?:is\s+|will\s+be\s+|gets\s+|to\s+be\s+)?(?:added(?:\s+on\s+top)?|"
     r"(?:charged|payable)\s+on\s+top)\b|\b(?<!\bnot\s)(?<!n't\s)(?<!n’t\s)(?:add|adds|adding)\s+(?:on\s+)?vat\b", "qexvat"),
    (r"(?:\bincl\.?|\bincluding|\binclusive\s+of|\binc\.?)\s*vat\b|\bvat\s+(?:is\s+)?(?:included|inclusive)\b", "qincvat"),
    # per person
    (r"\bper\s+(?:person|guest|head|adult|pax)\b|\beach\s+(?:guest|person|adult|diner|visitor)s?\b|"
     r"(?<![\w.])p\.p\.(?!\w)", "qperperson"),
    (_AMT + r"\s?(?:pp\b|p/p\b|a\s+head\b|each\b(?!\s+way\b))", "qperperson"),
    # per night / month / year
    (r"\bper\s+night\b", "qpernight"),
    (_AMT + r"\s?(?:/\s?night\b|a\s+night\b|nightly\b)", "qpernight"),
    (r"\bper\s+(?:calendar\s+)?month\b", "qpermonth"),
    (_AMT + r"\s?(?:/\s?(?:month|mo|mth)\b|p/?m\b|pcm\b|a\s+month\b|monthly\b)", "qpermonth"),
    (r"\bper\s+(?:year|annum)\b", "qperyear"),
    (_AMT + r"\s?(?:/\s?(?:year|yr)\b|p\.?a\.?(?![\w.])|a\s+year\b|annually\b|yearly\b)", "qperyear"),
    # a price "from" (only before an amount: "from order confirmation" is another concept)
    (r"\b(?:from|starting\s+(?:at|from)|prices?\s+start(?:ing)?\s+(?:at|from)|start(?:s|ing)?\s+(?:at|from)|"
     r"as\s+little\s+as)(?=\s+(?:(?:just|only)\s+)?[£$€])", "qfrom"),
    # subject to a survey / status / availability; while stocks last
    (r"(?:\b(?:final\s+|exact\s+)?(?:price|quote|cost|figure|amount|fee)s?\s+(?:is\s+|are\s+|will\s+be\s+|gets?\s+)?"
     r"(?:confirmed|fixed|agreed|finali[sz]ed|set)\s+)?\b(?:subject\s+to|after|once|following|upon)\s+"
     r"(?:we(?:'ve|’ve|\s+have)?\s+)?(?:(?:a|an|the|our|your)\s+)?(?P<adj>(?:(?:free|home|site|full)\s+){0,2})"
     r"survey(?:ed|ing)?\b(?!\s+of\b)", "qsurvey"),
    (r"\bsubject\s+to\s+(?:your\s+)?(?:credit\s+)?status\b|\b(?:depending|dependent|based)\s+on\s+(?:your\s+)?"
     r"(?:credit\s+)?status\b|\bstatus\s+permitting\b|\bsubject\s+to\s+(?:a\s+)?credit\s+(?:check|status)\b", "qstatus"),
    (r"\bsubject\s+to\s+availability\b|\b(?:depending|dependent|based)\s+on\s+availability\b|"
     r"\bavailability\s+permitting\b|\bwhile\s+availability\s+lasts\b", "qavail"),
    (r"\bwhile\s+(?:stocks?|supplies)\s+lasts?\b", "qstocklast"),
    # not refundable: "non-refundable" = "no refunds" = "which we can't refund" = "won't be refunded"
    # ("refundable" / "fully refundable" on their own are the opposite and are left alone)
    (r"\bnon[\s-]?(?:refundable|returnable)\b|\bnot\s+(?:be\s+)?refund(?:able|ed)\b|\bno\s+refunds?\b|"
     r"\b(?:can(?:'|’)?t|cannot|can\s+not|won(?:'|’)?t|will\s+not|do(?:es)?n(?:'|’)?t|do(?:es)?\s+not|"
     r"is(?:n(?:'|’)?t|\s+not)|are(?:n(?:'|’)?t|\s+not))\s+(?:be\s+)?refund(?:ed|able)?\b", "qnorefund"),
    # terms apply
    (r"\b(?:terms(?:\s+(?:and|&)\s+conditions)?|t\s?s?\s?(?:&|\+|and)\s?cs?|conditions)\s+apply\b", "qtermsapply"),
    # minimum term: "12-month minimum term" = "minimum term of 12 months" = "min. 12 months"; the
    # thing held for that long is part of the concept ("minimum 12-week stay", "2-night minimum stay")
    (r"\b(?P<n>" + _NUMW + r")[\s-]*(?P<u>" + _TERM_UNIT + r")s?\s+min(?:imum|\.)?(?:\s+" + _TERM_NOUN + r")?\b",
     "qminterm"),
    (r"\bmin(?:imum|\.)?\s+" + _TERM_NOUN + r"\s+(?:of\s+)?(?P<n>" + _NUMW + r")[\s-]*(?P<u>" + _TERM_UNIT + r")s?\b",
     "qminterm"),
    (r"\bmin(?:imum|\.)?\s+(?:of\s+)?(?P<n>" + _NUMW + r")[\s-]*(?P<u>" + _TERM_UNIT + r")s?"
     r"(?:\s+" + _TERM_NOUN + r")?\b", "qminterm"),
    # ... said as how long the customer commits: "stay with us for at least 12 weeks", "keep the unit
    # for 12 weeks or longer", "sign up for a year at a time", "a one-year sign-up". Only after a
    # verb of holding / committing ("book at least 2 days ahead" is a notice period, not a term).
    (r"(?P<keep>\b" + _TERM_VERB + r"\b(?:\s+[\w'’]+){0,4}?\s+(?:for\s+)?)(?:at\s+least|a\s+minimum\s+of|"
     r"no\s+(?:less|fewer)\s+than|not\s+less\s+than)\s+(?P<n>" + _NUMW + r"|an?)[\s-]*(?P<u>" + _TERM_UNIT +
     r")s?\b(?!\s+(?:ahead|before|in\s+advance|notice)\b)", "qminterm"),
    # ... said as how long each one lasts: "each visit is at least two hours long", "sessions last at
    # least 45 minutes" (a length, never a notice period: "at least 2 days ahead" stays apart)
    (r"\bat\s+least\s+(?P<n>" + _NUMW + r"|an?)[\s-]*(?P<u>" + _TERM_UNIT + r")s?\s+(?:long|in\s+length)\b", "qminterm"),
    (r"(?P<keep>\b(?:last(?:s|ing)?|run(?:s|ning)?)\s+(?:for\s+)?)at\s+least\s+(?P<n>" + _NUMW + r"|an?)[\s-]*(?P<u>" +
     _TERM_UNIT + r")s?\b(?!\s+(?:ahead|before|in\s+advance|notice)\b)", "qminterm"),
    (r"(?P<keep>\b" + _TERM_VERB + r"\b(?:\s+[\w'’]+){0,4}?\s+(?:for\s+)?)(?P<n>" + _NUMW + r")(?:[\s-]*(?P<u>" +
     _TERM_UNIT + r")s?\s+or\s+(?:more|longer|over|above)\b|\+[\s-]*(?P<u2>" + _TERM_UNIT + r")s?\b)", "qminterm"),
    (r"\b(?:sign(?:s|ed|ing)?[\s-]?up|commit(?:s|ted|ting)?|lock(?:s|ed)?\s+in|tie[sd]?\s+in|contract(?:s|ed)?)\s+"
     r"(?:(?:with\s+us|to\s+us|in)\s+)?(?:for|to)\s+(?P<n>" + _NUMW + r"|an?)[\s-]*(?P<u>" + _TERM_UNIT + r")s?"
     r"(?:\s+at\s+a\s+time)?\b", "qminterm"),
    (r"\b(?P<n>" + _NUMW + r"|an?)[\s-]+(?P<u>" + _TERM_UNIT + r")s?[\s-]+(?:sign[\s-]?ups?|commitments?|tie[\s-]?ins?|"
     r"lock[\s-]?ins?)\b", "qminterm"),       # not "a 12-month contract": its length, not a minimum
    # not part of the price: "frames not included" = "frames extra" = "frames charged separately" =
    # "excluding frames" = "an extra charge for matted fur". "extra" on its own is a concept only at
    # the end of a clause ("(frames extra)", "matted coats cost extra"), never "an extra pair".
    (r"\bnot\s+includ(?:ed|ing)\b|\bexclud(?:ed|ing)\b|\bexclusive\s+of\b|"
     r"\b(?:charged|sold|priced|billed|paid(?:\s+for)?|bought|purchased)\s+separately\b|"
     # "buy the coursebook separately": the word on its own (the thing named before it is kept)
     r"(?<!\bnot\s)\bseparately\b|"
     r"\b(?:an?\s+)?(?:extra|additional|separate)\s+(?:purchase|buy)\b|"
     r"\b(?:bought|purchased|paid\s+for|charged|billed|added)\s+on\s+top\b|"
     r"\b(?:at|for)\s+(?:an\s+)?(?:extra|additional)\s+(?:cost|charge|fee)\b|"
     r"\b(?:an?\s+)?(?:extra|additional)\s+(?:charge|cost|fee)s?\s+(?=(?:for|on)\b)|"
     r"\b(?:(?:cost|costs|is|are|charged|priced)\s+)?(?:extra|additional)\b(?=\s*(?:[).,;:!?\n]|$))", "qexcl"),
    # paid in advance: "upfront" = "up front" = "in advance" = "beforehand"; "prepaid" = "paid in advance"
    (r"\bpre[\s-]?pa(?:id|y)\b", "pay qadvance"),
    (r"\bin\s+advance\b|\bup[\s-]?front\b|\bbeforehand\b|\bahead\s+of\s+time\b", "qadvance"),
    # a deadline: "by 12 noon" = "before midday" = "no later than 12pm" (the time itself is rewritten
    # to "qat HHMM" first)
    (r"\b(?:by|before|no\s+later\s+than|not\s+later\s+than|ahead\s+of)\s+(?=qat\b)", "qby"),
    # round 12: contents that change: "contents vary with the season" = "what goes in changes with the seasons"
    (r"\b(?:contents?|what(?:['’]s|\s+goes|\s+is|\s+comes)\s+in(?:side)?(?:\s+(?:it|the\s+box))?)\s+"
     r"(?:vary|varies|change|changes|rotates?)\b", "qcontentvary"),
    # round 12: a minimum age: "adults 18 and over" = "aged 18 or over" = "18+" = "(18+)" = "over-18s"
    (r"(?:\b(?:adults?|aged|people|anyone|customers?|patients?|those|guests?)\s+)?\(?\s*(?P<n>\d{1,2})\s*"
     r"(?:\+(?=\s*(?:$|[),.;:!?/]|only\b|welcome\b))|(?:years?\s+)?(?:and|or)\s+(?:over|older|above|upwards)\b)\s*\)?|"
     r"\bover[\s-](?P<n2>\d{1,2})s\b", "qminage"),
    # "if you're eligible" = "if you qualify" = "anyone who qualifies" = "those who meet the criteria"
    (r"\bif\s+(?:you(?:'re|’re|\s+are)\s+)?eligible\b|\b(?:if|when|provided|as\s+long\s+as)\s+you\s+"
     r"(?:qualify|meet\s+the\s+(?:\w+\s+)?criteria)\b|\b(?:anyone|those|people|patients|customers|you)\s+who\s+"
     r"(?:qualify|qualifies|(?:is|are)\s+eligible|meets?\s+the\s+(?:\w+\s+)?criteria)\b", "qeligible"),
    # Monday to Friday: "Mon–Fri" = "weekdays" = "on a weekday" = "Monday to Friday"
    (r"\bmon(?:day)?s?\.?\s*(?:-|–|—|to|through|thru|till|until)\s*fri(?:day)?s?\b|\bweekdays?\b", "qweekday"),
    # new customers: "for new students" = "newcomers" = "first-time customers" = "if you haven't been
    # to us before" = "if you're new to us" (the kind of customer is the business's own word for it)
    (r"\bnew\s+(?:students?|customers?|clients?|patients?|members?|pupils?|learners?)\b|\bnewcomers?\b|"
     r"\bfirst[\s-]time\s+(?:students?|customers?|clients?|patients?|members?|pupils?|learners?|visitors?|buyers?)\b|"
     r"\b(?:if|when)\s+you\s+(?:haven['’]t|have\s+not|have\s+never|['’]ve\s+never)\s+(?:been|studied|learned|trained|"
     r"booked|shopped|stayed)\s+(?:to|with|at)\s+us\s+before\b|\b(?:if|when)\s+you(?:['’]re|\s+are)\s+new\s+"
     r"(?:to\s+us|here)\b", "qnewcust"),
    # a confirmed booking: "with a confirmed booking" = "once you've booked" = "book with us and get"
    (r"\bwith\s+a\s+confirmed\s+booking\b|\b(?:once|when|after|as\s+soon\s+as)\s+you(?:['’]ve|\s+have)?\s+"
     r"(?:booked|confirmed\s+(?:your|a)\s+booking)\b|\bonce\s+your\s+booking\s+is\s+confirmed\b|"
     r"\bbook\s+(?:with\s+us\s+)?and\s+(?:you['’]ll\s+)?(?:get|receive)\b", "qbooked"),
    # an age limit: "under 6 months" = "younger than six months" = "below the age of 6 months"
    (r"\b(?:aged\s+)?(?:under|below|younger\s+than|less\s+than)(?:\s+the\s+age\s+of)?\s+"
     r"(?=(?:" + _NUMW + r")[\s-]*(?:months?|years?|weeks?|yrs?)\b)", "qunder"),
    (r"\b(?:aged\s+)?(?:over|above|older\s+than)(?:\s+the\s+age\s+of)?\s+"
     r"(?=(?:" + _NUMW + r")[\s-]*(?:months?|years?|yrs?)\b)", "qover"),
]
_DISC_EQUIV = [(re.compile(p, re.I), tok) for p, tok in _DISC_EQUIV]
# Plain rewrites before the concepts: a period said in other units ("within a month" = "within 30
# days", "a fortnight" = "14 days") and a named part of a year ("the first half of 2026" = "January
# to June 2026", "Q3 2026" = "July to September 2026").
_HALF = {"first": "january to june", "second": "july to december", "1": "january to june", "2": "july to december"}
_QTR = {"1": "january to march", "2": "april to june", "3": "july to september", "4": "october to december",
        "first": "january to march", "second": "april to june", "third": "july to september",
        "fourth": "october to december"}
def _time_tok(h: int, mi: int, ap: str) -> str:
    ap = (ap or "").replace(".", "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f" qat {h:02d}{mi:02d} " if h <= 23 and mi <= 59 else f" {h} {mi} {ap} "


_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_TEENS = {"thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
          "nineteen": 19}
_UNITS_W = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
NUM_WORD_RX = re.compile(r"\b(?P<t>" + "|".join(_TENS) + r")(?:[\s-](?P<u>" + "|".join(_UNITS_W) + r"))?\b|"
                         r"\b(?P<teen>" + "|".join(_TEENS) + r")\b", re.I)


def _num_word(m: re.Match) -> str:
    if m.group("teen"):
        return str(_TEENS[m.group("teen").lower()])
    return str(_TENS[m.group("t").lower()] + (_UNITS_W[m.group("u").lower()] if m.group("u") else 0))


_DISC_NORM = [
    # numbers in words past twelve: "thirty" = "30", "twenty-five" = "25" (one to twelve are compared
    # as words already)
    (NUM_WORD_RX, _num_word),
    # a maximum said in other words: "as many as 30" = "a maximum of 30" = "no more than 30" = "up to 30"
    (re.compile(r"\b(?:as\s+many\s+as|a\s+maximum\s+of|maximum\s+of|no\s+more\s+than|not\s+more\s+than|at\s+most|"
                r"max(?:imum|\.)?)(?=\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b)", re.I),
     "up to"),
    # round 12: free said otherwise: "which costs nothing" = "free of charge" = "at no cost" = "free"
    (re.compile(r"\b(?:which\s+|that\s+)?costs?\s+(?:you\s+)?nothing\b|\bfree\s+of\s+charge\b|\bat\s+no\s+cost\b", re.I),
     " free "),
    # opening hours as a day and a bare range: "weekdays 8–6" = "weekdays 8am–6pm", "Sat 9-1" = "Sat
    # 9am–1pm" (only when the range crosses noon: the second hour is not later than the first)
    (re.compile(r"\b(?P<day>(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?|weekdays?|weekends?|daily)\s*,?\s+(?:from\s+)?"
                r"(?P<a>\d{1,2})\s*(?:-|–|—|to|till|until)\s*(?P<b>\d{1,2})\b(?![\d:%]|\.\d|\s*[ap]\.?m)", re.I),
     lambda m: (f"{m.group('day')} {m.group('a')}am {int(m.group('b'))}pm"
                if int(m.group("b")) < int(m.group("a")) <= 12 else m.group(0))),
    # times of day: "12 noon" = "midday" = "12pm" = "12:00"; "5.30pm" = "17:30" (never after a currency
    # sign: "£30pm" is per month)
    (re.compile(r"\b(?:12\s*)?(?:noon|midday)\b", re.I), " qat 1200 "),
    (re.compile(r"\bmidnight\b", re.I), " qat 0000 "),
    (re.compile(r"(?<![£$€\d.,:])(?<![£$€]\s)\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap]\.?m\.?)(?![\w])", re.I),
     lambda m: _time_tok(int(m.group(1)), int(m.group(2) or 0), m.group(3))),
    (re.compile(r"(?<![£$€\d.,:])(?<![£$€]\s)\b([01]?\d|2[0-3]):([0-5]\d)\b(?![:.]\d)"),
     lambda m: _time_tok(int(m.group(1)), int(m.group(2)), "")),
    # "sign off (on) the proof" = "approve the proof"
    (re.compile(r"\bsign(?:s|ed|ing)?[\s-]?off(?:\s+on)?\b", re.I), "approved"),
    (re.compile(r"\b(within|in|inside|under)\s+(?:a|one|1)\s+month\b", re.I), r"\1 30 days"),
    (re.compile(r"\b(within|in|inside|under)\s+(?:a|one|1)\s+fortnight\b", re.I), r"\1 14 days"),
    (re.compile(r"\b(within|in|inside|under)\s+(?:a|one|1)\s+week\b", re.I), r"\1 7 days"),
    (re.compile(r"\b(within|in|inside|under)\s+(?:two|2)\s+weeks\b", re.I), r"\1 14 days"),
    (re.compile(r"\b(?:the\s+)?(first|second)\s+half\s+of\s+(?:the\s+year\s+)?(\d{4})\b", re.I),
     lambda m: f"{_HALF[m.group(1).lower()]} {m.group(2)}"),
    (re.compile(r"\bH([12])\s+(\d{4})\b"), lambda m: f"{_HALF[m.group(1)]} {m.group(2)}"),
    (re.compile(r"\bQ([1-4])\s+(\d{4})\b"), lambda m: f"{_QTR[m.group(1)]} {m.group(2)}"),
    (re.compile(r"\b(?:the\s+)?(first|second|third|fourth)\s+quarter\s+of\s+(\d{4})\b", re.I),
     lambda m: f"{_QTR[m.group(1).lower()]} {m.group(2)}"),
]
DISC_CONCEPTS = frozenset(w for _, tok in _DISC_EQUIV for w in tok.split() if w.startswith("q")) | {"qat"}


def _term_len(n: str, u: str) -> list[str]:
    """A minimum term's length in one unit: "a year" = "one-year" = "12 months"."""
    n = (n or "").lower()
    k = 1 if n in ("a", "an") else NUMBER_WORDS.get(n) or {"eighteen": 18, "twenty-four": 24, "thirty-six": 36}.get(n)
    if k is None and n.isdigit():
        k = int(n)
    u = (u or "").lower()
    if k is None:
        return [n, u]
    if u == "year":
        k, u = k * 12, "month"
    return [str(k), u]


def _disc_sub(tok: str):
    def rep(m: re.Match) -> str:
        g = m.groupdict()
        out = [g["keep"]] if g.get("keep") else []
        out.append(tok)
        if tok == "qminterm":
            out += _term_len(g.get("n"), g.get("u") or g.get("u2"))
        else:
            out += [g[k] for k in ("n", "n2", "u", "adj") if g.get(k)]
        return " " + " ".join(out) + " "
    return rep


def _disc_canon(s: str) -> str:
    for rx, rep in _DISC_NORM:
        s = rx.sub(rep, s or "")
    for rx, tok in _DISC_EQUIV:
        s = rx.sub(_disc_sub(tok), s or "")
    return s


# A disclosure said in other words: its content words (numbers, named terms, key nouns), in any
# order and inflection, with filler words ignored: "when registered within 30 days of delivery" =
# "just register it within 30 days of delivery"; "146 first-time tests, Sep 2025 to Aug 2026" =
# "146 first-time tests taken between September 2025 and August 2026". A content word the piece
# only says negated ("not registered") does not count, and "incl. VAT" is never "excl. VAT".
DISC_FILLER = frozenset("""a an the and or of to is are be been it its it's this that these those when whenever if
once just simply between from on in at for by with your you our we us as so then there here any all
taken made applies apply based cohort sample intake""".split())
DISC_NEG = frozenset("no not without never non excl excluding excludes except".split())
_MONTH_TOK = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
              "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
              "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}


_PAY_FORMS = frozenset("paid payment payments payable".split())
# plain synonyms a disclosure is often said in: "up to 30 kids" = "up to 30 children"; "each visit" =
# "every visit" = "per visit"
_DSYN = {"kid": "child", "kids": "child", "children": "child", "each": "per", "every": "per"}


def _dstem(w: str) -> str:
    if w in DISC_CONCEPTS:
        return w
    if w in _DSYN:
        return _DSYN[w]
    if w in _PAY_FORMS:
        return "pay"                   # "paid in advance" = "pay upfront" = "payment in advance"
    if w in _MONTH_TOK:
        return f"m{_MONTH_TOK[w]}"
    if w in NUMBER_WORDS and w not in ("a", "an", "single"):
        return str(NUMBER_WORDS[w])
    if w.isdigit() or w in DISC_NEG:
        return w
    if len(w) >= 10 and w.endswith("ation"):
        return _dstem(w[:-5])        # "installation" -> "install", "registration" -> "registr"
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ied"):
        w = w[:-3] + "y"
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouls":
        w = w[:-1]                     # "planned" -> "plan"
    return w


def _dwords(text: str) -> list[str]:
    t = _disc_canon(text or "").lower().replace("’", "'")
    return [_dstem(w) for w in re.findall(r"[a-z]+|\d+(?:[.,]\d+)*", t)]


def disclosure_said(disclosure: str, text: str) -> bool:
    filler = {_dstem(w) for w in DISC_FILLER} | DISC_FILLER
    need = [w for w in _dwords(disclosure) if w not in filler]
    if len(need) < 2 and not (need and need[0] in DISC_CONCEPTS):     # one concept ("plus VAT") is enough
        return False
    have = _dwords(text)
    negs = {w for w in need if w in DISC_NEG}
    exact = [w for w in need if w not in DISC_NEG and w in have]
    fuzzy = 0
    missing = []
    for w in need:
        if w in DISC_NEG:
            if w not in have:
                return False
            continue
        idx = [i for i, x in enumerate(have) if x == w]
        if not idx and len(exact) >= 2 and not fuzzy:
            # one clipped or compound word, when the rest is said exactly: "pups" for "puppies",
            # "daytime guests" for "day guests"
            idx = [i for i, x in enumerate(have) if _clipped(w, x)]
            fuzzy = len(idx) > 0
        if not idx:
            missing.append(w)
            continue
        if not negs and all(any(x in DISC_NEG for x in have[max(0, i - 3):i]) for i in idx):
            return False               # "not registered": said only negated
    return not missing or (len(missing) == 1 and not fuzzy and (_deadline_said(need, have, missing[0])
                                                                 or _mode_said(disclosure, text, missing[0])))


MODE_NOUNS = frozenset("mode setting".split())


def _mode_said(disclosure: str, text: str, missing: str) -> bool:
    """A mode named without the word "mode": "riding in eco" = "in eco mode", "on the turbo setting"
    = "in turbo mode". Only after in / on / using, and never as part of a longer word ("eco-friendly")."""
    if missing not in {_dstem(w) for w in MODE_NOUNS}:
        return False
    m = re.search(r"(?i)\b([a-z][\w-]*)\s+(?:mode|setting)s?\b", disclosure)
    if not m or m.group(1).lower() in DISC_FILLER:
        return False
    return re.search(r"(?i)\b(?:in|on|using|into)\s+(?:the\s+|its\s+|their\s+|your\s+)?" + re.escape(m.group(1)) +
                     r"(?![\w-])", text) is not None


def _deadline_said(need: list[str], have: list[str], missing: str) -> bool:
    """A deadline ("artwork approved by 12 noon") said with the same time and one other word of it,
    the thing or the action named otherwise ("sign off your proof before midday"): the deadline and
    at least one other word of it are said, only one plain word is not."""
    dl = [need[i:i + 3] for i in range(len(need) - 2) if need[i] == "qby" and need[i + 1] == "qat"]
    if not dl or not missing.isalpha() or missing in DISC_CONCEPTS:
        return False
    if not any(have[i:i + 3] == d for d in dl for i in range(len(have) - 2)):
        return False
    return any(w.isalpha() and w not in DISC_CONCEPTS and w not in DISC_NEG and w != missing and w in have
               for w in need)


def _clipped(a: str, b: str) -> bool:
    """One word is the other with at most four letters more on the end: pup / puppy, day / daytime."""
    if not (a.isalpha() and b.isalpha()) or a == b or a in DISC_CONCEPTS or b in DISC_CONCEPTS:
        return False
    short, long = sorted((a, b), key=len)
    return len(short) >= 3 and long.startswith(short) and len(long) - len(short) <= 4


_NEG_RX = re.compile(r"\b(?:no|not|without|excluding|excludes|never)\s+(?:a\s+|an\s+|any\s+)?([a-z][a-z-]{2,})", re.I)


def _negated(text: str) -> set[str]:
    """Words a fact says are NOT part of it: "room only (no breakfast)" -> breakfast. A word the
    fact also states without a negator ("X is Gold certified. Y is not certified.") is affirmed for
    the fact's own subject; the negation is about something else, so it is not returned."""
    out = set()
    for m in _NEG_RX.finditer(text or ""):
        for w in F.words(m.group(1)):
            w = _stem(w)
            if len(w) >= 3 and w not in ID_STOP and w not in WEAK and w not in UNIT_WORDS:
                out.add(w)
            break
    return {w for w in out if not _affirmed_in(text, w)}


def _affirmed_in(text: str, word: str) -> bool:
    """Some occurrence of `word` in the text has no negator in the three words before it."""
    words = [_stem(w) for w in F.words(text)]
    return any(w == word and not any(x in NEGATOR for x in words[max(0, i - 3):i]) for i, w in enumerate(words))


def _negated_in(sentence: str, word: str) -> bool:
    words = [_stem(w) for w in F.words(sentence)]
    for i, w in enumerate(words):
        if w == word and any(x in NEGATOR for x in words[max(0, i - 3):i]):
            return True
    return False


SHORT_CLASS = re.compile(r"(?<![\w-])[Cc]lass[ \t]+(?P<g>[A-F][1-3]?)(?![\w-])")


def short_class_grades(s: str, have: list, schemes: set, grades: dict, fs: "FactSets") -> list:
    """"Class A" said for a scheme whose name ends in "class" ("Euroclass B-s1,d0"), when the sentence
    shares a word with a valid fact graded on that scheme ("fire classification, Class A")."""
    long = [x for x in schemes if isinstance(x, str) and x.lower().endswith("class") and x.lower() != "class"]
    out = []
    for m in SHORT_CLASS.finditer(s):
        if any(g.start <= m.start() < g.end for g in have):
            continue
        for sc in long:
            tied = [f for f in fs.ok if any(x.scheme == sc for x in grades.get(f["key"], []))
                    and _overlap(s, f, {"class", "classification", sc.lower()}) >= 1]
            if tied:
                out.append(Grade(sc, m.group("g").upper(), "", m.start(), m.end(), m.group(0)))
                break
    return out


def _grade_finding(fs: FactSets, grades: dict, s: str, sg: Grade, use, veto: set, t_grades=None) -> list[dict]:
    """A scheme + grade in copy ("Level 3", "EPC A", "AEO-S") against the facts that use the same
    scheme: the same grade matches (and counts as a use, so its disclosures apply); another grade
    than an in-scope valid fact's conflicts. A generic scheme word (Level, Class, Type...) needs the
    sentence to be about that fact too (a shared word or its subject)."""
    def same(f, extra=False):
        return any(x.scheme == sg.scheme and x.grade == sg.grade and (not sg.suffix or sg.suffix == x.suffix)
                   for x in grades.get(f["key"], []) + ((t_grades or {}).get(f["key"], []) if extra else []))

    def scheme_of(f):
        return any(x.scheme == sg.scheme for x in grades.get(f["key"], []))
    ok = [f for f in fs.ok if same(f)]
    if ok:
        f = _best(ok, s)
        use(f["key"], s)
        return [_finding(s, "match", f, _quote(f), f"{sg.text} = {f['key']}")]
    generic = sg.scheme in GENERIC_SCHEMES

    def loose(f):      # a grade that is only part of the fact's name ("A2" of "A2 Intensive"): WEAK
        return _name_part(Val("code", (), sg.text, 0, 0), f, s)
    sc = [f for f in fs.scope if same(f)]
    if sc:
        f = _best(sc, s)
        veto.add(f["key"])
        return [_finding(s, "wrong_scope", f, _quote(f), f"{sg.text} is {_why(fs, f)}", weak=loose(f))]
    st = [f for f in fs.stale if same(f)]
    if st:
        f = _best(st, s)
        veto.add(f["key"])
        return [_finding(s, "conflict_or_expired", f, _quote(f), f"{sg.text} is from a fact that is {_why(fs, f)}",
                         weak=loose(f))]
    if not any(scheme_of(f) for f in fs.ok):
        # a valid in-scope fact that names the same grade in its own sentence or name ("MOT test
        # (Class 4)"): the grade is no evidence for another scope's fact
        own = [f for f in fs.ok if same(f, extra=True)]
        if own:
            f = _best(own, s)
            use(f["key"], s, False)
            return [_finding(s, "match", f, _quote(f), f"{sg.text} named by {f['key']}", weak=True)]
        # "Tier 2 pricing" when only an out-of-scope / expired fact's sentence names Tier 2
        for pool, label in ((fs.scope, "wrong_scope"), (fs.stale, "conflict_or_expired")):
            hit = [f for f in pool if same(f, extra=True)]
            if hit:
                f = _best(hit, s)
                veto.add(f["key"])
                return [_finding(s, label, f, _quote(f) or f.get("text"), f"{sg.text} is {_why(fs, f)}",
                                 weak=loose(f))]
    # "Class Two" written as a name (capitalised, in words) when only one fact, a certification or
    # credential, grades on that scheme: it is that fact's scheme even with nothing else to tie it
    graders = [k for k, gl in grades.items() if any(x.scheme == sg.scheme for x in gl)]
    named_alone = (sg.words and sg.text[:1].isupper() and sg.text.split()[-1][:1].isupper() and len(graders) == 1)
    other = [f for f in fs.ok if scheme_of(f) and (not generic or _subject_named(f, s)
                                                   or _overlap(s, f, {sg.scheme.lower()}) >= 1
                                                   or (named_alone and f["key"] == graders[0]
                                                       and f.get("fact_type") in IDENTITY_TYPES))]
    if not other:
        return []
    f = _best(other, s)
    veto.add(f["key"])
    held = ", ".join(sorted({f"{x.scheme} {x.grade}{x.suffix}" for x in grades[f["key"]] if x.scheme == sg.scheme}))
    return [_finding(s, "conflict_or_expired", f, _quote(f), f"{sg.text} differs from the fact: {held}")]


def _cap_tail(text: str, kw: str) -> str:
    """The capitalised name right after a claim word: "accredited General Practice" -> "General Practice"."""
    m = re.search(r"(?i:" + re.escape(kw) + r")[ \t]+((?:[A-Z][\w'&-]*[ \t]*){1,4})", text)
    return m.group(1).strip() if m else ""


def _identity_finding(fs: FactSets, s: str, kw: str, ids: set[str], use) -> list[dict]:
    """A certification / approval / partner claim is supported only by a fact naming the same
    standard, number or body: ISO 14001 is not ISO 9001, SOC 2 Type II is not Type I."""
    name = " ".join(sorted(ids))

    def names(f):
        return ids <= fact_ids(f)

    business = set().union(*(id_tokens(F.subject_ref(g)) for g in fs.ok + fs.scope + fs.stale
                             if F.subject_kind(g) == "business"))

    def value_names(f):
        # "EU Ecolabel certified" backed by a fact whose own value is "EU Ecolabel cleaning products":
        # the fact states that very mark (not just the business's name), and does not deny it
        vt = " ".join(x for x in [f.get("value_text") or "", *F.phrases(f.get("allowed_phrasing")),
                                  f["value"] if isinstance(f.get("value"), str) else ""] if x)
        return (bool(ids - business) and ids <= id_tokens(vt)
                and not re.search(r"(?i)\b(?:not|no|never|non|without)\b", vt))

    def eligible(f):
        return (f.get("fact_type") in IDENTITY_TYPES or (f.get("claim_class") or "none") in IDENTITY_CLASSES_F
                or phrase_in(kw, _blob(f)) or value_names(f))
    ok = [f for f in fs.ok if eligible(f) and names(f)]
    if ok:
        f = _best(ok, s)
        mine, theirs = _cap_tail(s, kw), _cap_tail(f.get("value_text") or "", kw)
        if mine and theirs and not (set(F.words(mine)) & set(F.words(theirs))):
            # "XYZ-accredited Advanced Clinic" when the fact says "... accredited Standard Clinic"
            return [_finding(s, "conflict_or_expired", f, _quote(f),
                             f'"{kw} {mine}" differs from the fact: {kw} {theirs}', blocking=True), "VETO"]
        use(f["key"], s)
        return [_finding(s, "match", f, _quote(f), f'"{kw}" ({name}) supported by {f["key"]}')]
    sc = [f for f in fs.scope if eligible(f) and names(f)]
    if sc:
        f = _best(sc, s)
        return [_finding(s, "wrong_scope", f, _quote(f), f'"{kw}" ({name}) is {_why(fs, f)}')]
    st = [f for f in fs.stale if eligible(f) and names(f)]
    if st:
        f = _best(st, s)
        return [_finding(s, "conflict_or_expired", f, _quote(f), f'"{kw}" ({name}) is from a fact that is {_why(fs, f)}')]
    # "Acme Platinum Partner" when the fact says "Acme Gold Partner": the same scheme, another tier
    tiers, rest = ids & TIER_WORDS, ids - TIER_WORDS
    if tiers and rest and any(len(w) >= 3 and not w.isdigit() for w in rest):
        # "Lexcel Gold accredited" when the fact says Lexcel v6.1 (no tiers): a tier the scheme's
        # fact does not name
        def other_tier(f):
            fi = fact_ids(f)
            return eligible(f) and rest <= fi and not tiers <= fi
        for pool, label in ((fs.ok, "conflict_or_expired"), (fs.scope, "wrong_scope"), (fs.stale, "conflict_or_expired")):
            hit = [f for f in pool if other_tier(f)]
            if hit:
                f = _best(hit, s)
                held = ", ".join(sorted(fact_ids(f) & TIER_WORDS)) or "no tier"
                return [_finding(s, label, f, _quote(f),
                                 f'"{kw}" ({name}): the fact names another tier of {" ".join(sorted(rest))}: {held}',
                                 blocking=True), "VETO"]
    return [_finding(s, "no_source", None, None, f'"{kw}" ({name}): no fact names this', blocking=True)]


def _rating_finding(fs: FactSets, s: str, start: int, kw: str, kind: str, use) -> list[dict]:
    """Ratings, awards, superlatives, rankings, customer counts: need a result / claim / testimonial
    (or comparative / result class) fact that says the same thing (same number, same thing)."""
    if kind == "award":
        # "the Accessible Coast Platinum award" when the fact says "Accessible Coast Gold award"
        before = RUN_BEFORE.search(s[:start])
        ids = id_tokens(before.group(1)) if before else set()
        tiers, rest = ids & TIER_WORDS, ids - TIER_WORDS
        if tiers and rest:
            for pool, label in ((fs.ok, "conflict_or_expired"), (fs.scope, "wrong_scope"), (fs.stale, "conflict_or_expired")):
                hit = [f for f in pool if rest <= fact_ids(f) and fact_ids(f) & TIER_WORDS and not tiers <= fact_ids(f)]
                if hit:
                    f = _best(hit, s)
                    held = ", ".join(sorted(fact_ids(f) & TIER_WORDS))
                    return [_finding(s, label, f, _quote(f), f'"{" ".join(sorted(tiers))} {kw}": the fact names '
                                     f'another tier of {" ".join(sorted(rest))}: {held}', blocking=True)]
    ok = [f for f in fs.ok if _rating_supports(f, kind, kw, s, start)]
    if ok:
        f = _best(ok, s)
        use(f["key"], s)
        return [_finding(s, "match", f, _quote(f), f'"{kw}" supported by {f["key"]}')]
    sc = [f for f in fs.scope if _rating_supports(f, kind, kw, s, start)]
    if sc:
        f = _best(sc, s)
        return [_finding(s, "wrong_scope", f, _quote(f), f'"{kw}" is {_why(fs, f)}')]
    st = [f for f in fs.stale if _rating_supports(f, kind, kw, s, start)]
    if st:
        f = _best(st, s)
        return [_finding(s, "conflict_or_expired", f, _quote(f), f'"{kw}" is from a fact that is {_why(fs, f)}')]
    if kind in RATING_KINDS_NUM and _rating_numbers(kw):
        # "rated Four for food hygiene" when the valid fact says food hygiene rating 5: the same
        # rating (a word of its topic besides the rating words) with another number
        rwords = {"rated", "rating", "ratings", "star", "stars", "score", "review", "reviews"}
        diff = [f for f in fs.ok if _rating_supports(f, kind, kw, s, start, any_number=True)
                and _overlap(s, f, rwords) >= 1 and (_rating_numbers(_blob(f)) - _numbers(" ".join(
                    re.findall(r"\d{4,}", _blob(f)))))]
        if diff:
            f = _best(diff, s)
            return [_finding(s, "conflict_or_expired", f, _quote(f), f'"{kw}" differs from the fact: {_quote(f)}')]
    before = F.words(s[:start])[-2:]
    market = re.search(r"(?i:\b(?:in|of|across|around)\s+(?:the\s+)?)(?:(?i:town|city|country|world|region|area|"
                       r"county|uk|us|industry|market)\b|[A-Z]\w+)", s[start:])
    if kind in OWN_RANGE and ("our" in before or "my" in before) and not market:
        return [_finding(s, "review", None, None, f'"{kw}": about your own range; check you can show it')]
    return [_finding(s, "no_source", None, None, f'"{kw}": no fact supports this rating / award / ranking',
                     blocking=True)]


_TIER_ALT = "|".join(w.capitalize() for w in sorted(TIER_WORDS))


def scheme_names(f: dict) -> set[str]:
    """Capitalised names a certification / credential fact's value uses ("Lexcel", "Investors"),
    not claim words, tiers or the business's own name."""
    if not (f.get("fact_type") in IDENTITY_TYPES or (f.get("claim_class") or "none") in IDENTITY_CLASSES_F):
        return set()
    own = set(F.words(F.subject_ref(f))) if F.subject_kind(f) in ("business", "site", "person") else set()
    src = " ".join(str(x) for x in (f.get("value_text"), f.get("value") if isinstance(f.get("value"), str) else None) if x)
    words = {w for w in re.findall(r"(?<![\w-])[A-Z][a-z]{3,}(?![\w-])", src)
             if w.lower() not in CLAIM_VERBS | ID_STOP | TIER_WORDS | own}
    # a body's acronym ("NFRC Full Contractor Member"): "NFRC Platinum" names a tier of it too
    acr = {w for w in re.findall(r"(?<![\w-])[A-Z]{3,6}(?![\w-])", src) if w not in NOT_ID_PREFIX}
    return words | acr


def _tier_name_findings(fs: "FactSets", names: dict, s: str, veto: set) -> list[dict]:
    """"Lexcel Gold standard" when the Lexcel fact names no Gold tier: a tier said with a scheme's name."""
    out = []
    live = [(f, pool) for pool in (fs.ok, fs.scope, fs.stale) for f in pool if names.get(f["key"])]
    for f, pool in live:
        for n in names[f["key"]]:
            m = re.search(r"(?<![\w-])" + re.escape(n) + r"[ \t]+(?P<t>" + _TIER_ALT + r")(?![\w-])|(?<![\w-])(?P<t2>" +
                          _TIER_ALT + r")[ \t]+" + re.escape(n) + r"(?![\w-])", s)
            if not m:
                continue
            tier = (m.group("t") or m.group("t2")).lower()
            if any(tier in fact_ids(g) for g, _ in live if n in names[g["key"]]) or pool is not fs.ok:
                continue
            veto.add(f["key"])
            out.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                f'"{m.group(0)}": the fact about {n} names no {tier} tier ({_quote(f)})'))
            break
    return out


def _scope_clash(f: dict, scope: dict, s_set: set) -> set[str]:
    """Words of the task's own scope values ("winter" of variant winter-weekday) that the sentence
    says and that none of the fact's values for that dimension has (summer-saturday, summer-weekday):
    "a winter wedding in the Tithe Barn" puts a summer-only venue into the task's winter package."""
    fs_, ts = F.norm_scope(f.get("scope")), F.norm_scope(scope)
    out = set()
    for d in F.DIMENSIONS:
        if not fs_[d] or not ts[d] or all(v in fs_[d] for v in ts[d]):
            continue
        theirs = {_stem(w) for v in fs_[d] for w in re.split(r"[\s_/-]+", v) if w}
        for v in ts[d]:
            for w in re.split(r"[\s_/-]+", v):
                if len(w) >= 4 and not w.isdigit() and _stem(w) not in theirs and _stem(w) in s_set \
                        and w not in F.STOP and w not in GENERIC:
                    out.add(w)
    return out


# ---------- a grade of a named register said with a name the facts never give it: "NESR Master
#            Contractor" when the NESR fact says its grades are Domestic Installer and Approved Contractor

_CAPRUN = r"[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){0,2}"


def _grade_name_findings(fs: "FactSets", s: str, veto: set) -> list[dict]:
    out = []
    for m in re.finditer(r"(?<![\w-])(?P<a>[A-Z]{2,6})[ \t]+(?P<run>" + _CAPRUN + r")(?![\w-])", s):
        acr, run = m.group("a"), m.group("run").split()
        head = _stem(run[-1].lower())
        if len(run) < 2 or acr in NOT_ID_PREFIX:
            continue
        said = [_stem(w.lower()) for w in run]
        for f in fs.ok:
            text = " ".join(str(x) for x in (f.get("value_text"), f.get("text")) if x)
            if f["key"] in veto or not re.search(r"(?<![\w-])" + acr + r"(?![\w-])", text) or not (
                    f.get("fact_type") in ("credential", "certification") or
                    (f.get("claim_class") or "none") in ID_CLASSES):
                continue
            names = [[_stem(w.lower()) for w in r.split()] for r in re.findall(r"(?<![\w-])" + _CAPRUN + r"(?![\w-])", text)]
            same_head = [n for n in names if len(n) >= 2 and n[-1] == head]
            if not same_head or any(said[-len(n):] == n or n[-len(said):] == said for n in same_head):
                continue
            veto.add(f["key"])
            out.append(_finding(s, "conflict_or_expired", f, _quote(f),
                                f'"{m.group(0)}": the fact names {acr} grades '
                                f'{" / ".join(dict.fromkeys(" ".join(n) for n in same_head))} only ({_quote(f)})'[:400]))
            break
    return out


# ---------- a named award / certificate a valid fact says is NOT held ("the Professional Certificate
#            in Glaucoma (not the Higher Certificate)"), said as held

NOT_NAMED = re.compile(r"\b(?:not|never|no)\s+(?:the\s+|a\s+|an\s+|any\s+)?(?P<n>[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)+)")


def _denied_name_findings(fs: "FactSets", s: str, flagged: set) -> list[dict]:
    out = []
    for f in fs.ok:
        if f["key"] in flagged:
            continue
        text = " ".join(str(x) for x in (f.get("text"), f.get("value_text")) if x)
        for m in NOT_NAMED.finditer(text):
            name = m.group("n")
            if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", re.sub(re.escape(m.group(0)), " ", text)):
                continue          # the fact also says it without the negation
            if any(re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", " ".join(
                    str(x) for x in (g.get("text"), g.get("value_text")) if x)) for g in fs.ok if g is not f):
                continue          # another valid fact holds it
            hit = re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", s)
            if hit and not _NEG_BEFORE.search(s[max(0, hit.start() - 20):hit.start()]) and not re.search(
                    r"(?i)\b(?:not|never|no)\b[^.]{0,12}$", s[:hit.start()]):
                out.append(_finding(s, "conflict_or_expired", f, _quote(f) or f.get("text"),
                                    f'"{name}": the fact says not ({f.get("text") or ""})'[:400]))
                break
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    """One finding per (label, fact); a STRONG one replaces a WEAK one for the same fact."""
    seen: dict = {}
    out: list = []
    for x in items:
        k = (x["label"], x["fact_key"], x["detail"] if x["fact_key"] is None else "")
        if k in seen:
            i = seen[k]
            if out[i].get("_weak") and not x.get("_weak"):
                out[i] = x
            continue
        seen[k] = len(out)
        out.append(x)
    return out


def blocked(findings: list[dict]) -> bool:
    return any(f["blocking"] for f in findings)
