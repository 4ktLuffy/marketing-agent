"""Round 10 evidence rules (invented businesses, not the eval companies). Precision first.

"approved by <a time / day / date>" is a deadline, not an approving body; "free" meaning available
("pitches free right now"); disclosures said in other words: not part of the price ("bought
separately", "an additional purchase", "paid for on top"), paid in advance ("upfront", "prepaid"),
a minimum term said as how long the customer commits ("stay at least three nights", "sign up for six
months at a time", "a one-year sign-up"), a deadline time ("by 2pm" = "before 14:00", "12 noon" =
"midday", "sign off" = "approve"); a per-person price said as the price for a group ("for you and a
friend"); "tomorrow" = "the next day" for a valid fact's own value words. Every rule has negative
controls."""
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


# ======================================================================= "approved by" a deadline

KILN = [fact("hours", "Emberfold Pottery is open 10am to 4pm.", "10am to 4pm",
             subject={"kind": "business", "ref": "Emberfold Pottery"}, fact_type="hours")]


@pytest.mark.parametrize("text", [
    "Glaze choices approved by 3pm are fired the same week.",
    "Designs approved by 11:30 go into Thursday's kiln.",
    "Mock-ups approved by Friday are ready the following Tuesday.",
    "Orders approved by the end of the day ship next week.",
    "Custom plates approved by 4 June arrive in July.",
    "Anything approved by midday is packed that afternoon.",
    "Proofs approved by 9 a.m. leave the studio at lunch.",
])
def test_approved_by_a_deadline_is_not_a_claim(text):
    assert not blocking(run(text, KILN))


@pytest.mark.parametrize("text", [
    "Our glazes are approved by the Craft Potters Guild.",
    "Every mug is approved by 14 local councils.",
    "Approved by the Food Standards Agency.",
])
def test_approved_by_a_body_still_needs_a_fact(text):
    assert ("no_source", None) in blocking(run(text, KILN))


# ======================================================================= "free" meaning available

CAMP = [fact("pitch", "A grass pitch at Brackenlow Camping costs £22 a night.", "£22 a night",
             subject={"kind": "service", "ref": "Grass pitch"}, fact_type="price", value=22, currency="GBP")]


@pytest.mark.parametrize("text", [
    "Brackenlow has three grass pitches free right now.",
    "We've got two riverside pitches free at the moment.",
    "A couple of tent pitches are free currently, so come on down.",
])
def test_free_meaning_available_is_not_an_offer(text):
    assert ("no_source", None) not in blocking(run(text, CAMP))


@pytest.mark.parametrize("text", [
    "Every booking gets a free bag of firewood right now.",
    "Book a pitch and get a free lantern hire.",
])
def test_free_thing_without_a_fact_still_blocks(text):
    assert ("no_source", None) in blocking(run(text, CAMP))


# ======================================================================= disclosures in other words

@pytest.mark.parametrize("disc,text,ok", [
    # not part of the price
    ("hard hat not included", "The site safety course is £95; the hard hat is bought separately.", True),
    ("hard hat not included", "The site safety course is £95 and you buy your hard hat separately.", True),
    ("hard hat not included", "The site safety course is £95; the hard hat is an additional purchase.", True),
    ("hard hat not included", "The site safety course is £95, with the hard hat paid for on top.", True),
    ("hard hat not included", "The site safety course is £95; the hard hat is not sold separately.", False),
    ("hard hat not included", "The site safety course is £95, hard hat included.", False),
    ("hard hat not included", "The site safety course is £95; gloves are bought separately.", False),
    # paid in advance
    ("paid in advance", "Six pottery sessions are £120 when you pay for them upfront.", True),
    ("paid in advance", "Six pottery sessions cost £120, all prepaid.", True),
    ("paid in advance", "Six pottery sessions are £120 with payment up front.", True),
    ("paid in advance", "Six pottery sessions are £120 if you book them upfront.", False),
    ("paid in advance", "Six pottery sessions are £120, pay on the day.", False),
    # a minimum stay / term said as the customer's commitment
    ("minimum 3-night stay", "Cabins are £90 a night when you stay at least three nights.", True),
    ("minimum 3-night stay", "Cabins are £90 a night if you stay 3 nights or more.", True),
    ("minimum 3-night stay", "Cabins are £90 a night (3-night minimum stay).", True),
    ("minimum 3-night stay", "Cabins are £90 a night if you stay 2 nights or more.", False),
    ("minimum 3-night stay", "Cabins are £90 a night; book at least 3 nights ahead.", False),
    ("6-month minimum term", "Studio membership is £40 a month; you sign up for six months at a time.", True),
    ("6-month minimum term", "Studio membership is £40 a month on a six-month sign-up.", True),
    ("6-month minimum term", "Studio membership is £40 a month when you commit to six months.", True),
    ("24-month minimum term", "The broadband deal is £28 a month and you sign up for two years.", True),
    ("6-month minimum term", "Studio membership is £40 a month, paid a month at a time.", False),
    ("6-month minimum term", "Studio membership is £40 a month on a 6-month contract.", False),
    ("6-month minimum term", "Studio membership is £40 a month, no 6-month sign-up.", False),
    ("6-month minimum term", "Studio membership is £40 a month; sign up for three months.", False),
    # a deadline time
    ("orders placed by 2pm", "Order before 14:00 and your parcel leaves today.", True),
    ("orders placed by 2pm", "Order by 2 p.m. and your parcel leaves today.", True),
    ("drawings approved by 12 noon", "Sign off the drawings by midday for a same-week build.", True),
    ("drawings approved by 12 noon", "Sign off your plans before 12pm for a same-week build.", True),
    ("orders placed by 2pm", "Order by 3pm and your parcel leaves today.", False),
    ("orders placed by 2pm", "Call us by 2pm and your parcel leaves today.", False),
    ("orders placed by 2pm", "Orders placed after 2pm leave tomorrow.", False),
    ("drawings approved by 12 noon", "Send the plans by midday for a same-week build.", False),
    ("drawings approved by 12 noon", "Drawings approved by 1pm go into a same-week build.", False),
])
def test_disclosure_said_in_other_words(disc, text, ok):
    assert evidence.disclosure_said(disc, text) is ok


