"""Round 12 evidence rules (invented businesses, not the eval companies). Every rule has negative
controls.

- amounts and percentages written in words are values like "£36" / "96%": "nineteen quid", "forty-two
  pounds", "one hundred and twenty-five pounds", "a thousand quid", "seventy-five per cent", "half a
  percent"; the whole number is read ("twenty-five quid" is 25, never 5); "one of our", "a couple of",
  "first", "ten pounds of flour" and "a hundred per cent sure" are not values;
- a closing hour with no am / pm in a sentence about opening hours ("open till nine") is an evening hour;
- a round ceiling ("for less than fifty quid") stands for a fact's price just under it (within 10%);
  STRONG only when the sentence is about that fact;
- free said otherwise ("on the house", "for nothing", "won't cost you a penny", "thrown in") is a
  benefit; a valid benefit said by its own object uses that fact (its disclosures apply); a stale /
  out-of-scope benefit is found by its head word or one distinctive word of its own name;
- "two for one" / "2-for-1" / "buy one get one free" is the 1 + 1 deal;
- disclosures in other words: a minimum age ("18+", "aged eighteen or over"), eligibility ("if you
  qualify"), Monday to Friday ("weekdays", "Mon–Fri") and a bare hours range after a day ("weekdays
  8–6"), a survey with two adjectives ("after a free home survey") and free said otherwise ("which
  costs nothing"), new customers ("if you haven't been to us before", "newcomers"), a confirmed booking
  ("once you've booked"), contents that vary ("what goes in changes"), a word amount "each";
- a staff credential named by its body or school ("DBS-checked", "Montessori-trained") needs a fact;
  "well-trained" / "fully-trained" are not credentials;
- a track record count ("12,000 happy moves", "3,400 weddings since 2009") needs a fact.
"""
from datetime import date

import pytest

from app import evidence

from .data import fact

DAY = date(2031, 5, 12)


def run(text, facts, scope=None):
    findings, _ = evidence.check_text(text, facts, DAY, scope or {})
    return findings


def blocking(findings):
    return [(f["label"], f["fact_key"]) for f in findings if f["blocking"]]


def values(text):
    return [v.key for v in evidence.extract(text)]


# ======================================================================= amounts and percentages in words

@pytest.mark.parametrize("text,key", [
    ("A cut and blow-dry is nineteen quid.", ("money", "19", "GBP")),
    ("Forty-two pounds covers the lot.", ("money", "42", "GBP")),
    ("It costs one hundred and twenty-five pounds.", ("money", "125", "GBP")),
    ("Yours for a hundred and ten quid.", ("money", "110", "GBP")),
    ("That is two thousand three hundred and forty euros.", ("money", "2340", "EUR")),
    ("A thousand quid, all in.", ("money", "1000", "GBP")),
    ("Only sixty p a cup.", ("money", "0.6", "GBP")),
    ("Seventy-five per cent of members renew.", ("percent", "75")),
    ("It is half a percent ABV.", ("percent", "0.5")),
])
def test_amounts_in_words_are_values(text, key):
    assert key in values(text)


@pytest.mark.parametrize("text", [
    "One of our stylists will call you.",
    "It takes a couple of hours.",
    "Your first visit is on a Tuesday.",
    "Add ten pounds of flour to the mix.",
    "We are a hundred per cent sure you'll love it.",
])
def test_number_words_without_money_are_not_values(text):
    assert not [k for k in values(text) if k[0] in ("money", "percent")]


def test_the_whole_compound_number_is_read():
    assert values("Twenty-five quid a head.") == [("money", "25", "GBP")]


SALON = [
    fact("cut-price", "A cut and blow-dry at Mirelle costs £42.", "£42", subject={"kind": "service", "ref": "Cut and blow-dry"},
         fact_type="price", value=42, currency="GBP", required_disclosures=["with a senior stylist"]),
    fact("spring-cut", "Spring cut offer: £19 until 30 April.", "£19 spring cut", subject={"kind": "offer", "ref": "Spring cut"},
         fact_type="price", value=19, currency="GBP", valid_to="2031-04-30"),
    fact("renewal", "75% of members renewed in 2030.", "75% renewal rate", subject={"kind": "business", "ref": "Mirelle"},
         fact_type="result", value=75, unit="%", claim_class="result", required_disclosures=["members in 2030"]),
]


def test_a_stale_price_in_words_conflicts():
    assert ("conflict_or_expired", "spring-cut") in blocking(run("A cut is only nineteen quid this month.", SALON))


