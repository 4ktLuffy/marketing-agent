"""Eighth round (invented businesses, not the eval companies).

Disclosure equivalents (a curated table, both sides, opposites kept apart), short questions and
speaker / caption labels that never block without a value, idioms that are not superlatives,
headings on a paragraph of their own, and the STRONG rules: a quantity carried over from another
plan's sentence, identifiers written in groups, star numbers, 24/7 against set hours, "N + 1" deals
said without "buy", expired names said in part, membership grades, a tier after a body's acronym,
"Class A" for a Euroclass, what only another plan includes, and unsourced 24/7 / same-day /
years-of-experience claims (never blocking). Every rule has negative controls."""
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


def labels(findings):
    return [(f["label"], f["fact_key"]) for f in findings]


def price(key, ref, disc, value=40, text=None, **kw):
    return fact(key, text or f"The {ref} costs £{value}.", f"£{value}", value=value, currency="GBP",
                fact_type="price", subject={"kind": kw.pop("kind", "package"), "ref": ref},
                required_disclosures=[disc], **kw)


# ======================================================================= disclosure equivalents

@pytest.mark.parametrize("disc,text,ok", [
    # per person
    ("per person", "The Harbour Supper Club is £40pp.", True),
    ("per person", "The Harbour Supper Club is £40 p.p.", True),
    ("per person", "The Harbour Supper Club is £40 each.", True),
    ("per person", "The Harbour Supper Club is £40 a head.", True),
    ("per person", "The Harbour Supper Club is £40 per head.", True),
    ("per person", "At the Harbour Supper Club each guest pays £40.", True),
    ("per person", "The Harbour Supper Club is £40 per adult.", True),
    ("per person", "The Harbour Supper Club is £40 per table.", False),
    ("per person", "The Harbour Supper Club is £40, and each course is seasonal.", False),
    # VAT
    ("excl. VAT", "The Harbour Supper Club is £40 (VAT extra).", True),
    ("excl. VAT", "The Harbour Supper Club is £40 before VAT.", True),
    ("excl. VAT", "The Harbour Supper Club is £40 + VAT.", True),
    ("excl. VAT", "The Harbour Supper Club is £40 ex VAT.", True),
    ("excl. VAT", "The Harbour Supper Club is £40, VAT included.", False),
    ("excl. VAT", "The Harbour Supper Club is £40 including VAT.", False),
    ("incl. VAT", "The Harbour Supper Club is £40, VAT included.", True),
    ("incl. VAT", "The Harbour Supper Club is £40 inc VAT.", True),
    ("incl. VAT", "The Harbour Supper Club is £40, VAT not included.", False),
    ("incl. VAT", "The Harbour Supper Club is £40 plus VAT.", False),
    ("incl. VAT", "The Harbour Supper Club is £40, no VAT.", False),
    # per night / month / year
    ("per night", "The Harbour Supper Club is £40 a night.", True),
    ("per night", "The Harbour Supper Club is £40 nightly.", True),
    ("per night", "The Harbour Supper Club is £40 for a night out.", False),
    ("per month", "The Harbour Supper Club is £40/month.", True),
    ("per month", "The Harbour Supper Club is £40 pm.", True),
    ("per month", "The Harbour Supper Club is £40 monthly.", True),
    ("per month", "The Harbour Supper Club is £40 a month.", True),
    ("per month", "The Harbour Supper Club is £40, doors at 7pm.", False),
    ("per month", "The Harbour Supper Club is £40 a year.", False),
    ("per year", "The Harbour Supper Club is £40 annually.", True),
    ("per year", "The Harbour Supper Club is £40/yr.", True),
    ("per year", "The Harbour Supper Club is £40 a year.", True),
    ("per year", "The Harbour Supper Club is £40 a month.", False),
    # a price "from"
    ("from £40", "The Harbour Supper Club starts at £40.", True),
    ("from £40", "Harbour Supper Club prices start at £40.", True),
    ("from £40", "The Harbour Supper Club is £40.", False),
    # subject to survey / status / availability, terms, stock
    ("subject to a survey", "The Harbour Supper Club is £40, fixed once we've surveyed.", True),
    ("subject to a survey", "The Harbour Supper Club is £40, confirmed after a survey.", True),
    ("subject to a survey", "The Harbour Supper Club is £40, no survey needed.", False),
    ("subject to a survey", "The Harbour Supper Club is £40, without a survey.", False),
    ("final price confirmed after a free survey",
     "The Harbour Supper Club is £40, with the exact figure fixed once we've surveyed it for free.", True),
    ("final price confirmed after a free survey", "The Harbour Supper Club is £40 after a survey.", False),
    ("subject to status", "The Harbour Supper Club is £40, depending on your credit status.", True),
    ("subject to status", "The Harbour Supper Club is £40, whatever your status.", False),
    ("subject to availability", "The Harbour Supper Club is £40, depending on availability.", True),
    ("subject to availability", "The Harbour Supper Club is £40, always available.", False),
    ("terms apply", "The Harbour Supper Club is £40. T&Cs apply.", True),
    ("terms apply", "The Harbour Supper Club is £40. Ts&Cs apply.", True),
    ("terms apply", "The Harbour Supper Club is £40, no strings.", False),
    ("while stocks last", "The Harbour Supper Club is £40 while supplies last.", True),
    # minimum term
    ("12-month minimum term", "The Harbour Supper Club is £40, minimum term of 12 months.", True),
    ("12-month minimum term", "The Harbour Supper Club is £40, min. 12 months.", True),
    ("12-month minimum term", "The Harbour Supper Club is £40, twelve-month minimum contract.", True),
    ("12-month minimum term", "The Harbour Supper Club is £40, no minimum term.", False),
    ("12-month minimum term", "The Harbour Supper Club is £40, minimum term of 6 months.", False),
    ("12-month minimum term", "The Harbour Supper Club is £40 on a 12-month contract.", False),
])
def test_disclosure_equivalents(disc, text, ok):
    f = price("supper", "Harbour Supper Club", disc)
    missing = ("missing_disclosure", "supper") in blocking(run(text, [f]))
    assert missing is not ok, (disc, text)


