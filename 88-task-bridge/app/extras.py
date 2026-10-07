"""Invented extras: the plausible details a chatbot adds that no fact supports.

Free chatbots write "free Wi-Fi", "kids under 12 stay free", "a short drive from the airport", "seats up
to 100" or "book 3 nights, get the 4th free" because such lines are typical of the genre — not because
the business offers them. Each pattern below names a kind of extra and the words a fact would use if
it were true. A sentence is flagged (`no_source`, blocking) only when no valid public fact in scope
mentions any of those words; vague ones ("a short drive", "walking distance") are only `review`.
Negated sentences and questions are skipped. Runs after the evidence check, like the arithmetic check.
"""
import os
import re
from dataclasses import dataclass
from datetime import date

from . import facts as F
from . import local as L


@dataclass(frozen=True)
class Extra:
    kind: str           # amenity | offer | policy | claim | distance | capacity
    pattern: re.Pattern
    support: tuple      # words that, in a fact, make the line true
    strong: bool = True


def _p(rx: str) -> re.Pattern:
    return re.compile(rx, re.I)


EXTRAS = (
    # amenities
    Extra("amenity", _p(r"\bwi[- ]?fi\b|\binternet\b"), ("wi-fi", "wifi", "internet")),
    Extra("amenity", _p(r"\bair[- ]?condition(?:ing|ed|er)\b|\ba/c\b"), ("air condition", "air-condition")),
    Extra("amenity", _p(r"\b(?:free |on-site |secure )?(?:car )?parking\b|\bcar park\b"), ("parking", "car park")),
    Extra("amenity", _p(r"\bairport (?:pick[- ]?ups?|transfers?|shuttles?|runs?)\b|\b(?:pick|collect)s? you up\b"),
          ("airport", "transfer", "pick-up", "pickup", "shuttle")),
    Extra("amenity", _p(r"\bprojectors?\b|\bsound system\b|\bPA system\b|\bmicrophones?\b|\bscreens?\b(?= for)"),
          ("projector", "sound system", "microphone", "audio-visual", "av equipment")),
    Extra("amenity", _p(r"\brooftop (?:bar|terrace|restaurant|pool)\b|\binfinity pool\b|\bjacuzzi\b|\bhot tubs?\b"),
          ("rooftop", "infinity", "jacuzzi", "hot tub")),
    Extra("amenity", _p(r"\bminibars?\b|\broom service\b|\blaundry\b|\bbackup (?:power|generator)\b"),
          ("minibar", "room service", "laundry", "generator", "backup")),
    Extra("amenity", _p(r"\bcold room\b|\bfridges?\b|\bcoolers?\b|\bchillers?\b"), ("cold room", "fridge", "cooler", "chiller")),
    Extra("amenity", _p(r"\bonline (?:ordering|shop|store|portal)\b|\bordering (?:portal|app|platform)\b|\bmobile app\b|\bour app\b"),
          ("online", "portal", " app")),
    Extra("amenity", _p(r"\brefrigerated (?:trucks?|vans?|lorr(?:y|ies)|delivery|transport)\b|\bcold[- ]chain\b"),
          ("refrigerated", "cold chain", "cold-chain")),
    Extra("amenity", _p(r"\b(?:shelf|shelving|display) (?:racks?|stands?|units?)\b|\bbranded (?:umbrellas?|signage|posters?|glasses)\b"),
          ("rack", "shelf", "display", "branded")),
    Extra("amenity", _p(r"\bkids'? club\b|\bplay ?(?:ground|area|room)\b|\bbabysitting\b|\bcots?\b|\bhigh ?chairs?\b"),
          ("kids", "children", "child", "play", "cot", "high chair", "babysit")),
    # offers and policies
    Extra("policy", _p(r"\b(?:kids?|children|under[- ]\d+s?)\b[^.!?\n]{0,40}\b(?:stay|eat|go|travel)s? (?:for )?free\b"),
          ("children", "kids", "child")),
    Extra("offer", _p(r"\b(?:\d+|one|two|three|four|five|six|seven)(?:st|nd|rd|th)? night(?:s)? (?:is |are )?(?:free|on us|on the house)\b|"
                     r"\b(?:fourth|third|second|fifth) night (?:is )?(?:free|on us|on the house)\b|"
                     r"\bstay \d+ nights?,? (?:pay|get)\b|"
                     r"\bget the \d+(?:st|nd|rd|th) (?:night )?(?:free|on us)\b"), ("night free", "free night", "nights for the price")),
    Extra("policy", _p(r"\bfree cancell?ation\b|\bcancel (?:for free|any ?time)\b|\bfull refunds?\b"),
          ("cancel", "refund")),
    Extra("policy", _p(r"\b\d+[- ]days?(?:'|’)? (?:credit|payment terms|to pay)\b|\bpay (?:in|within) \d+ days\b|\bnet[- ]\d+ (?:days|terms)\b|"
                       r"\b\d+ days(?:'|’)? credit\b"), ("credit", "payment terms", "days to pay")),
    Extra("policy", _p(r"\bsale[- ]or[- ]return\b|\breturns? (?:accepted|policy)\b|\bmoney[- ]back\b"),
          ("sale or return", "sale-or-return", "returns", "money back")),
    Extra("policy", _p(r"\b(?:buy back|bought back|refund|deposit)\b[^.!?]{0,30}\bempt(?:y|ies)\b|"
                       r"\bempt(?:y|ies)\b[^.!?]{0,30}\b(?:refund|deposit|buy back|bought back|pay)\b"),
          ("deposit", "buy back", "refund")),
    Extra("policy", _p(r"\bopen (?:late|until late|till late|until midnight|24 hours)\b|\blate[- ]night\b"),
          ("late", "midnight", "24 hours")),
    Extra("offer", _p(r"\bfree delivery\b|\bdelivery is free\b|\bdeliver(?:ed|y)? (?:for )?free\b"), ("delivery", "deliver")),
    Extra("offer", _p(r"\bloyalty (?:discounts?|cards?|points?|scheme|programme|program)\b|\bmember(?:s|ship)? discounts?\b"),
          ("loyalty", "member")),
    Extra("offer", _p(r"\bfree (?:gifts?|crates?|bottles?|drinks?|glass(?:es)?|welcome drink|dessert|upgrade)\b|"
                     r"\bbuy \w+,? get \w+ free\b|\bget \w+ (?:crates?|bottles?|cases?|packs?) (?:free|on us)\b|"
                     r"\b(?:\d+|one|a) (?:crates?|bottles?|cases?) (?:free|on us|on the house)\b"),
          ("free gift", "free crate", "crate free", "free bottle", "bottle free", "free drink", "free glass", "welcome drink",
           "free dessert", "free upgrade", "get one free", "buy one")),
    Extra("offer", _p(r"\b(?:collect|pick up|picks up|take back|takes back|deliver|install|set up)\b[^.!?]{0,50}\bfree of charge\b|"
                     r"\bfree of charge\b[^.!?]{0,40}\b(?:collection|pick-?up|delivery|installation|set-?up)\b"),
          ("free of charge", "collection", "we collect", "pick-up service")),
    Extra("offer", _p(r"\b(?:hall|room|venue|meeting room|conference) hire is free\b|\bfree (?:hall|room|venue) hire\b|"
                     r"\bno (?:hall|room|venue) hire (?:fee|charge)\b"), ("hire",)),
    # claims
    Extra("claim", _p(r"\beco[- ]certified\b|\bcarbon[- ]neutral\b|\bnet[- ]zero\b"), ("eco-certif", "carbon", "net zero", "net-zero")),
    Extra("claim", _p(r"\bchild[- ](?:safe|proof)\b|\bpet[- ]friendly\b|\bwheelchair[- ](?:accessible|friendly)\b|\bdisabled access\b"),
          ("child-safe", "child safe", "pet", "wheelchair", "accessible", "disabled")),
    Extra("claim", _p(r"\bhalal\b|\bkosher\b|\bvegan[- ]certified\b"), ("halal", "kosher", "vegan")),
    Extra("claim", _p(r"\bfully licen[cs]ed\b|\blicen[cs]ed (?:venue|bar|premises)\b"), ("licen",)),
    Extra("claim", _p(r"\b(?:fastest|quickest|cheapest|lowest[- ]priced?) (?:delivery|service|prices?|in)\b"),
          ("fastest", "cheapest", "lowest")),
    Extra("claim", _p(r"\b(?:since|established in|est\.?|founded in|serving [\w ]{1,20} since) (?:19|20)\d\d\b|"
                     r"\b\d+\+? years? (?:of experience|in business|serving|of service)\b"),
          ("since 19", "since 20", "established", "founded", "years of", "years in business")),
    Extra("claim", _p(r"\b(?:cheaper|better value|lower[- ]priced) than\b|\bbeat any (?:other )?(?:price|quote|distributor|supplier)\b|"
                     r"\bprice[- ]match(?:ing)?(?: guarantee)?\b"), ("cheaper than", "price match", "beat any")),
    Extra("claim", _p(r"\b(?:the )?(?:largest|biggest|leading|most popular|top-rated)\s+(?:\w+\s+){0,2}"
                     r"(?:distributor|supplier|lodge|hotel|resort|venue|brand|company|in)\b"),
          ("largest", "biggest", "leading", "most popular", "top-rated")),
    Extra("claim", _p(r"\b(?:guaranteed|we guarantee|money-back guarantee|satisfaction guarantee)\b"), ("guarantee",)),
    Extra("claim", _p(r"\b24/7 (?:support|service|customer|help)\b|\bround[- ]the[- ]clock (?:support|service)\b"),
          ("24/7", "round the clock", "24 hours")),
    Extra("offer", _p(r"\bget (?:an? |one |\d+ )?(?:extra|additional|bonus) (?:crates?|bottles?|cases?|packs?|nights?|rooms?)\b"),
          ("extra crate", "bonus", "free crate", "crate free")),
    Extra("offer", _p(r"\b(?:gives?|offers?|get|gets|enjoy)\b[^.!?\n]{0,40}\ba discount\b|\bdiscounted (?:rates?|prices?)\b"),
          ("discount",)),
    Extra("offer", _p(r"\b(?:free|tasting)\s+samples?\b|\bwith\s+(?:free\s+)?samples\b|\bsamples?\s+(?:to\s+taste|for\s+you)\b"),
          ("sample",)),
    Extra("policy", _p(r"\b(?:reserved?|held|hold|kept|keep)\b[^.!?\n]{0,30}\bfor (?:\d+|one|two|three|five|seven|ten|fourteen) days\b"),
          ("reserve", "hold", "held for")),
    Extra("policy", _p(r"\b(?:until|till|til) midnight\b"), ("midnight", "24 hours", "24/7")),
    Extra("policy", _p(r"\bsame[- ]day (?:delivery|deliveries|restock\w*|dispatch|service|supply)\b"), ("same-day", "same day")),
    Extra("amenity", _p(r"\b(?:named|dedicated|personal|own) account managers?\b"), ("account manager",)),
    Extra("amenity", _p(r"\blive (?:band|music|entertainment|dj)\b|\bin-house (?:photographer|dj|band|florist|decorator)s?\b"),
          ("live band", "live music", "entertainment", "photographer", "florist", "decorat")),
    Extra("amenity", _p(r"\b(?:photography|bird|birding|viewing|wildlife) hides?\b|\bpacked (?:lunch|breakfast)(?:es)?\b|"
                       r"\b(?:breakfast|lunch|picnic) box(?:es)?\b"), (" hide", "packed", " box")),
    Extra("claim", _p(r"\bthe only (?:\w+ ){0,2}(?:lodge|hotel|venue|resort|distributor|supplier|company|business|place)\b"),
          ("the only",)),
    # trial (small model): what a weaker chatbot adds
    Extra("claim", _p(r"\bworld[- ]class\b|\bbest[- ]in[- ]class\b|\bunrivall?ed\b|\bunmatched\b|\bsecond to none\b"),
          ("world-class", "world class", "best-in-class", "unrivalled", "unmatched")),
    Extra("amenity", _p(r"\b(?:expert|professional|qualified|certified|experienced)\s+(?:local\s+)?(?:guides?|naturalists?|rangers?)\b"),
          ("expert guide", "professional guide", "qualified guide", "certified guide", "experienced guide")),
    Extra("amenity", _p(r"\bswim(?:s|ming)?\s+in\s+(?:the\s+)?(?:crystal(?:[- ]clear)?\s+|clear\s+)?(?:lakes?|lake\s+[A-Z]\w+|rivers?)\b"),
          ("swim in the lake", "lake swimming", "swimming in the lake")),
    Extra("capacity", _p(r"\b(?:groups?|teams?|retreats?|events?|parties)\s+of\s+(?:every|any|all)\s+sizes?\b|\bany\s+(?:group|team)\s+size\b|"
                         r"\bevery\s+team\s+size\b"), ("capacity", "groups of up to", "up to ")),
    Extra("claim", _p(r"\bno\s+middlem[ae]n\b|\bcut(?:ting)?\s+out\s+the\s+middlem[ae]n\b|\b(?:direct|straight)\s+from\s+the\s+"
                     r"(?:brewery|factory|farm|maker|manufacturer)\b"), ("middleman", "direct from", "straight from the")),
    Extra("distance", _p(r"\b\d+(?:\.\d+)?\s*(?:minutes?|mins?|km|kilomet(?:re|er)s?|miles?|hours?|hrs?)\s+away\b"),
          ("km from", "kilometres from", "minutes from", "hours from", "drive from", "away from"), strong=True),
    # held-out round 5: urgency and rivals nobody can check
    Extra("claim", _p(r"\blimited\s+(?:availability|spaces?|spots?|places?|rooms?|stock)\b|\blast\s+(?:few\s+)?(?:spots?|places?|rooms?|tables?|crates?)\b|"
                     r"\b(?:filling|selling|going|books?)\s+(?:up\s+)?fast\b|\bbefore\s+(?:it'?s|they'?re)\s+(?:gone|sold\s+out)\b|"
                     r"\bonly\s+(?:a\s+few|\d+|two|three|four|five)\s+(?:rooms?|spots?|places?|crates?|tables?)\s+left\b"),
          ("limited availability", "few rooms left", "last rooms", "sells out")),
    Extra("claim", _p(r"\byour\s+competitors\s+(?:are|have)\b|\b(?:other|rival)\s+(?:bars|hotels|shops|operators)\s+are\s+already\b"),
          ("competitor",)),
    Extra("amenity", _p(r"\bon\s+tap\b|\bevery\s+(?:shelf\s+and\s+)?tap\b|\bdraught\b|\bdraft\s+beer\b|\bkegs?\b"),
          ("draught", "on tap", "keg", "draft beer")),
    # distances and capacities
    Extra("distance", _p(r"\b\d+(?:\.\d+)?\s*(?:minutes?|mins?|km|kilomet(?:re|er)s?|miles?|hours?|hrs?)\s+(?:drive |walk |ride )?"
                         r"(?:from|to|away from)\b|\b\d+[- ](?:minute|min|km|mile)[- ](?:drive|walk|ride)\b"),
          ("km from", "kilometres from", "kilometers from", "miles from", "minutes from", "minute drive", "minutes' drive",
           "drive from", "walk from", "walk to", "drive to")),
    Extra("distance", _p(r"\b(?:a )?short (?:drive|walk|ride|hop) (?:from|to|away)\b|\bwalking distance\b|\bminutes (?:from|away)\b"),
          ("km from", "minutes from", "minute drive", "drive from", "walk from", "walking distance", "close to the airport",
           "near the airport"), strong=False),
    Extra("capacity", _p(r"\b(?:seats?|holds?|accommodates?|fits?|hosts?|for up to|up to)\s+(?:up to\s+)?\d{2,4}\s+"
                         r"(?:people|guests|delegates|persons|participants|seated|attendees)\b"),
          ("seats ", "seated", "capacity", "holds up to", "up to ")),
)
NEG = _p(r"\b(?:no|not|never|without|don'?t|doesn'?t|isn'?t|aren'?t|can'?t|cannot)\b")
NEG_AFTER = _p(r"\W*(?:is|are|was|were)\s+(?:not|never)\b|\W*(?:isn'?t|aren'?t|wasn'?t|weren'?t)\b")
SENT = re.compile(r"(?<=[.!?])\s+|\n+")