def test_a_valid_price_in_words_carries_its_disclosure():
    assert ("missing_disclosure", "cut-price") in blocking(run("Forty-two pounds for a cut and blow-dry.", SALON))
    assert not blocking(run("Forty-two pounds for a cut and blow-dry with a senior stylist.", SALON))


def test_a_percentage_in_words_carries_its_disclosure():
    assert ("missing_disclosure", "renewal") in blocking(run("Seventy-five per cent of our members renew.", SALON))


# ======================================================================= a closing hour said without am / pm

HOURS = [
    fact("late-night", "Only the Quay Street shop stays open until 9pm on weekdays.", "open until 9pm on weekdays",
         subject={"kind": "site", "ref": "Quay Street"}, fact_type="hours", sites=["quay-street"]),
]


def test_a_closing_hour_in_words_is_an_evening_time():
    assert ("wrong_scope", "late-night") in blocking(
        run("All our shops stay open till nine on weeknights.", HOURS, {"sites": ["mill-lane"]}))
    assert ("time", "21:00") in values("We're open until nine.")


@pytest.mark.parametrize("text", [
    "Stay with us until 6 months have passed.",
    "Offers run till nine in the morning.",
    "Wait until ten, then add the eggs.",
])
def test_a_number_after_until_is_not_always_a_closing_hour(text):
    assert not [k for k in values(text) if k[0] == "time"]


# ======================================================================= a round ceiling for a price just under it

MOVERS = [
    fact("student-van", "The £49 student man-and-van offer ended in March.", "£49 student man-and-van move",
         subject={"kind": "offer", "ref": "Student man-and-van"}, fact_type="price", value=49, currency="GBP",
         valid_to="2031-03-31"),
    fact("crate-hire", "Crate hire costs £40 a week.", "£40 a week", subject={"kind": "service", "ref": "Crate hire"},
         fact_type="price", value=40, currency="GBP"),
]


def test_under_a_round_amount_is_the_offer_just_below_it():
    assert ("conflict_or_expired", "student-van") in blocking(run("Students: move for less than fifty quid!", MOVERS))


def test_under_a_round_amount_untied_is_only_review():
    found = run("Everything for under £50.", MOVERS)
    assert ("conflict_or_expired", "student-van") not in blocking(found)
    assert any(f["label"] == "review" and f["fact_key"] == "student-van" for f in found)


def test_a_price_more_than_ten_percent_below_is_not_linked():
    found = run("Crate hire for under £50.", MOVERS[1:])
    assert not any(f["label"] == "match" for f in found)


# ======================================================================= free said otherwise

CAFE = [
    fact("free-refill", "Filter coffee refills are free with any breakfast.", "free coffee refills",
         subject={"kind": "offer", "ref": "Coffee refills"}, fact_type="inclusion",
         required_disclosures=["with any breakfast"]),
    fact("pastry-tasting", "The free pastry tasting ran in January.", "free pastry tasting",
         subject={"kind": "offer", "ref": "Free pastry tasting"}, fact_type="availability", valid_to="2031-01-31"),
    fact("corporate-delivery", "Corporate customers get office delivery at no extra charge.",
         "office delivery at no extra charge", subject={"kind": "service", "ref": "Office delivery"}, fact_type="policy",
         segments=["corporate"]),
]


@pytest.mark.parametrize("text", [
    "Your coffee refills are on the house.",
    "Coffee refills for nothing!",
    "Refills won't cost you a penny.",
    "Breakfast from £8, with coffee refills thrown in.",
])
def test_a_valid_benefit_said_otherwise_carries_its_disclosure(text):
    assert ("missing_disclosure", "free-refill") in blocking(run(text, CAFE))


def test_a_valid_benefit_with_its_disclosure_is_clean():
    assert not blocking(run("Coffee refills are on the house with any breakfast.", CAFE))


def test_a_stale_benefit_found_by_a_distinctive_word_of_its_name():
    assert ("conflict_or_expired", "pastry-tasting") in blocking(run("Try a warm pastry for nothing this week.", CAFE))


def test_an_out_of_scope_benefit_said_otherwise():
    assert ("wrong_scope", "corporate-delivery") in blocking(
        run("Home delivery won't cost you a penny more.", CAFE, {"segments": ["retail"]}))


@pytest.mark.parametrize("text", [
    "Thanks for nothing, rain.",
    "You can count on us.",
    "Nothing beats a warm pastry.",
])
def test_free_idioms_carry_no_benefit(text):
    assert not blocking(run(text, CAFE))


# ======================================================================= two for one

PUB = [
    fact("pie-deal", "In March the Crown served pies 2-for-1 on Mondays.", "2-for-1 pies",
         subject={"kind": "offer", "ref": "Pie Monday"}, fact_type="price", valid_to="2031-03-31"),
]