def test_other_disclosure_concepts_are_not_rewritten():
    # "survey of 120 clients" is a result disclosure, not "subject to a survey"
    assert evidence.disclosure_said("survey of 120 clients, results vary",
                                    "After a survey of 120 clients, results vary.")
    # "each way" is not "per person"
    assert not evidence.disclosure_said("per person, each way", "£49 each way")
    assert evidence.disclosure_said("per person, each way", "£49 per person, each way")
    # "from order confirmation" (a lead time) is not a price "from"
    assert evidence.disclosure_said("from order confirmation", "Ready in 3 days from order confirmation.")


# ======================================================================= short questions and labels

DEAL = fact("board-deal", "Board Bonanza: buy 2 surfboards and get the 3rd free, for orders by 30 April 2031.",
            "buy 2 surfboards, get the 3rd free", subject={"kind": "offer", "ref": "Board Bonanza"},
            fact_type="price", attribute="multibuy", valid_from="2031-03-01", valid_to="2031-04-30")
PCT = fact("spring-pct", "Spring Swell: 20% off wetsuits until 30 April 2031.", "20% off wetsuits",
           subject={"kind": "offer", "ref": "Spring Swell"}, fact_type="price", value=20, unit="%",
           valid_from="2031-03-01", valid_to="2031-04-30")


@pytest.mark.parametrize("text", ["VO: Two surfboards?", "Caption: Buy two, get a third?", "Two surfboards, a third free?"])
def test_short_question_or_label_does_not_block(text):
    assert blocking(run(text, [DEAL])) == []


@pytest.mark.parametrize("text", [
    "VO: Is Board Bonanza back?",                      # the fact's full name
    "Caption: 20% off wetsuits?",                      # an exact value
    "Buy 2 surfboards and get the 3rd free this month?",   # more than six words
    "Buy 2 surfboards and get the 3rd free this month.",   # not a question
])
def test_short_line_with_a_name_or_value_or_long_still_blocks(text):
    assert blocking(run(text, [DEAL, PCT])) != []


def test_light_line_shapes():
    assert evidence.light_line("VO: Two A-boards?")
    assert evidence.light_line("Still on the fence?")
    assert evidence.light_line("On screen: Autumn boxes")
    assert not evidence.light_line("Subject: Autumn boxes")            # a subject line is copy, not a label
    assert not evidence.light_line("Is a weekday spa day the very best thing for you?")   # 10 words


# ======================================================================= idioms

AWARD = fact("award", "Kittiwake Café won Best Café in Cornwall 2030.", "Best Café in Cornwall 2030",
             subject={"kind": "business", "ref": "Kittiwake Café"}, fact_type="result", claim_class="comparative")


@pytest.mark.parametrize("text", [
    "Is a midweek brunch the best-kept secret?",
    "Our garden is the worst-kept secret in town.",
    "Even the best-laid plans need a backup brunch.",
    "Make the best of the sunshine on our terrace.",
    "Our crab sandwich is the best thing since sliced bread.",
])
def test_idioms_are_not_superlative_claims(text):
    assert blocking(run(text, [])) == []