UNIT_STEM = {"min": "min", "mins": "min", "minute": "min", "minutes": "min", "hour": "hour", "hours": "hour",
             "hr": "hour", "hrs": "hour", "km": "km", "kilometre": "km", "kilometres": "km", "kilometer": "km",
             "kilometers": "km", "mile": "mile", "miles": "mile"}


def _number_in_facts(found: str, support: str) -> bool:
    """The extra's number with the same unit is already in a fact (a duration, a distance, a capacity)."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*[- ]?\s*([a-z]+)", found.lower())
    if not m:
        return False
    n, unit = m.group(1), UNIT_STEM.get(m.group(2), m.group(2))
    for fm in re.finditer(r"(\d+(?:\.\d+)?)\s*[- ]?\s*([a-z]+)", support):
        if fm.group(1) == n and UNIT_STEM.get(fm.group(2), fm.group(2)) == unit:
            return True
    return False


def _support_list(facts: list[dict], day: date, scope: dict) -> list[str]:
    out = []
    for f in facts:
        if (f.get("sensitivity") or "public") != "public" or F.classify(f, day, scope) is not None or not_offered(f):
            continue
        out.append(" ".join(str(x) for x in (F.subject_ref(f), f.get("text"), f.get("value_text"), f.get("attribute"),
                                             " ".join(str(p) for p in f.get("allowed_phrasing") or [])) if x).lower())
    return out


def _support_text(facts: list[dict], day: date, scope: dict) -> str:
    out = []
    for f in facts:
        if (f.get("sensitivity") or "public") != "public" or F.classify(f, day, scope) is not None or not_offered(f):
            continue
        out.append(" ".join(str(x) for x in (F.subject_ref(f), f.get("text"), f.get("value_text"), f.get("attribute"),
                                             " ".join(str(p) for p in f.get("allowed_phrasing") or [])) if x))
    return " ".join(out).lower()


# Offers in a frame, not a word list: "<thing> is included / available to borrow", "complimentary <thing>",
# "every guest receives <thing>", "<thing> on Saturdays", "at no cost". The thing's head word must be
# in a valid public fact, or the line is an invented extra. Words most facts share (the business,
# the town) never count as support.
_W = r"[A-Za-z][\w'’-]*"
FRAMES = (
    ("offer", _p(r"(?P<obj>(?:" + _W + r"\s+){0,4}" + _W + r")\s+(?:is|are)\s+(?:also\s+)?(?:included|complimentary|provided|"
                 r"thrown in|available(?:\s+(?:to\s+(?:borrow|hire|rent)|on\s+request))?)\b")),
    ("offer", _p(r"(?<![\w-])(?:complimentary|free)\s+(?P<obj>" + _W + r"(?:,?\s+" + _W + r"){0,3})")),
    ("offer", _p(r"\b(?:every|each|all)\s+(?:" + _W + r"\s+){1,2}(?:receives?|gets?|is\s+given|are\s+given|will\s+(?:receive|get))\s+"
                 r"(?:a|an|one|their\s+own|your\s+own)?\s*(?:free\s+|printed\s+|personal\s+)?(?P<obj>" + _W + r"(?:\s+" + _W + r"){0,3})")),
    ("schedule", _p(r"\b(?:talk|talks|band|music|show|shows|class|classes|session|sessions|party|quiz|market|brunch|buffet|"
                    r"dinner|barbecue|bbq|tour|tours|event|events|entertainment|dancing|tasting|delivery|deliveries)\b"
                    r"[^.!?\n]{0,40}\b(?:every|on)\s+(?P<obj>(?:mon|tues|wednes|thurs|fri|satur|sun)days?)\b|"
                    r"\b(?P<obj2>(?:mon|tues|wednes|thurs|fri|satur|sun)day)\s+(?:delivery|deliveries|service|opening)\b")),
    ("offer", _p(r"\b(?:at\s+no\s+(?:extra\s+|additional\s+)?(?:cost|charge)|free\s+of\s+charge)\b")),
)
STOP_OBJ = frozenset("""a an the and or of on for with to at in by from our your their its this that these those every each all
any some also just one two three four five six seven eight nine ten first new extra own day days week weeks night nights
evening morning afternoon guest guests customer customers visitor visitors client clients delegate delegates people
party parties birder birders team staff we you they it is are be been was were will can may served given offered
price prices rate rates everything anything access stay""".split())
FREE_WORDS = ("free", "complimentary", "no charge", "no cost", "included", "includes", "at no", "on the house")
_STOP_AT = frozenset("""of on for with to at in by from during throughout when if as all every each will would can could should
is are was were be come comes came show shows that which who today tonight now right this included includes provided
available""".split())


def _words(s: str) -> list[str]:
    return [w.lower().strip("'’-") for w in re.findall(r"[A-Za-z][\w'’-]*", s or "")]


def _forms(w: str) -> set:
    out = {w}
    if w.endswith("ies") and len(w) > 4:
        out.add(w[:-3] + "y")
    elif w.endswith("es") and len(w) > 4:
        out |= {w[:-2], w[:-1]}
    elif w.endswith("s") and len(w) > 3:
        out.add(w[:-1])
    else:
        out.add(w + "s")
    return out


def _in_support(w: str, support_words: set) -> bool:
    return bool(_forms(w) & support_words)


def _head(obj: str, after: bool) -> list[str] | None:
    """The thing offered: the last content word before the frame, or the words after it up to a preposition."""
    ws = _words(obj)
    if after:                                  # "free skin consultation included" -> skin consultation
        cut = []
        for w in ws:
            if w in _STOP_AT:
                break
            cut.append(w)
        ws = cut
    else:                                      # "rates for tour operators are available" -> rates
        stop = next((i for i, w in enumerate(ws) if w in _STOP_AT and i > 0), None)
        ws = ws[:stop] if stop is not None else ws
        if ws and ws[-1] in STOP_OBJ:          # the thing is a price / rate / stay: not an extra
            return None
    ws = [w for w in ws if w not in STOP_OBJ and len(w) >= 3]
    return ws or None


def frame_check(s: str, support_facts: list[str], generic: set) -> dict | None:
    support_words = set(_words(" ".join(support_facts))) - generic
    for kind, rx in FRAMES:
        m = rx.search(s)
        if not m:
            continue
        if NEG.search(" ".join(s[:m.start()].split()[-4:])):
            continue
        if "obj" in rx.groupindex and NEG.search(m.group("obj") or ""):
            continue
        if "obj" not in rx.groupindex:            # "at no cost": something here is given free
            content = {w for w in _words(s) if len(w) >= 4 and w not in STOP_OBJ and w not in generic}
            if not content or any(any(fw in f for fw in FREE_WORDS) and any(_in_support(w, set(_words(f))) for w in content)
                                  for f in support_facts):
                continue
            thing = m.group(0)
        else:
            obj = m.group("obj") or (m.groupdict().get("obj2") or "")
            if kind == "schedule":
                day = obj.lower().rstrip("s")
                if any(day in f for f in support_facts):
                    continue
                thing = m.group(0)
            else:
                heads = _head(obj, after=m.start("obj") > m.start())
                # the thing is its last word ("bird guide"); a list ("tweaks and adjustments") is
                # backed when any of its words is
                listed = re.search(r"\band\b|,", obj) is not None
                if not heads or heads[-1] in generic or any(_in_support(w, support_words) for w in (heads if listed else heads[-1:])):
                    continue
                thing = " ".join(heads)
        return {"sentence": s, "label": "no_source", "fact_key": None, "quote": None, "blocking": True,
                "detail": f"\"{thing}\": an offer or service no fact mentions — remove it, or add it as a fact if it's true"}
    return None


# A service promise: the business (or "every guest") does or gives something ("Our merchandiser visits
# weekly to arrange your shelves", "We lend chilled storage tubs", "Late checkout until 4 pm is yours").
# Flagged only when the line's own distinctive words are almost all absent from every valid public fact:
# at least 3 such words and at most one in four found. Marketing filler and generic words never count.
PROMISE = _p(r"\b(?:we|we'll|our\s+(?:[a-z-]+\s+){0,2}?(?:team|staff|drivers?|van|vans|guides?|coordinator|merchandiser|"
             r"chef|kitchen|rangers?|naturalists?|nurse|sales\s+team|people|crew)|every\s+\w+|each\s+\w+|"
             r"(?:guests|couples|teachers|students|shops|shoppers|customers|delegates|tour\s+leaders|you)\s+(?:can|will|get|receive|collect))"
             r"\b[^.!?\n]{0,30}?\b(?:will\s+|can\s+|also\s+)?(?:lend|lends|provide|provides|serve|serves|bring|brings|collect|collects|"
             r"visit|visits|train|trains|arrange|arranges|set\s+up|sets\s+up|decorate|style|lock|send|credit|"
             r"give|gives|receive|receives|request|taste|exchange|organi[sz]e|organi[sz]es|offer|offers|stays|get|gets)\b|"
             r"\b(?:can\s+be\s+(?:added|bought|booked|arranged|requested)|is\s+(?:arranged|offered|set\s+up|on\s+duty|based|dressed|yours)|"
             r"are\s+(?:offered|arranged|set\s+up)|comes?\s+with|waits?\s+in\s+your|is\s+due\s+\d+|pay\s+half|paid\s+by)\b")
FILLER = frozenset("""make makes made unforgettable perfect special memorable amazing wonderful beautiful great best warm warmly
welcome welcoming happy love loved enjoy enjoys relax relaxing stunning views view experience experiences moments moment
memories memory forward look looking help helps need needs want wants ready simply easy easily always never more most
very really just like better place time times today tomorrow soon book booking contact call message order orders reply
questions question details info information plan plans planning trip trips visit visiting stay staying wait reply
email emails quote quotes quantities send list here's whatever thank thanks hold holds keep keeps
sorry feedback honest thinking near approaches whole already dollars dollar decision simple different takes busy
season autumn summer winter spring week weeks year years month months""".split())
FILLER = FILLER | frozenset(L.currency()) | frozenset(L.currency_names().values())
CTA = _p(r"\b(?:reply|email|e-mail|call|ring|message|whatsapp|text|contact|dm)\b[^.!?\n]{0,60}\b(?:and|&)\s+(?:we'll|we\s+will|our)\b|"
         r"\b(?:glad|happy|delighted|love|pleased)\s+to\s+(?:receive|take|hear|welcome|help|see)\b")


def promise_on() -> bool:
    return os.environ.get("PROMISE_CHECK", "on").lower() not in ("off", "0", "false", "no")


def promise_check(s: str, support_words: set, generic: set) -> dict | None:
    if not PROMISE.search(s) or CTA.search(s) or NEG.search(s):
        return None          # a negated line ("we don't offer …") promises nothing
    words = [w for w in _words(s) if len(w) >= 4 and w not in STOP_OBJ and w not in FILLER and w not in generic
             and not w.isdigit()]
    words = list(dict.fromkeys(words))
    if len(words) < 3:
        return None
    stems = {w[:5] for w in support_words if len(w) >= 5}
    found = [w for w in words if _in_support(w, support_words) or (len(w) >= 5 and w[:5] in stems)]
    missing = [w for w in words if w not in found]
    if len(missing) < 3 or len(found) * 4 > len(words):
        return None
    return {"sentence": s, "label": "no_source", "fact_key": None, "quote": None, "blocking": True,
            "detail": f"a promise no fact backs ({', '.join(missing[:4])}): remove it, or add it as a fact if it's true"}


def not_offered(f: dict) -> bool:
    """A fact that says the business does NOT offer something: an availability fact with value false
    ("We do not offer airport pick-up.")."""
    return f.get("fact_type") == "availability" and f.get("value") is False


def _names(f: dict) -> list[str]:
    out = [F.subject_ref(f) or "", *(str(p) for p in f.get("allowed_phrasing") or [])]
    return [n.strip() for n in out if len(n.strip()) >= 3]


def not_offered_check(s: str, facts: list[dict], day: date, scope: dict) -> dict | None:
    """A line that offers what the business told us it does not: blocked, quoting its own fact."""
    for f in facts:
        if not not_offered(f) or f.get("status") == "draft" or F.classify(f, day, scope) is not None:
            continue
        for n in _names(f):
            words = [re.escape(w) for w in n.split()]
            m = re.search(r"(?i)(?<![\w-])" + r"[\s-]+".join(words) + r"(?:s|es)?(?![\w-])", s)
            if not m:
                continue
            if NEG.search(" ".join(s[:m.start()].split()[-4:])) or NEG_AFTER.match(s[m.end():]):
                continue                 # "we don't offer airport pick-up" agrees with the fact
            public = (f.get("sensitivity") or "public") == "public"
            return {"sentence": s, "label": "conflict_or_expired", "fact_key": f["key"],
                    "quote": (f.get("text") if public else None), "blocking": True,
                    "detail": f"\"{m.group(0)}\": your facts say this is not offered"
                              + (f" ({f.get('text')})" if public and f.get("text") else "")}
    return None


VARIANT_NAME = re.compile(r"(?P<base>[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,4})\s*\((?P<var>[A-Z][\w' -]{1,30})\)")


def variant_check(s: str, facts: list[dict], day: date, scope: dict) -> dict | None:
    """real-9: a room / product named with a variant it does not come in ("Family Room (Lake View)" when only
    the Garden View one exists). conflict_or_expired with that name's real variant (the one whose value the
    line states, if any)."""
    live = [f for f in facts if f.get("status") != "draft" and F.classify(f, day, scope) is None]
    refs = {}
    for f in live:
        r = " ".join((F.subject_ref(f) or "").split())
        if "(" in r:
            refs.setdefault(r.lower(), []).append(f)
    if not refs:
        return None
    for m in VARIANT_NAME.finditer(s):
        words, var = m.group("base").split(), " ".join(m.group("var").split())
        for k in range(len(words)):
            base = " ".join(words[k:])
            full = f"{base} ({var})".lower()
            if any(r == full or r.startswith(full) for r in refs):
                break                            # a real name ("… (Level 1), live online" starts with it)
            others = [r for r in refs if r.startswith(base.lower() + " (")]
            if not others:
                continue
            cands = [f for r in others for f in refs[r]]
            said = {x for x in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}
            pick = next((f for f in cands if isinstance(f.get("value"), (int, float))
                         and any(x.replace(",", "") in (str(int(f["value"])) if float(f["value"]).is_integer() else str(f["value"]))
                                 for x in said)), None) or cands[0]
            real = sorted({F.subject_ref(f) for f in cands})
            return {"sentence": s, "label": "conflict_or_expired", "fact_key": pick["key"], "quote": None, "blocking": True,
                    "detail": f"\"{base} ({var})\" is not one of yours: you have " + ", ".join(real[:4])}
    return None


PRODUCT_SIZE = re.compile(r"(?<![\w-])(?P<brand>[A-Z][a-z]{2,})\s+(?P<size>\d{2,4}\s?(?:cl|ml|l|kg|g))\b")
_NOT_BRAND = frozenset("""our the a an each every your this that these those new only all any one two three per with and other
others more extra premium value best both cold chilled fresh local small large big mini standard regular classic""".split())


def unknown_products(text: str, facts: list[dict]) -> list[dict]:
    """real-8/9: a brand + size the business has no fact for ("Corvo 33cl", "Selva 33cl at 1,400 kora"), when
    the business does sell sized products (some fact names one). wrong_scope, no fact key."""
    known = " ".join(" ".join(str(x) for x in (F.subject_ref(f), f.get("text"), f.get("value_text")) if x)
                     for f in facts).lower()
    if not re.search(r"\d{2,4}\s?(?:cl|ml|l|kg|g)\b", " ".join(F.subject_ref(f) or "" for f in facts), re.I):
        return []
    out = []
    for raw in SENT.split(text or ""):
        s = raw.strip()
        for m in PRODUCT_SIZE.finditer(s):
            brand = m.group("brand")
            if brand.lower() in _NOT_BRAND or re.search(r"(?<![a-z])" + re.escape(brand.lower()) + r"(?![a-z])", known):
                continue
            if NEG.search(" ".join(s[:m.start()].split()[-4:])):
                continue                 # "we don't carry Corvo 33cl"
            out.append({"sentence": s, "label": "wrong_scope", "fact_key": None, "quote": None, "blocking": True,
                        "detail": f"\"{m.group(0)}\": not a product in your facts — remove it, or add it as a fact if you sell it"})
            break
    return out


def check(text: str, all_facts: list[dict], day: date, scope: dict) -> list[dict]:
    """no_source findings for invented extras; [] when every extra is backed by a fact."""
    support = _support_text(all_facts, day, scope)
    support_facts = _support_list(all_facts, day, scope)
    df: dict[str, int] = {}
    for f in support_facts:
        for w in set(_words(f)):
            df[w] = df.get(w, 0) + 1
    generic = {w for w, c in df.items() if len(support_facts) >= 6 and c > len(support_facts) * 0.25}
    out = []
    for raw in SENT.split(text or ""):
        s = raw.strip()
        if not s or s.endswith("?"):
            continue
        seen = set()
        hit = not_offered_check(s, all_facts, day, scope) or variant_check(s, all_facts, day, scope)
        if hit:
            out.append(hit)
            continue
        for ex in EXTRAS:
            m = ex.pattern.search(s)
            if not m or ex.kind + m.group(0).lower() in seen:
                continue
            if NEG.search(" ".join(s[:m.start()].split()[-4:])):
                continue                 # "no Wi-Fi", "we don't offer free delivery": a negation right before it
            if NEG_AFTER.match(s[m.end():]):
                continue                 # "Wi-Fi is not available in the halls"
            if any(w in support for w in ex.support):
                continue
            if ex.kind in ("distance", "capacity") and _number_in_facts(m.group(0), support):
                continue                 # "90 minutes from …" when a fact gives that 90-minute duration
            seen.add(ex.kind + m.group(0).lower())
            what = {"amenity": "an amenity", "offer": "an offer", "policy": "a policy", "claim": "a claim",
                    "distance": "a distance or travel time", "capacity": "a capacity"}[ex.kind]
            out.append({"sentence": s, "label": "no_source" if ex.strong else "review", "fact_key": None,
                        "quote": None, "blocking": ex.strong,
                        "detail": f"\"{m.group(0)}\": {what} no fact mentions — remove it, or add it as a fact if it's true"})
        if not seen:
            hit = frame_check(s, support_facts, generic)
            if not hit and promise_on():
                hit = promise_check(s, set(_words(" ".join(support_facts))) - generic, generic)
            if hit:
                out.append(hit)
    return out
