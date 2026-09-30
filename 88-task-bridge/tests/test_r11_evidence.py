"""Round 11 evidence rules (invented businesses, not the eval companies). Every rule has negative
controls.

- a price said on another basis than the fact's (contract basis): a flat / per-unit / per-room price
  said per head ("£240 per guest" for a per-boat hire), a per-seat / per-person / per-unit price said
  for a whole group ("£9 a month for your whole office", "£30 per household");
- another number in the same frame as a fact's own value, same unit ("up to 55 km per charge" against
  "up to 40 km per charge");
- a period said as never ending ("lifetime guarantee", "cover for life") against a fact that gives the
  same thing a set length;
- disclosures in other words: numbers past twelve in words ("forty" = "40"), a maximum said otherwise
  ("as many as" / "no more than" = "up to"), kids = children, each / every = per, not refundable
  ("we can't refund it" = "non-refundable"), VAT added ("VAT added on top" = "plus VAT"), a minimum
  length ("at least three hours long" = "minimum 3 hours"), a mode named without the word ("in sport"
  = "in sport mode");
- a certification claim backed by a fact whose own value names that very mark ("Blue Angel
  certified" for "Blue Angel recycled paper");
- a sentence about the past that speaks of no one here ("when every shop was shut on Sundays") only
  sets the scene: a number in it that differs from a fact is review, not a conflict.
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


# ======================================================================= a flat / per-unit price said per head

BOAT = [fact("boat-hire", "A day's hire of the Kestrel rowing boat costs £240 per boat (not per guest), up to six on board.",
             "£240 per boat", subject={"kind": "service", "ref": "Kestrel boat hire"}, fact_type="price",
             value=240, currency="GBP", basis="flat")]
ROOM = [fact("room-rate", "The Garden Room at Mossbank Inn costs £130 per room per night.", "£130 per room per night",
             subject={"kind": "package", "ref": "Garden Room"}, fact_type="price", value=130, currency="GBP",
             basis="per_room")]


@pytest.mark.parametrize("text,facts,key", [
    ("Hire the Kestrel for £240 per guest and spend the day on the lake.", BOAT, "boat-hire"),
    ("The Kestrel rowing boat: £240pp for a whole day.", BOAT, "boat-hire"),
    ("A day on the Kestrel is £240 a head.", BOAT, "boat-hire"),
    ("Stay in the Garden Room from £130 per person per night.", ROOM, "room-rate"),
    ("The Garden Room is £130 per night per adult.", ROOM, "room-rate"),
])
def test_flat_or_per_room_price_said_per_head_conflicts(text, facts, key):
    assert ("conflict_or_expired", key) in blocking(run(text, facts))


@pytest.mark.parametrize("text,facts", [
    ("Hire the Kestrel for £240 per boat, with room for six.", BOAT),
    ("The Kestrel costs £240 for the day, not per guest.", BOAT),
    ("The Kestrel is £240 in total, however many of you come along.", BOAT),
    ("The Garden Room is £130 per room per night.", ROOM),
    ("The Garden Room is £130 a night.", ROOM),
])
def test_price_said_on_its_own_basis_is_a_match(text, facts):
    assert not [b for b in blocking(run(text, facts)) if b[0] == "conflict_or_expired"]


def test_a_per_person_fact_said_per_head_is_not_contradicted():
    f = fact("tasting", "The gin tasting costs £35 per person.", "£35 per person", value=35, currency="GBP",
             fact_type="price", basis="per_person", subject={"kind": "service", "ref": "Gin tasting"})
    assert not blocking(run("Our gin tasting is £35 per guest.", [f]))


# ======================================================================= a per-seat / per-person price said for a group

SEATS = [fact("team-plan", "The Team plan of Rotaly costs £9 per user per month.", "£9 per user per month",
              subject={"kind": "plan", "ref": "Team"}, fact_type="price", value=9, currency="GBP", basis="per_seat")]
CLASS = [fact("pottery", "A pottery taster at Wheelhouse costs £28 per person.", "£28 per person",
              subject={"kind": "service", "ref": "Pottery taster"}, fact_type="price", value=28, currency="GBP",
              basis="per_person")]


@pytest.mark.parametrize("text,facts,key", [
    ("Rotaly Team is only £9 a month for your whole office.", SEATS, "team-plan"),
    ("Get the Team plan for £9 per company.", SEATS, "team-plan"),
    ("Team costs £9 per month for all of you.", SEATS, "team-plan"),
    ("Book a pottery taster for £28 for the entire family.", CLASS, "pottery"),
    ("Our pottery taster is £28 for your whole party.", CLASS, "pottery"),
])
def test_per_head_price_said_for_a_whole_group_conflicts(text, facts, key):
    assert ("conflict_or_expired", key) in blocking(run(text, facts))


@pytest.mark.parametrize("text,facts", [
    ("Rotaly Team is £9 per user per month for your whole office.", SEATS),
    ("Team costs £9 a month for each person on your rota.", SEATS),
    ("Our pottery taster is £28 per person, so bring the whole family.", CLASS),
    ("Pottery tasters are £28 each.", CLASS),
    ("Pottery tasters are £28 for everyone who books.", CLASS),
    ("Team is £9 a month for everybody on the rota.", SEATS),
])
def test_per_head_price_said_per_head_is_not_contradicted(text, facts):
    assert not [b for b in blocking(run(text, facts)) if b[0] == "conflict_or_expired"]


def test_a_fact_that_prices_the_whole_group_is_not_contradicted():
    f = fact("office-plan", "The Office plan is a flat £9 a month for the whole office.", "£9 per user per month",
             value=9, currency="GBP", fact_type="price", basis="per_seat", subject={"kind": "plan", "ref": "Office"})
    assert not blocking(run("The Office plan is £9 a month for your whole office.", [f]))


# ======================================================================= the same frame, another number

SCOOTER = [fact("range", "The Zippa S2 scooter goes up to 40 km per charge.", "up to 40 km per charge",
                subject={"kind": "product", "ref": "Zippa S2"}, fact_type="spec", attribute="range",
                value=40, unit="km"),
           fact("price", "The Zippa S2 costs £549.", "£549", subject={"kind": "product", "ref": "Zippa S2"},
                fact_type="price", value=549, currency="GBP")]


@pytest.mark.parametrize("text", [
    "It will take you up to 55 km per charge.",
    "Expect as much as 60 km on a single charge.",
])
def test_another_number_in_the_facts_frame_conflicts(text):
    assert ("conflict_or_expired", "range") in blocking(run(text, SCOOTER))


@pytest.mark.parametrize("text", [
    "It will take you up to 40 km per charge.",
    "We are 55 km from the city centre.",
    "Our test route was 12 km of hills.",
])
def test_other_numbers_with_the_same_unit_are_not_the_range(text):
    assert ("conflict_or_expired", "range") not in blocking(run(text, SCOOTER))


# ======================================================================= a period said as never ending

WARRANTY = [fact("guarantee", "Every Tapwell boiler install carries a 7-year guarantee on parts and labour.",
                 "7-year guarantee", subject={"kind": "policy", "ref": "Install guarantee"}, fact_type="policy",
                 attribute="guarantee", value=7, unit="year"),
            fact("cover", "Tapwell Home Cover protects your boiler for 12 months.", "12 months of cover",
                 subject={"kind": "plan", "ref": "Home Cover"}, fact_type="policy", attribute="cover",
                 value=12, unit="month")]


@pytest.mark.parametrize("text,key", [
    ("Every new boiler comes with a lifetime guarantee.", "guarantee"),
    ("Our installs carry a life-long guarantee on parts.", "guarantee"),
    ("With Home Cover your boiler has cover for life.", "cover"),
    ("Home Cover is cover that never expires.", "cover"),
])
def test_a_never_ending_period_conflicts_with_a_set_length(text, key):
    assert ("conflict_or_expired", key) in blocking(run(text, WARRANTY))


@pytest.mark.parametrize("text", [
    "A once in a lifetime chance to go green.",
    "Our engineers bring a lifetime of experience.",
    "Every install carries a 7-year guarantee on parts and labour.",
    "It is not a lifetime guarantee, but seven years is a long time.",
])
def test_no_never_ending_claim_about_the_facts_thing(text):
    assert not [b for b in blocking(run(text, WARRANTY)) if b[0] == "conflict_or_expired"]


def test_a_fact_that_says_lifetime_is_not_contradicted():
    f = fact("lt", "Tapwell radiators carry a lifetime guarantee (first 10 years parts and labour).",
             "lifetime guarantee", subject={"kind": "policy", "ref": "Radiator guarantee"}, fact_type="policy",
             attribute="guarantee", value=10, unit="year")
    assert not blocking(run("Our radiators come with a lifetime guarantee.", [f]))


# ======================================================================= disclosures in other words

def disc_fact(disclosure, text, value_text, value, **kw):
    return fact("f", text, value_text, value=value, currency="GBP", fact_type="price",
                subject={"kind": "service", "ref": kw.pop("ref", "Session")},
                required_disclosures=[disclosure], **kw)


@pytest.mark.parametrize("disclosure,text", [
    ("up to 40 guests", "Lakeview hire is £300 for the evening, with room for as many as forty guests."),
    ("up to 40 guests", "Lakeview hire is £300 for the evening, for no more than forty guests."),
    ("up to 12 children", "Soft-play parties cost £150, for up to twelve kids."),
    ("up to 25 people", "The barn is £400 a day and takes a maximum of twenty-five people."),
    ("non-refundable", "A £20 deposit holds your place, which we can't refund."),
    ("non-refundable", "Pay £20 now to hold your place; it won't be refunded if you cancel."),
    ("non-refundable", "Deposits of £20 are not refundable."),
    ("plus VAT", "Desk hire is £15 a day, with VAT added on top."),
    ("plus VAT", "Desk hire is £15 a day and we add VAT."),
    ("minimum 3 hours per booking", "Studio time is £45 an hour, and each booking is at least three hours long."),
    ("minimum 3 hours per booking", "Studio time is £45 an hour; every booking lasts at least 3 hours."),
    ("in sport mode", "Riding in sport, the Zippa reaches 25 mph."),
])
def test_disclosure_said_in_other_words(disclosure, text):
    assert evidence.disclosure_said(disclosure, text)


@pytest.mark.parametrize("disclosure,text", [
    ("non-refundable", "A £20 deposit holds your place and is fully refundable."),
    ("non-refundable", "A £20 deposit holds your place; refunds are quick."),
    ("plus VAT", "Desk hire is £15 a day, with no VAT added."),
    ("plus VAT", "Desk hire is £15 a day and we don't add VAT."),
    ("minimum 3 hours per booking", "Studio time is £45 an hour; book at least 3 hours ahead."),
    ("up to 40 guests", "Lakeview hire is £300 for the evening, with room for at least forty guests."),
    ("up to 40 guests", "Lakeview hire is £300 for the evening, for as many as fifty guests."),
    ("in sport mode", "Our sport-tuned scooter reaches 25 mph."),
    ("in sport mode", "Sport fans love it: 25 mph."),
])
def test_disclosure_not_said(disclosure, text):
    assert not evidence.disclosure_said(disclosure, text)


def test_missing_disclosure_cleared_by_other_words_end_to_end():
    f = disc_fact("non-refundable", "A £20 deposit secures your class place; it is non-refundable.",
                  "£20 deposit", 20, ref="Class deposit")
    assert not blocking(run("To hold your place we take a £20 deposit, which we can't refund.", [f]))
    assert ("missing_disclosure", "f") in blocking(run("To hold your place we take a £20 deposit.", [f]))


# ======================================================================= a mark named by the fact's own value

PAPER = [fact("paper", "Our notebooks are made from Blue Angel recycled paper.", "Blue Angel recycled paper",
              subject={"kind": "product", "ref": "Notebooks"}, fact_type="spec", attribute="paper")]


def test_mark_named_in_the_facts_value_supports_a_certified_claim():
    assert not blocking(run("The paper we use is Blue Angel certified.", PAPER))


@pytest.mark.parametrize("text", [
    "The paper we use is Nordic Swan certified.",
    "The paper we use is FSC certified.",
])
def test_other_marks_still_need_a_fact(text):
    assert ("no_source", None) in blocking(run(text, PAPER))


def test_a_value_that_denies_the_mark_does_not_support_it():
    f = fact("paper2", "Our notebooks use recycled paper; it is not Blue Angel paper.", "recycled paper, not Blue Angel",
             subject={"kind": "product", "ref": "Notebooks"}, fact_type="spec", attribute="paper")
    assert ("no_source", None) in blocking(run("The paper we use is Blue Angel certified.", [f]))


def test_the_business_name_is_not_a_mark():
    facts = [fact("biz", "Inkwell Press prints notebooks in Leeds.", "Inkwell Press notebooks",
                  subject={"kind": "business", "ref": "Inkwell Press"}, fact_type="claim")]
    assert ("no_source", None) in blocking(run("Every notebook is Inkwell certified.", facts))


# ======================================================================= a past scene, not a claim

SHOP = [fact("hours", "Corner Crumb bakery on Mill Street is open seven days a week, 7am to 4pm.",
             "open seven days a week", subject={"kind": "business", "ref": "Mill Street bakery"},
             fact_type="hours", attribute="opening days")]


@pytest.mark.parametrize("text", [
    "Bread tasted better when every bakery on the street was open six days a week.",
    "Back when Mill Street bakeries used to close on Sundays, bread was baked six days a week.",
])
def test_a_past_scene_is_review_not_a_conflict(text):
    assert not blocking(run(text, SHOP))


@pytest.mark.parametrize("text", [
    "The Mill Street bakery was open six days a week.",
    "We were open six days a week at Mill Street bakery.",
    "Our street bakery opening covers six days a week.",
])
def test_a_claim_about_the_business_still_conflicts(text):
    assert ("conflict_or_expired", "hours") in blocking(run(text, SHOP))
