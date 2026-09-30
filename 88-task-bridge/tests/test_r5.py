"""Fifth round (invented businesses, not the eval companies): evidence strength tiers (a WEAK basis
is only a review), the negation reader of a fact's own text, offers said in words, a rating number
on a named scheme, staff credentials, hyphenated superlatives, a disclosure used through the named
service, "24/7" said and described, and the splitter's heading-less pieces. Every rule has
negative controls."""
from datetime import date

import pytest

from app import evidence, paste

from .data import fact

DAY = date(2031, 5, 12)


def run(text, facts, scope=None):
    findings, _ = evidence.check_text(text, facts, DAY, scope or {})
    return findings


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


def reviews(findings):
    return [f["fact_key"] for f in findings if f["label"] == "review"]


# ---------- 1. tiers: shared everyday words only hint (review); a word of the fact's own blocks

KILNS = [
    fact("studio-firing", "Every pottery class includes glaze firing in the studio kiln.", "glaze firing included",
         subject={"kind": "service", "ref": "Pottery class firing"}, fact_type="inclusion", attribute="firing"),
    fact("spring-clay-offer", "Spring offer: a free clay bag with any pottery class booking.",
         "a free clay bag with any pottery class booking", subject={"kind": "offer", "ref": "Spring clay offer"},
         fact_type="price", attribute="free_gift", valid_from="2031-03-01", valid_to="2031-04-30"),
    fact("wheel-hire", "Members can hire a potter's wheel with its own splash pan.", "a wheel with a splash pan",
         subject={"kind": "service", "ref": "Studio extras"}, fact_type="inclusion", attribute="wheel",
         plan_tiers=["member"]),
]


@pytest.mark.parametrize("text", ["Headline: Pottery Class Evenings", "Is a pottery class on your list this spring?",
                                  "Our pottery class books up fast."])
def test_shared_everyday_words_never_block(text):
    f = run(text, KILNS)
    assert blocking(f) == []


def test_word_pair_with_a_word_only_the_stale_fact_has_blocks():
    f = run("Every booking comes with a clay bag to take home.", KILNS)
    assert ("conflict_or_expired", "spring-clay-offer") in blocking(f)


def test_named_offer_still_blocks_and_attribute_term_alone_is_review():
    assert ("conflict_or_expired", "spring-clay-offer") in blocking(run("The Spring clay offer is back!", KILNS))
    f = run("Bring your apron: splash pans for everyone.", KILNS, {"plan_tiers": ["dropin"]})
    assert ("wrong_scope", "wheel-hire") in blocking(f)             # "splash pan": the fact's own words
    f = run("Wheel time for everyone!", KILNS, {"plan_tiers": ["dropin"]})
    assert blocking(f) == [] and "wheel-hire" in reviews(f)          # the attribute term only: review


LEVELS = [
    fact("c1-summer", "The 4-week C1 Summer Sprint cost £600. Finished.", "£600 for the 4-week C1 Summer Sprint",
         subject={"kind": "service", "ref": "C1 Summer Sprint"}, fact_type="price", attribute="course_fee",
         value=600, currency="GBP", status="expired", valid_to="2031-03-01"),
    fact("c1-pass", "88% of our C1 Advanced candidates passed in 2030.", "88% of our C1 Advanced candidates passed",
         subject={"kind": "service", "ref": "C1 Advanced preparation"}, fact_type="result", attribute="pass_rate",
         value=88, unit="%", claim_class="result", required_disclosures=["25 candidates, 2030"]),
]


def test_a_code_that_is_only_part_of_a_name_is_weak():
    assert blocking(run("Stuck at C1?", LEVELS)) == []
    assert blocking(run("Ready to reach C1?", LEVELS)) == []          # no disclosure duty from a level name
    assert ("conflict_or_expired", "c1-summer") in blocking(run("Join our C1 Summer Sprint, just £600.", LEVELS))
    assert ("missing_disclosure", "c1-pass") in blocking(run("88% of our C1 Advanced candidates passed.", LEVELS))