def test_superlatives_still_need_a_fact():
    assert ("no_source", None) in blocking(run("Kittiwake is the best café in Falmouth.", []))
    assert blocking(run("Kittiwake is the fastest-growing café in Cornwall.", [])) != []


# ======================================================================= splitter: headings on their own paragraph

PIECES = [{"key": "p1", "channel": "instagram", "kind": "caption"}, {"key": "p2", "channel": "pinterest", "kind": "pin"},
          {"key": "p3", "channel": "email", "kind": "newsletter"}]


def test_heading_paragraphs_with_emoji_or_heading_words_split():
    text = ("Here's your content 🌿\n\n📸 Instagram caption\n\nCalm starts here.\nBook now.\n\n"
            "*📌 Pinterest Pin*\n\nTitle: A calm day\nDescription: Unwind.\n\nSubject: Your escape\n\nHello,\n\nSee you.\n\n"
            "Want a Stories version too?")
    res = paste.split(text, PIECES)
    got = {p["piece_key"]: p["text"] for p in res.split}
    assert got["p1"].startswith("Calm starts here.") and "Title:" not in got["p1"]
    assert got["p2"].startswith("Title: A calm day") and "Subject" not in got["p2"]
    assert got["p3"].startswith("Subject: Your escape")


def test_a_lone_channel_word_paragraph_is_not_a_heading():
    text = ("=== 1 INSTAGRAM ===\nWe post daily on\n\nInstagram\n\nso follow us.\n\n=== 2 PINTEREST ===\nPins.\n\n"
            "=== 3 EMAIL ===\nSubject: Hi\n\nHello.")
    res = paste.split(text, PIECES)
    assert "Instagram" in {p["piece_key"]: p["text"] for p in res.split}["p1"]
    assert not any("appears twice" in p for p in res.problems)


# ======================================================================= a quantity carried over from another plan

WEEKDAY = fact("weekday", "The Weekday Swim Pass costs £30 and includes a 20-minute sauna session.", "£30",
               value=30, currency="GBP", fact_type="price", subject={"kind": "package", "ref": "Weekday Swim Pass"},
               variants=["weekday"])
WEEKEND = fact("weekend", "The Weekend Swim Pass costs £45 and includes a 45-minute massage of your choice.", "£45",
               value=45, currency="GBP", fact_type="price", subject={"kind": "package", "ref": "Weekend Swim Pass"},
               variants=["weekend"])
V_WEEKDAY = {"variants": ["weekday"]}


def test_a_quantity_only_another_scope_says_with_its_noun_is_wrong_scope():
    got = blocking(run("Every Weekday Swim Pass now includes a 45-minute massage.", [WEEKDAY, WEEKEND], V_WEEKDAY))
    assert ("wrong_scope", "weekend") in got


@pytest.mark.parametrize("text", [
    "Every Weekday Swim Pass now includes a 45-minute swim lesson.",   # another noun
    "Every Weekday Swim Pass now includes a 50-minute swim.",          # another number, not the noun
    "Every Weekday Swim Pass includes a 20-minute sauna session.",     # this scope's own
])
def test_quantity_controls(text):
    assert blocking(run(text, [WEEKDAY, WEEKEND], V_WEEKDAY)) == []


# ======================================================================= identifiers written in groups

LICENCE = fact("licence", "Tern Bookkeeping is an AAT licensed practice, licence number 3049712.",
               "AAT licensed practice, licence number 3049712", fact_type="credential",
               subject={"kind": "business", "ref": "Tern Bookkeeping"})
ORGANIC = fact("organic", "Our lamb is certified organic, licence K8841.", "certified organic, licence K8841",
               fact_type="certification", subject={"kind": "business", "ref": "Tern Farm"})
REPORT = fact("report", "Our panels are tested, test report TRP-FR-5510.", "test report TRP-FR-5510",
              fact_type="certification", subject={"kind": "variant", "ref": "FR panel"})
ESTAB = fact("estab", "Tern Inn is an AA Four Star Inn, AA establishment ID 8812034.",
             "AA Four Star Inn, AA establishment ID 8812034", value=4, fact_type="credential",
             attribute="aa_rating", subject={"kind": "business", "ref": "Tern Inn"})


@pytest.mark.parametrize("text,key", [
    ("We are AAT licensed, licence 304·97·21.", "licence"),
    ("We are AAT licensed, licence number 30-497/21.", "licence"),
    ("Certified organic, licence K/88-14.", "organic"),
    ("Test report TRP–FR–5501, if you want it.", "report"),
    ("We're an AA-rated inn, establishment ID 881-20-43.", "estab"),
])
def test_grouped_identifier_with_other_digits_conflicts(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text, [LICENCE, ORGANIC, REPORT, ESTAB]))