def test_an_amount_before_pm_stays_per_month():
    assert "qpermonth" in evidence._dwords("Desk hire £30pm")
    assert "qat" not in evidence._dwords("Desk hire £30pm")


def test_minimum_stay_in_other_words_does_not_block_the_piece():
    f = fact("cabin", "Riverside cabins cost £90 a night, minimum 3-night stay.", "£90 a night", value=90,
             currency="GBP", fact_type="price", subject={"kind": "product", "ref": "Riverside cabin"},
             required_disclosures=["minimum 3-night stay"])
    assert not blocking(run("Riverside cabins: £90 a night when you stay three nights or longer.", [f]))
    assert ("missing_disclosure", "cabin") in blocking(run("Riverside cabins: £90 a night.", [f]))


# ======================================================================= a per-person price said for a group

WORKSHOP = [fact("wreath", "The wreath-making workshop costs £55 per person, materials included.", "£55 per person",
                 value=55, currency="GBP", fact_type="price", basis="per_person",
                 subject={"kind": "event", "ref": "Wreath-making workshop"})]


@pytest.mark.parametrize("text", [
    "Our wreath-making workshop is £55 for you and a friend.",
    "Wreath-making workshop: £55 for two.",
    "Bring your partner: the wreath workshop is £55 for the two of you.",
    "The wreath-making workshop is £55 for couples.",
])
def test_per_person_price_said_for_a_group_conflicts(text):
    assert ("conflict_or_expired", "wreath") in blocking(run(text, WORKSHOP))


@pytest.mark.parametrize("text", [
    "Our wreath-making workshop is £55 each for you and a friend.",
    "Our wreath-making workshop is £55 per person.",
    "The wreath-making workshop is £55 for two hours of making.",
    "Our wreath-making workshop is £55 for everyone who books.",
])
def test_per_person_price_said_per_person_matches(text):
    assert not blocking(run(text, WORKSHOP))


def test_a_fact_that_names_the_group_is_not_contradicted():
    f = fact("dinner", "Supper for two sharing costs £55 per person.", "£55 per person", value=55, currency="GBP",
             fact_type="price", basis="per_person", subject={"kind": "package", "ref": "Supper for two"})
    assert not blocking(run("Supper is £55 for two.", [f]))


# ======================================================================= "tomorrow" = "the next day"

LAUNDRY = [
    fact("next-day", "Shirts handed in before 10am are pressed and returned the next working day.",
         "returned the next working day", subject={"kind": "policy", "ref": "Next-day shirts"},
         fact_type="availability", required_disclosures=["handed in before 10am"]),
    fact("shirt-price", "A shirt press costs £2.80.", "£2.80", value=2.8, currency="GBP", fact_type="price",
         subject={"kind": "service", "ref": "Shirt press"}),
]


def test_tomorrow_uses_a_next_day_fact():
    assert ("missing_disclosure", "next-day") in blocking(run("Drop off today, returned tomorrow.", LAUNDRY))
    assert not blocking(run("Hand it in before 10am today, returned tomorrow.", LAUNDRY))


@pytest.mark.parametrize("text", [
    "See you tomorrow at the counter.",
    "Our shirt press is £2.80, and tomorrow we open at 8am.",
])
def test_tomorrow_alone_uses_nothing(text):
    assert ("missing_disclosure", "next-day") not in blocking(run(text, LAUNDRY))