def test_grade_named_by_a_valid_fact_is_not_another_segments():
    facts = [
        fact("svc-price", "A Class 7 service costs £60 for private owners.", "£60",
             subject={"kind": "service", "ref": "Service (Class 7)"}, fact_type="price", attribute="price",
             value=60, currency="GBP", segments=["private"]),
        fact("fleet-price", "Fleet customers pay £48 per Class 7 service.", "£48 per service",
             subject={"kind": "service", "ref": "Service (fleet)"}, fact_type="price", attribute="price",
             value=48, currency="GBP", segments=["fleet"]),
    ]
    assert blocking(run("A Class 7 service costs £60.", facts, {"segments": ["private"]})) == []
    assert ("wrong_scope", "fleet-price") in blocking(run("A service costs £48.", facts, {"segments": ["private"]}))


# ---------- 2. a fact's own text: "X is certified. Y is not certified." affirms X

LOFTS = [
    fact("loft-cert", "Harbour Loft is Gold certified, certificate HL-40211. Quay Loft is not certified.",
         "Gold certified (certificate HL-40211)", subject={"kind": "site", "ref": "Harbour Loft"},
         fact_type="certification", attribute="rating"),
    fact("loft-room", "Harbour Loft rooms are room only (no breakfast).", "room only",
         subject={"kind": "package", "ref": "Harbour Loft room"}, fact_type="inclusion", attribute="board"),
]


def test_a_negation_about_another_subject_is_not_the_facts():
    assert blocking(run("Harbour Loft is Gold certified.", LOFTS)) == []


def test_a_negation_about_the_fact_itself_still_conflicts():
    f = run("Every Harbour Loft room includes breakfast.", LOFTS)
    assert ("conflict_or_expired", "loft-room") in blocking(f)


# ---------- 3. offers said in words

OFFERS = [
    fact("early-offer", "Early booking: 10% off summer camp fees for bookings by 1 May 2031.",
         "10% off summer camp fees", subject={"kind": "offer", "ref": "Early booking discount"}, fact_type="price",
         attribute="discount", value=10, unit="%", valid_to="2031-05-01"),
    fact("half-first", "New members pay half price for their first month.", "half price on your first month",
         subject={"kind": "offer", "ref": "Half-price first month"}, fact_type="price", attribute="discount",
         value=50, valid_to="2031-05-01"),
    fact("seed-deal", "Seed packets: any 4 for £10.", "4 packets of seeds for £10",
         subject={"kind": "offer", "ref": "Seed deal"}, fact_type="price", attribute="multibuy",
         value=10, currency="GBP", valid_to="2031-05-01"),
    fact("pot-price", "Terracotta pots cost £5 each.", "£5 each", subject={"kind": "product", "ref": "Terracotta pot"},
         fact_type="price", attribute="price", value=5, currency="GBP"),
]


@pytest.mark.parametrize("text,key", [
    ("Early birds still get a tenth off camp fees.", "early-offer"),
    ("Half-price first month for new members!", "half-first"),
    ("Sign up and we'll knock half off your first month.", "half-first"),
    ("Your first month costs half the usual £60.", "half-first"),
    ("Any four packets for a tenner.", "seed-deal"),
    ("Four packets, ten quid.", "seed-deal"),
])
def test_offer_in_words_matches_the_offer_value(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text, OFFERS))


@pytest.mark.parametrize("text", ["Terracotta pots are a fiver each.", "Half the class meets outdoors.",
                                  "It takes half an hour to pot up.", "A tenth of our visitors come by bike.",
                                  "We take half the seedlings to the market."])
def test_offer_words_negative_controls(text):
    assert blocking(run(text, OFFERS)) == []


# ---------- 4. a rating number on the scheme a fact rates

RATED = [
    fact("inspection", "Our March 2031 inspection graded the academy 2 (Good) on a 4-point scale.",
         "inspection grade 2 (Good)", subject={"kind": "business", "ref": "Larkfield Academy"},
         fact_type="certification", attribute="inspection_grade", value=2),
    fact("hygiene", "The kiosk has a food hygiene rating of 3 (Generally satisfactory).", "food hygiene rating 3",
         subject={"kind": "site", "ref": "Larkfield kiosk"}, fact_type="certification", attribute="food_hygiene_rating",
         value=3, claim_class="regulated_food"),
]