@pytest.mark.parametrize("text", [
    "We are AAT licensed, licence 304·97·12.", "Certified organic, licence K/88-41.",
    "Test report TRP–FR–5510, if you want it.", "Establishment ID 881-20-34.",
])
def test_grouped_identifier_with_the_same_digits_matches(text):
    assert blocking(run(text, [LICENCE, ORGANIC, REPORT, ESTAB])) == []


# ======================================================================= star numbers

HYGIENE = fact("hygiene", "The café holds a food hygiene rating of 4 (Good).", "food hygiene rating of 4 (Good)",
               value=4, fact_type="credential", attribute="food_hygiene_rating",
               subject={"kind": "site", "ref": "Tern Café"})


@pytest.mark.parametrize("text,key", [
    ("Proud holders of a five-star food hygiene rating.", "hygiene"),
    ("Relax at our five-star inn.", "estab"),
    ("We're rated five stars by the AA.", "estab"),
])
def test_star_number_in_words_conflicts(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text, [HYGIENE, ESTAB]))


@pytest.mark.parametrize("text", ["A four-star food hygiene rating.", "Relax at our four-star inn.",
                                  "Rated four stars by the AA."])
def test_star_number_that_agrees_matches(text):
    assert blocking(run(text, [HYGIENE, ESTAB])) == []


def test_star_number_about_another_thing_is_not_this_fact():
    assert ("conflict_or_expired", "estab") not in blocking(run("Our five-star breakfast reviews speak for us.", [ESTAB]))


# ======================================================================= 24/7 against set hours

SUPPORT = fact("support", "Support is available Monday to Friday, 9am to 5pm, by phone.",
               "Monday to Friday, 9am to 5pm", fact_type="hours", attribute="support_hours",
               subject={"kind": "service", "ref": "Customer support"})


def test_24_7_against_set_support_hours_conflicts():
    assert ("conflict_or_expired", "support") in blocking(run("Our support team is available 24/7.", [SUPPORT]))


@pytest.mark.parametrize("text", ["Book a table online 24/7.", "Our support team answers any time of the night."])
def test_24_7_about_something_else_or_a_hint_does_not(text):
    assert blocking(run(text, [SUPPORT])) == []


# ======================================================================= N + 1 deals said without "buy"

MONTHS = fact("switch", "Switch Deal: pay for 2 months and get the 3rd free, for sign-ups by 30 April 2031.",
              "pay for 2 months, get the 3rd free", subject={"kind": "offer", "ref": "Switch Deal"},
              fact_type="price", attribute="multibuy", valid_from="2031-03-01", valid_to="2031-04-30")


@pytest.mark.parametrize("text", ["Your third month is on us when you switch.",
                                  "Your first three months cost the price of two."])
def test_deal_said_without_buy_is_the_expired_offer(text):
    assert ("conflict_or_expired", "switch") in blocking(run(text, [MONTHS]))


@pytest.mark.parametrize("text", ["Your first month is on us.", "Your second month is on us.",
                                  "Three lessons for the price of three."])
def test_other_deals_are_not_it(text):
    assert blocking(run(text, [MONTHS])) == []


# ======================================================================= expired names said in part

TWILIGHT = fact("twilight", "The Summer Twilight Swim (£20, 6pm to 9pm) ran June to August 2030.", "£20",
                value=20, currency="GBP", fact_type="price", subject={"kind": "package", "ref": "Summer Twilight Swim"},
                valid_from="2030-06-01", valid_to="2030-08-31", status="expired")
PASTY = fact("pasty", "Pasty Pile: buy 3 pasties, get the 4th free, until 30 April 2031.",
             "buy 3 pasties, get the 4th free", subject={"kind": "offer", "ref": "Pasty Pile"}, fact_type="price",
             attribute="multibuy", valid_from="2031-03-01", valid_to="2031-04-30")
GLOW = fact("glow", "The Glow Swim costs £15.", "£15", value=15, currency="GBP", fact_type="price",
            subject={"kind": "service", "ref": "Glow Swim"})
AUTUMN = fact("autumn-glow", "Autumn Glow Swim offer: 20% off the Glow Swim until 30 April 2031.",
              "20% off the Glow Swim", subject={"kind": "offer", "ref": "Autumn Glow Swim offer"}, fact_type="price",
              value=20, unit="%", valid_from="2031-03-01", valid_to="2031-04-30")


@pytest.mark.parametrize("text,key", [("Twilight swim evenings are back.", "twilight"),
                                      ("Our pasty deal is running all month.", "pasty")])
