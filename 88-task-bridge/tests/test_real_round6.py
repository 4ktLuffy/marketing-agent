"""Real-data round 6 (a brewery's price history, a tented camp's room rates): clusters fixed IN GENERAL.
Invented businesses and wording (Reedbank Brewing, Dune Camp), every rule with negative controls.

a. an expired price stated truthfully in a PAST frame ("was X until <end date>", "up to <date> ... cost X",
   "moved from X to Y on <date>", "used to be X") is a match; the same old price said as current, or with a
   date that is not the fact's end / its successor's start, still conflicts; the past date is not "before
   the publish date";
b. the unit said after the price ("1,200 birr a crate", "each crate", "$56 a night for the whole tent") is
   the disclosure, and "breakfast comes with it" = breakfast included;
c. an expired offer is not named by a kind noun alone ("safari package" for the valid "Omo Valley safari");
d. a price change said in percent is checked against the old and new price facts.
"""
from datetime import date

import pytest

from app import evidence

from .data import fact

DAY = date(2026, 11, 10)


def beer(key, ref, value, valid_from=None, valid_to=None, status="active"):
    return fact(key, f"{ref}: {value:,} birr per crate.", f"{value:,} birr per crate",
                subject={"kind": "product", "ref": ref}, fact_type="price", attribute="crate price", value=value,
                currency="ETB", unit="crate", basis="per_unit", required_disclosures=["per crate"],
                valid_from=valid_from, valid_to=valid_to, status=status, sites=["main"])


BREWERY = [
    beer("amber-33", "Amber Ale 33cl", 1200, valid_from="2026-09-01"),
    beer("amber-33-old", "Amber Ale 33cl", 1050, valid_to="2026-08-31", status="expired"),
    beer("stout-50", "Stout 50cl", 1800, valid_from="2026-09-01"),
    beer("stout-50-old", "Stout 50cl", 1650, valid_to="2026-08-31", status="expired"),
    fact("camp-site", "Dune Camp is a tented camp by the dunes.", None,
         subject={"kind": "site", "ref": "Dune Camp"}, sites=["main"]),
    fact("tent-rate", "Canvas Tent: $56 per room per night, breakfast included.", "$56 per room per night",
         subject={"kind": "variant", "ref": "Canvas Tent"}, fact_type="price", attribute="rate", value=56,
         currency="USD", unit="night", basis="per_room", sites=["main"],
         required_disclosures=["per room per night", "breakfast included"]),
    fact("dune-trek", "Dune Trek (2 nights/3 days) with overnights at Dune Camp.", "2 nights/3 days",
         subject={"kind": "package", "ref": "Dune Trek (2 nights/3 days)"}, fact_type="inclusion", value=3,
         unit="days", sites=["main"]),
    fact("trek-from", "Trek packages from $300 per person.", "from $300 per person",
         subject={"kind": "offer", "ref": "Trek packages"}, fact_type="price", attribute="from", value=300,
         currency="USD", basis="per_person", required_disclosures=["per person"], status="expired",
         valid_to="2026-10-01", sites=["main"]),
]
SCOPE = {"sites": ["main"]}


def run(text, facts=None, scope=None):
    return evidence.check_text(text, facts or BREWERY, DAY, scope or SCOPE, None)[0]


def blocking(text, **kw):
    return [x for x in run(text, **kw) if x["blocking"]]


# ---- a. a past price, truthfully dated

@pytest.mark.parametrize("text", [
    "Amber Ale 33cl was 1,050 birr per crate until 31 August 2026 and is now 1,200 birr per crate.",
    "Amber Ale 33cl is now 1,200 birr per crate (was 1,050 until 31 Aug 2026).",
    "Up to 31 August 2026 a crate of Amber Ale 33cl cost 1,050 birr; since 1 September it costs 1,200 birr per crate.",
    "Stout 50cl moved from 1,650 birr to 1,800 birr for each crate on 1 September 2026.",
    "Stout 50cl was 1,650 birr per crate up to 31 August 2026.",
    "Amber Ale 33cl used to be 1,050 birr per crate before the September change.",
    "Stout 50cl: 1,800 birr per crate, up from 1,650 birr on 1 September.",
])
def test_a_a_past_price_truthfully_dated_is_not_a_conflict(text):
    assert blocking(text) == []