@pytest.mark.parametrize("text,key", [("Our last inspection graded us 1 (Outstanding).", "inspection"),
                                      ("Grab a snack at our kiosk, with its 5-star food hygiene rating.", "hygiene"),
                                      ("The kiosk has a food hygiene rating of 5.", "hygiene")])
def test_another_number_on_the_rated_scheme_conflicts(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text, RATED))


@pytest.mark.parametrize("text", ["Our last inspection graded us 2 (Good).",
                                  "The kiosk has a food hygiene rating of 3.",
                                  "The kiosk holds a 3-star food hygiene rating."])
def test_same_number_on_the_rated_scheme_matches(text):
    assert blocking(run(text, RATED)) == []


# ---------- 5. staff credentials: acronym-qualified, and "every" when one person holds it

STAFF = [
    fact("lead-cert", "Our head groomer, Mira Holt, is an ACG Certified Master Groomer (ACG ID 55120). "
                      "Our other groomers are ACG Certified at Level 2.",
         "ACG Certified Master Groomer (ACG ID 55120)", subject={"kind": "person", "ref": "Mira Holt"},
         fact_type="credential", attribute="acg_certification"),
]


@pytest.mark.parametrize("text", ["Every tutor is a DELTA-qualified examiner.",
                                  "Our RHS-qualified horticulturists will help you choose."])
def test_acronym_qualified_staff_need_a_fact(text):
    assert ("no_source", None) in blocking(run(text, []))


def test_everyone_said_to_hold_one_persons_level_conflicts():
    f = run("Every groomer at the salon is an ACG Certified Master Groomer.", STAFF)
    assert ("conflict_or_expired", "lead-cert") in blocking(f)


def test_one_person_holding_it_matches():
    assert blocking(run("Our head groomer Mira Holt is an ACG Certified Master Groomer.", STAFF)) == []


# ---------- 6. hyphenated superlatives

@pytest.mark.parametrize("text", ["The best-connected studio in Leeds.", "Subject: Leeds' best-equipped workshop",
                                  "The fastest-growing bakery in Kent."])
def test_hyphenated_superlative_needs_a_fact(text):
    assert ("no_source", None) in blocking(run(text, []))


@pytest.mark.parametrize("text", ["Our best-selling mug is back.", "Spread the cost with interest-free credit.",
                                  "A south-west-facing garden.", "Pick up the latest-season bulbs."])
def test_hyphenated_superlative_negative_controls(text):
    assert blocking(run(text, [])) == []


# ---------- 7. a disclosure used through the named service or its own value words

CARE = [
    fact("care-plan", "We finish every tattoo with aftercare that keeps your touch-up guarantee valid, using "
                      "sterile dressings.", "aftercare that keeps your touch-up guarantee valid",
         subject={"kind": "service", "ref": "Aftercare"}, fact_type="claim", attribute="guarantee_cover",
         required_disclosures=["using sterile dressings"]),
    fact("session-price", "A two-hour session costs £180.", "£180", subject={"kind": "service", "ref": "Tattoo session"},
         fact_type="price", attribute="price", value=180, currency="GBP"),
]


@pytest.mark.parametrize("text", ["Headline: Guarantee-Safe Aftercare", "Keep your touch-up guarantee valid with us.",
                                  "Our aftercare keeps the guarantee valid."])
def test_named_service_or_its_value_words_carry_the_disclosure(text):
    assert ("missing_disclosure", "care-plan") in blocking(run(text, CARE))


@pytest.mark.parametrize("text", ["Our aftercare keeps your touch-up guarantee valid, using sterile dressings.",
                                  "A two-hour session costs £180.", "Aftercare tips are on our blog."])
def test_disclosure_negative_controls(text):
    assert blocking(run(text, CARE)) == []


# ---------- 8. "24/7" said (STRONG with the benefit) and described (review)