def test_expired_name_said_in_part(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text, [TWILIGHT, PASTY, GLOW, AUTUMN]))


@pytest.mark.parametrize("text", ["Your autumn Glow Swim is waiting.", "Evening swims are back.",
                                  "Our summer deal is running all month."])
def test_season_words_and_valid_names_are_not_the_expired_one(text):
    assert blocking(run(text, [TWILIGHT, PASTY, GLOW, AUTUMN])) == []


# ======================================================================= membership grade, tier, short class

MEMBER = fact("aat", "Tern Bookkeeping is led by Asha Rendle MAAT, a full member of the AAT.",
              "led by Asha Rendle MAAT", fact_type="credential", subject={"kind": "business", "ref": "Tern Bookkeeping"})
NFRC = fact("nfrc", "Tern Roofing is an NFRC Full Contractor Member, membership number 41872.",
            "NFRC Full Contractor Member, membership number 41872", fact_type="credential",
            subject={"kind": "business", "ref": "Tern Roofing"})
EURO = fact("euro", "Our FR panels are classified Euroclass B-s1,d0 to EN 13501-1.", "Euroclass B-s1,d0 to EN 13501-1",
            fact_type="certification", subject={"kind": "variant", "ref": "FR panel"})


def test_membership_grade_of_the_same_person_and_body():
    assert ("conflict_or_expired", "aat") in blocking(run("Led by Asha Rendle, a Fellow of the AAT.", [MEMBER]))
    assert blocking(run("Led by Asha Rendle, a full member of the AAT.", [MEMBER])) == []
    assert blocking(run("Our adviser is a Fellow of the AAT.", [MEMBER])) == []     # no person named: not this fact


def test_tier_after_a_body_acronym():
    assert ("conflict_or_expired", "nfrc") in blocking(run("Proud NFRC Platinum Partner.", [NFRC]))
    assert blocking(run("Proud NFRC Full Contractor Member.", [NFRC])) == []


def test_class_letter_for_a_euroclass():
    assert ("conflict_or_expired", "euro") in blocking(run("Our panels carry the top fire classification, Class A.", [EURO]))
    assert blocking(run("Our panels carry fire classification Class B.", [EURO])) == []
    assert blocking(run("Class A service for your shop.", [EURO])) == []            # nothing ties it to the fact


# ======================================================================= what only another plan includes

SOLO = fact("solo", "The Solo plan costs £12 a month, for one owner and no staff.", "£12 a month", value=12,
            currency="GBP", unit="month", fact_type="price", subject={"kind": "plan", "ref": "Solo"}, plan_tiers=["solo"])
TEAM = fact("team", "The Team plan costs £30 a month and includes payroll.", "£30 a month", value=30, currency="GBP",
            unit="month", fact_type="price", subject={"kind": "plan", "ref": "Team"}, plan_tiers=["team"])
P_SOLO = {"plan_tiers": ["solo"]}


@pytest.mark.parametrize("text", ["Payroll is now included for solo traders.", "Run payroll from the app, even on the Solo plan."])
def test_what_only_another_plan_includes(text):
    assert ("wrong_scope", "team") in blocking(run(text, [SOLO, TEAM], P_SOLO))


@pytest.mark.parametrize("text", ["No payroll on this plan, just simple books.", "Payroll questions? Ask us.",
                                  "Our payroll guide explains the rules."])
def test_payroll_mentioned_otherwise(text):
    assert blocking(run(text, [SOLO, TEAM], P_SOLO)) == []


# ======================================================================= unsourced promises (never blocking)

@pytest.mark.parametrize("text,frag", [
    ("Our roofers are on call 24 hours a day.", "24 hours a day"),
    ("Same-day installation across the city.", "Same-day installation"),
    ("Our butchers bring over 40 years of experience.", "40 years of experience"),
    ("Our in-house design team works late.", "in-house design team"),
])
def test_unsourced_promises_are_no_source_not_blocking(text, frag):
    got = run(text, [])
    assert any(f["label"] == "no_source" and frag in f["detail"] for f in got)
    assert blocking(got) == []


def test_promises_a_fact_states_are_not_unsourced():
    facts = [fact("sd", "We offer same-day delivery across the city.", "same-day delivery", fact_type="spec"),
             fact("yrs", "Tern has 40 years of experience in roofing.", "40 years of experience", fact_type="claim"),
             fact("tm", "Our in-house design team prepares every proof.", "in-house design team", fact_type="spec")]
    for text in ("Same-day delivery across the city.", "Over 40 years of experience.", "Our in-house design team helps."):
        assert not any(f["label"] == "no_source" for f in run(text, facts)), text