def test_a_the_past_price_is_a_match_with_its_expired_fact():
    got = run("Amber Ale 33cl was 1,050 birr per crate until 31 August 2026.")
    assert any(x["label"] == "match" and x["fact_key"] == "amber-33-old" and "past price" in x["detail"] for x in got)


@pytest.mark.parametrize("text", [
    "Amber Ale 33cl is 1,050 birr per crate this week.",
    "Amber Ale 33cl is still 1,050 birr per crate.",
    "Amber Ale 33cl costs 1,050 birr per crate right now.",
    "Amber Ale 33cl was 1,050 birr per crate until 30 October 2026.",
    "Stout 50cl moved from 1,650 birr to 1,800 birr for each crate on 15 October 2026.",
    "Amber Ale 33cl was 1,050 birr per crate until 31 August 2026 and still is.",
])
def test_a_an_old_price_said_as_current_or_wrongly_dated_still_conflicts(text):
    assert any(x["label"] == "conflict_or_expired" for x in blocking(text))


def test_a_a_wrong_old_value_in_a_past_frame_is_not_excused():
    assert any(x["label"] == "no_source" or x["label"] == "conflict_or_expired"
               for x in blocking("Amber Ale 33cl was 1,111 birr per crate until 31 August 2026."))


# ---- b. the unit after the price

@pytest.mark.parametrize("text", [
    "Amber Ale 33cl costs 1,200 birr a crate.",
    "Amber Ale 33cl is 1,200 birr for each crate.",
    "Amber Ale 33cl is 1,200 birr each crate.",
])
def test_b_the_unit_said_after_the_price_is_the_disclosure(text):
    assert [x for x in blocking(text) if x["label"] == "missing_disclosure"] == []


def test_b_a_price_with_no_unit_still_needs_it():
    assert any(x["label"] == "missing_disclosure" for x in blocking("Amber Ale 33cl costs 1,200 birr."))


@pytest.mark.parametrize("text", [
    "The Canvas Tent holds a pair at $56 a night for the whole tent, and breakfast comes with it.",
    "The Canvas Tent is $56 a night for the whole tent and comes with breakfast.",
    "The Canvas Tent is $56 per room per night and breakfast is part of the rate.",
])
def test_b_a_room_rate_and_breakfast_said_in_other_words(text):
    assert blocking(text) == []


def test_b_a_room_rate_with_no_unit_still_needs_it():
    assert any(x["label"] == "missing_disclosure" for x in blocking("The Canvas Tent is $56, breakfast is lovely."))


def test_b_another_basis_is_still_a_conflict():
    assert blocking("The Canvas Tent is $56 a night per person and breakfast comes with it.")


# ---- c. a kind noun does not name the expired offer

def test_c_a_package_named_by_its_own_name_is_not_the_expired_offer():
    assert blocking("Our Dune Trek package runs 2 nights/3 days, with overnights at Dune Camp.") == []


def test_c_the_expired_offer_named_for_real_still_conflicts():
    assert any(x["label"] == "conflict_or_expired" for x in blocking("Our trek packages start from $300 per person."))


# ---- d. a percent change against the price facts

def test_d_a_right_percentage_change_is_a_match():
    assert blocking("Amber Ale 33cl is up 14% since August.") == []
    assert blocking("Amber Ale 33cl is up 14% from 1,050 birr per crate to 1,200 birr per crate.") == []


@pytest.mark.parametrize("text", [
    "Amber Ale 33cl is up 25% since August.",
    "Amber Ale 33cl is up 25% from 1,050 birr per crate to 1,200 birr per crate.",
    "Amber Ale 33cl is down 14% since August.",
])
def test_d_a_wrong_percentage_change_conflicts_with_the_current_price(text):
    got = [x for x in blocking(text) if x["label"] == "conflict_or_expired" and x["fact_key"] == "amber-33"]
    assert got


def test_a_a_bare_was_with_no_date_is_not_enough():
    assert any(x["label"] == "conflict_or_expired" for x in blocking("Amber Ale 33cl was 1,050 birr per crate."))