HOURS = [
    fact("night-access", "Round-the-clock (24/7) fob access is included with the Studio plan only.",
         "round-the-clock fob access", subject={"kind": "plan", "ref": "Studio"}, fact_type="inclusion",
         attribute="access_hours", plan_tiers=["studio"]),
    fact("day-price", "Day members pay £90 a month.", "£90 a month", subject={"kind": "plan", "ref": "Day"},
         fact_type="price", attribute="price", value=90, currency="GBP", plan_tiers=["day"]),
]
DAYPLAN = {"plan_tiers": ["day"]}


def test_24_7_with_the_benefit_is_wrong_scope():
    assert ("wrong_scope", "night-access") in blocking(run("Day members get 24/7 access.", HOURS, DAYPLAN))


@pytest.mark.parametrize("text", ["Paint when the mood strikes, even at 3am.", "Subject: Your easel, any hour",
                                  "Clients expect you to be on 24/7?"])
def test_described_or_bare_24_7_is_only_review(text):
    f = run(text, HOURS, DAYPLAN)
    assert blocking(f) == [] and "night-access" in reviews(f)


# ---------- 9. splitter: keycaps, emoji before bold, titles, a heading that names no channel

GOOG, WEB, TXT, BLOG, IG, EM = ({"key": k, "channel": c} for k, c in (
    ("g", "google"), ("w", "website"), ("t", "sms"), ("b", "blog"), ("ig", "instagram"), ("em", "email")))


def got(res):
    return {s["piece_key"]: s["text"] for s in res.split}


def test_keycap_numbered_headings_and_google_ad_on_a_google_piece():
    text = ("Here it is!\n\n1️⃣ Google ad\nHeadline 1: Fresh loaves daily\n\n"
            "2️⃣ Web page\nOur ovens start at 5am.\n\n3️⃣ Text\nLoaves from 7am. Reply STOP to opt out")
    res = paste.split(text, [GOOG, WEB, TXT])
    assert got(res) == {"g": "Headline 1: Fresh loaves daily", "w": "Our ovens start at 5am.",
                        "t": "Loaves from 7am. Reply STOP to opt out"}
    assert res.problems == []


def test_emoji_before_a_bold_heading_with_a_blank_line_after():
    text = "\U0001F4F8 **Instagram**\n\nAutumn bulbs are in.\n\n✉️ **Email**\n\nSubject: Bulbs\n\nHello,\nThey are in."
    assert got(paste.split(text, [IG, EM])) == {"ig": "Autumn bulbs are in.", "em": "Subject: Bulbs\n\nHello,\nThey are in."}


def test_blog_piece_starts_at_its_title_line():
    text = ("### 1. Website\nClasses start on 3 May.\nBook online.\n\nTitle: How to pick a class\n\n"
            "Not sure where to start? Try a taster.\n\nHope that helps!")
    res = paste.split(text, [WEB, BLOG])
    assert got(res)["b"].startswith("Title: How to pick a class") and got(res)["w"].endswith("Book online.")
    assert any("title" in p for p in res.problems)


def test_heading_without_a_channel_then_a_title_line():
    text = ("Sure! Here you go.\n\n## Studio intro\n\nPaint with us.\nClasses weekly.\n\n"
            "Oils or acrylics? Picking your first paints\n\nStart with acrylics.\nThey dry fast.\n\nLet me know!")
    res = paste.split(text, [WEB, BLOG])
    assert got(res) == {"w": "Paint with us.\nClasses weekly.",
                        "b": "Oils or acrylics? Picking your first paints\n\nStart with acrylics.\nThey dry fast."}
    assert len(res.problems) == 2


@pytest.mark.parametrize("middle", [
    "First idea for a title\n\nSome text.\n\nSecond idea for a title\n\nMore text.",   # two candidates: no guess
    "Hi there\n\nSome text.",                                                           # a greeting, not a title
    "Some text follows here.\n\nMore text.",                                            # a sentence, not a title
])
def test_title_rule_negative_controls(middle):
    res = paste.split(f"### 1. Website\nClasses start on 3 May.\n\n{middle}", [WEB, BLOG])
    assert "b" not in got(res)