@pytest.mark.parametrize("text", ["Pies, two for one, every Monday.", "Buy one, get one free on pies.",
                                  "Our famous pies are 2 for 1 this week."])
def test_two_for_one_is_the_stale_deal(text):
    assert ("conflict_or_expired", "pie-deal") in blocking(run(text, PUB))


@pytest.mark.parametrize("text", ["Book 2 for 1 hour.", "Pies from £4, one for you and one for a friend."])
def test_two_for_one_negative_controls(text):
    assert ("conflict_or_expired", "pie-deal") not in blocking(run(text, PUB))


# ======================================================================= disclosures in other words

@pytest.mark.parametrize("disclosure,text", [
    ("adults 18 and over", "Tastings for anyone aged eighteen or over."),
    ("adults 18 and over", "Gin tasting £30 (18+)."),
    ("adults 21 and over", "Over-21s only at the tasting."),
    ("if you're eligible", "If you qualify, the check-up is free."),
    ("if you're eligible", "Patients who meet the criteria pay nothing."),
    ("Mon–Fri, 9am–5pm", "Phone lines open weekdays 9–5."),
    ("Mon–Fri, 9am–5pm", "Call on a weekday between 9am and 5pm."),
    ("Monday to Friday", "Open Mon-Fri."),
    ("quote confirmed after a free site survey", "We fix the price once we've surveyed your site, which costs nothing."),
    ("quote confirmed after a free site survey", "final price after a free survey at your site"),
    ("for new members", "If you haven't trained with us before, your first class is free."),
    ("for new members", "Newcomers get a free class."),
    ("for new members", "First-time members get a free class."),
    ("with a confirmed booking", "Once you've booked, the welcome pack is yours."),
    ("with a confirmed booking", "Book with us and get a free welcome pack."),
    ("contents vary with the season", "What's inside changes with the seasons."),
    ("per person", "Sixty pounds each for the full menu."),
])
def test_disclosures_in_other_words(disclosure, text):
    assert evidence.disclosure_said(disclosure, text)


@pytest.mark.parametrize("disclosure,text", [
    ("adults 18 and over", "Tastings for anyone aged sixteen or over."),
    ("adults 18 and over", "Adults welcome."),
    ("adults 18 and over", "Over 4+ years of tasting experience."),
    ("if you're eligible", "Free check-ups for everyone."),
    ("Mon–Fri, 9am–5pm", "Phone lines open weekends 9–5."),
    ("Mon–Fri, 9am–5pm", "Phone lines open weekdays 8–6."),
    ("quote confirmed after a free site survey", "Quote confirmed after a paid survey."),
    ("for new members", "For existing members only."),
    ("for new members", "Not for new members."),
    ("with a confirmed booking", "Book now."),
    ("contents vary with the season", "The contents are the same all year."),
    ("per person", "Sixty pounds for the table."),
])
def test_disclosures_negative_controls(disclosure, text):
    assert not evidence.disclosure_said(disclosure, text)


# ======================================================================= credentials named by their body

@pytest.mark.parametrize("text", [
    "All our coaches are DBS-checked.",
    "Taught by Montessori-trained teachers.",
    "Every one of our instructors is police-vetted.",
])
def test_a_named_credential_needs_a_fact(text):
    assert ("no_source", None) in blocking(run(text, SALON))


def test_a_named_credential_backed_by_a_fact():
    f = fact("dbs", "All Kickstart coaches hold an enhanced DBS check.", "DBS checked coaches",
             subject={"kind": "business", "ref": "Kickstart"}, fact_type="credential", attribute="dbs")
    assert not blocking(run("All our coaches are DBS-checked.", [f]))


@pytest.mark.parametrize("text", [
    "Our instructors are well-trained and friendly.",
    "All our staff are fully-trained.",
    "Bring a well-trained dog.",
    "A self-trained ear helps.",
])
def test_puffery_is_not_a_named_credential(text):
    assert ("no_source", None) not in blocking(run(text, SALON))


# ======================================================================= a track record count

@pytest.mark.parametrize("text", [
    "Over 12,000 happy haircuts and counting.",
    "3,400 weddings since 2009.",
    "More than 250 satisfied installs.",
])
def test_a_track_record_count_needs_a_fact(text):
    assert ("no_source", None) in blocking(run(text, SALON))


@pytest.mark.parametrize("text", [
    "3 new stylists since 2029.",
    "We opened in 1998.",
    "12 happy staff greet you.",
])
def test_track_record_negative_controls(text):
    assert ("no_source", None) not in blocking(run(text, SALON))
